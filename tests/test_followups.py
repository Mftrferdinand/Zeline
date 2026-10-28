"""Task-scoped proactive watches: baseline, dedup and bounded delivery."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zeline import config, tasks
from zeline import followups


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.data_patch = patch.object(config, "DATA_DIR", self.home)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.owner = "telegram:12345"
        self.owner_patch = patch.object(config, "GATEWAYS", {"telegram": {"owner_identity": "12345"}})
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        tasks.update(self.owner, "Track reward for campaign", "completed")
        self.balance = self.home / "balance.json"
        self.balance.write_text('{"balance": 10}')

    def register(self):
        with patch.object(followups.scheduler, "add_job") as add:
            add.return_value.id = "job7"
            return followups.register(self.owner, "Track reward for campaign", "json_file",
                                      str(self.balance), "balance")

    def test_numeric_baseline_dedup_increase_and_decrease(self):
        result = self.register()
        self.assertIn("job7", result)
        self.assertEqual(followups.poll(), "")
        self.balance.write_text('{"balance": 14}')
        self.assertIn("10 → 14", followups.poll())
        self.assertEqual(followups.poll(), "")
        self.balance.write_text('{"balance": 12}')
        self.assertEqual(followups.poll(), "")  # credits, not withdrawals
        self.balance.write_text('{"balance": 16}')
        self.assertIn("12 → 16", followups.poll())

    def test_registration_is_task_scoped_owner_only(self):
        with self.assertRaises(ValueError):
            followups.register("telegram:other", "Track reward for campaign", "json_file", str(self.balance), "balance")
        with self.assertRaises(ValueError):
            followups.register(self.owner, "Unrelated task", "json_file", str(self.balance), "balance")
        with self.assertRaises(ValueError):
            followups.register(self.owner, "Track reward for campaign", "json_file", str(self.balance), "secret")
        self.assertFalse(followups.registry_path().exists())

    def test_dedup_scheduler_and_cancel_watch(self):
        with patch.object(followups.scheduler, "list_jobs", return_value=[]), patch.object(followups.scheduler, "add_job") as add:
            add.return_value.id = "job7"
            followups.register(self.owner, "Track reward for campaign", "json_file", str(self.balance), "balance")
            followups.register(self.owner, "Track reward for campaign", "json_file", str(self.balance), "balance")
            self.assertEqual(add.call_count, 1)
        self.assertEqual(len(followups.list_watches(self.owner).splitlines()), 1)
        followups.stop(self.owner, "Track reward for campaign")
        self.balance.write_text('{"balance": 20}')
        self.assertEqual(followups.poll(), "")

    def test_source_failure_preserves_baseline(self):
        self.register()
        self.balance.unlink()
        self.assertEqual(followups.poll(), "")
        self.balance.write_text('{"balance": 18}')
        self.assertIn("10 → 18", followups.poll())

    def test_registry_save_failure_removes_new_scheduler_job(self):
        with patch.object(followups, "_save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                followups.register(self.owner, "Track reward for campaign", "json_file",
                                   str(self.balance), "balance")
        self.assertEqual(followups.scheduler.list_jobs(), [])
        self.assertFalse(followups.registry_path().exists())

    def test_disabled_scheduler_rejects_registration_without_state(self):
        with patch.object(config, "CRON", False):
            with self.assertRaisesRegex(ValueError, "scheduler.*disabled"):
                followups.register(self.owner, "Track reward for campaign", "json_file",
                                   str(self.balance), "balance")
        self.assertEqual(followups.scheduler.list_jobs(), [])
        self.assertFalse(followups.registry_path().exists())

    def test_email_failure_does_not_create_watch(self):
        with patch.object(followups, "_read_email", side_effect=ValueError("mail unavailable")):
            with self.assertRaisesRegex(ValueError, "mail unavailable"):
                followups.register(self.owner, "Track reward for campaign", "email", "inbox", "sender@example.com|Campaign update")
        self.assertFalse(followups.registry_path().exists())

    def test_email_requires_exact_task_sender_and_subject(self):
        with self.assertRaisesRegex(ValueError, "sender.*subject"):
            followups.register(self.owner, "Track reward for campaign", "email", "inbox", "")
        with self.assertRaisesRegex(ValueError, "sender.*subject"):
            followups.register(self.owner, "Track reward for campaign", "email", "inbox", "sender@example.com|")
        entries = [
            {"id": "task-a", "from": {"addr": "sender@example.com"}, "subject": "Re: Campaign update"},
            {"id": "unrelated-sender", "from": {"addr": "other@example.com"}, "subject": "Campaign update"},
            {"id": "unrelated-subject", "from": {"addr": "sender@example.com"}, "subject": "Different campaign"},
        ]
        with patch.object(followups.subprocess, "run", return_value=type("Result", (), {
            "returncode": 0, "stdout": json.dumps(entries),
        })()):
            self.assertEqual(followups._read_email("sender@example.com|Campaign update"), {"task-a"})

    def test_file_must_be_small_json_number_and_no_arbitrary_path(self):
        outside = self.home.parent / "not-owned-file.json"
        with self.assertRaises(ValueError):
            followups.register(self.owner, "Track reward for campaign", "json_file", str(outside), "balance")
        self.balance.write_text('{"balance": "not a number"}')
        with self.assertRaises(ValueError):
            followups.register(self.owner, "Track reward for campaign", "json_file", str(self.balance), "balance")

    def test_source_failure_alerts_once_and_recovers(self):
        self.register()
        self.balance.unlink()
        with patch.object(followups.scheduler, "_deliver_telegram", return_value=(True, "ok")) as send:
            self.assertEqual(followups.poll(deliver=True), "")
            self.assertEqual(followups.poll(deliver=True), "")
            self.assertEqual(send.call_count, 1)
            self.assertIn("Monitoring is degraded", send.call_args.args[1])
            self.balance.write_text('{"balance": 18}')
            self.assertIn("10 → 18", followups.poll(deliver=True))
            self.assertEqual(send.call_count, 2)

    def test_real_scheduler_job_is_quiet_script_for_owner(self):
        result = followups.register(self.owner, "Track reward for campaign", "json_file",
                                    str(self.balance), "balance")
        self.assertIn("Monitoring", result)
        jobs = followups.scheduler.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].deliver, self.owner)
        self.assertTrue(jobs[0].no_agent)
        self.assertTrue(jobs[0].script.endswith("followups.py"))
        self.assertEqual(followups.poll(), "")
        self.assertIn("Stopped", followups.stop(self.owner, "Track reward for campaign"))
        self.assertEqual(followups.scheduler.list_jobs(), [])

    def test_delivery_failure_retries_and_success_deduplicates(self):
        self.register()
        self.balance.write_text('{"balance": 20}')
        with patch.object(followups.scheduler, "_deliver_telegram", return_value=(False, "offline")) as send:
            self.assertEqual(followups.poll(deliver=True), "")
            self.assertEqual(send.call_count, 1)
        with patch.object(followups.scheduler, "_deliver_telegram", return_value=(True, "ok")) as send:
            self.assertIn("10 → 20", followups.poll(deliver=True))
            self.assertEqual(followups.poll(deliver=True), "")
            self.assertEqual(send.call_count, 1)

    def test_cancelled_task_removes_monitor(self):
        self.register()
        tasks.update(self.owner, "Track reward for campaign", "cancelled")
        self.balance.write_text('{"balance": 20}')
        self.assertEqual(followups.poll(), "")
        self.assertEqual(followups.list_watches(self.owner), "No active task follow-up watches.")

    def test_atomic_record_is_private_and_email_ids_not_rendered(self):
        with patch.object(followups, "_read_email", return_value={"a", "b"}), patch.object(followups.scheduler, "add_job") as add:
            add.return_value.id = "job8"
            followups.register(self.owner, "Track reward for campaign", "email", "inbox", "sender@example.com|Campaign update")
            if os.name == "posix":
                self.assertEqual(followups.registry_path().stat().st_mode & 0o777, 0o600)
            with patch.object(followups, "_read_email", return_value={"a", "b", "c"}):
                self.assertIn("1 new email", followups.poll())
                self.assertEqual(followups.poll(), "")
            self.assertNotIn("new email(s)", followups.list_watches(self.owner))


if __name__ == "__main__":
    unittest.main()
