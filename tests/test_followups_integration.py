"""Offline acceptance test for the real persisted follow-up scheduler lifecycle.

The only substituted boundary is Telegram delivery in the scheduler's child Python
process. A fake receipt is *not* evidence of Bot API or client delivery.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from zeline import config, followups, scheduler, tasks


class FollowupSchedulerLifecycleTests(unittest.TestCase):
    TASK = "Track sandbox reward"
    IDENTITY = "telegram:12345"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="followups-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.data = self.root / "zeline-data"
        self.data.mkdir()
        self.child_python = self.root / "child-python"
        self.child_python.mkdir()
        self.receipts = self.root / "fake-delivery.jsonl"
        self.fail_marker = self.root / "fail-send"
        self.env = patch.dict(os.environ, {
            "HOME": str(self.home), "ZELINE_HOME": str(self.data),
            "ZELINE_TEST_RECEIPTS": str(self.receipts),
            "ZELINE_TEST_FAIL_SEND": str(self.fail_marker),
            "PYTHONPATH": os.pathsep.join((str(self.child_python), str(Path(__file__).resolve().parents[1]))),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.data_patch = patch.object(config, "DATA_DIR", self.data)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.owner_patch = patch.object(config, "GATEWAYS", {
            "telegram": {"owner_identity": "12345", "token": "offline-test-only"},
        })
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        self.cron_patch = patch.object(config, "CRON", True)
        self.cron_patch.start()
        self.addCleanup(self.cron_patch.stop)
        # The child loads configuration afresh; none of its state or credentials
        # comes from the operator's live ~/.zeline or gateway.
        (self.data / "config.json").write_text(json.dumps({
            "gateways": {"telegram": {"owner_identity": "12345", "token": "offline-test-only"}},
            "tools": {"cron": True},
        }), encoding="utf-8")
        (self.child_python / "sitecustomize.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "from zeline import scheduler\n"
            "def fake_telegram(chat_id, text, **_kwargs):\n"
            "    if Path(os.environ['ZELINE_TEST_FAIL_SEND']).exists():\n"
            "        return False, 'offline synthetic failure'\n"
            "    with open(os.environ['ZELINE_TEST_RECEIPTS'], 'a', encoding='utf-8') as stream:\n"
            "        stream.write(__import__('json').dumps({'chat_id': chat_id, 'text': text}) + '\\n')\n"
            "    return True, 'synthetic receipt only'\n"
            "scheduler._deliver_telegram = fake_telegram\n",
            encoding="utf-8",
        )
        tasks.update(self.IDENTITY, self.TASK, "completed")
        self.snapshot = self.data / "balance.json"
        self.snapshot.write_text('{"balance": 10}', encoding="utf-8")

    def _receipts(self):
        if not self.receipts.exists():
            return []
        return [json.loads(line) for line in self.receipts.read_text(encoding="utf-8").splitlines()]

    def _tick(self, job_id, expected_runs):
        self.assertTrue(scheduler.run_now(job_id))
        runner = scheduler.Scheduler(sessions=None)
        try:
            self.assertEqual(runner.tick(), [job_id])
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                job = scheduler.find_job(job_id)
                if job is not None and job.runs == expected_runs and not runner.busy:
                    self.assertEqual(runner.last_error, "")
                    self.assertEqual(job.last_status, "ok: silent")
                    self.assertEqual(job.failures, 0)
                    return
                time.sleep(0.02)
            self.fail(f"job did not finish run {expected_runs}: {scheduler.find_job(job_id)!r}; error={runner.last_error!r}")
        finally:
            runner.stop(timeout=5)

    def test_persisted_job_ticks_retries_deduplicates_and_stop_cleans_both(self):
        result = followups.register(self.IDENTITY, self.TASK, "json_file", str(self.snapshot), "balance")
        self.assertIn("Monitoring", result)
        self.assertTrue(followups.registry_path().is_relative_to(self.data))
        self.assertTrue(scheduler.jobs_path().is_relative_to(self.data))
        self.assertEqual(len(json.loads(followups.registry_path().read_text())["watches"]), 1)
        jobs_on_disk = json.loads(scheduler.jobs_path().read_text(encoding="utf-8"))
        self.assertEqual(len(jobs_on_disk), 1)
        job_id = jobs_on_disk[0]["id"]
        self.assertEqual(jobs_on_disk[0]["deliver"], self.IDENTITY)
        self.assertTrue(jobs_on_disk[0]["no_agent"])
        self.assertEqual(Path(jobs_on_disk[0]["script"]), Path(followups.__file__).resolve())
        self.assertEqual(scheduler.find_job(job_id).runs, 0)

        # Tick the actual Scheduler: it launches followups.py in a real child
        # process with isolated HOME/ZELINE_HOME and no real Bot API access.
        self._tick(job_id, 1)
        self.assertEqual(self._receipts(), [])
        self.snapshot.write_text('{"balance": 20}', encoding="utf-8")
        self.fail_marker.touch()
        self._tick(job_id, 2)
        self.assertEqual(self._receipts(), [])
        self.assertEqual(json.loads(followups.registry_path().read_text())["watches"][0]["baseline"], 10)

        self.fail_marker.unlink()
        self._tick(job_id, 3)
        self.assertEqual(self._receipts(), [{
            "chat_id": "12345", "text": f"💰 Follow-up: {self.TASK} — balance 10 → 20.",
        }])
        self.assertEqual(json.loads(followups.registry_path().read_text())["watches"][0]["baseline"], 20)
        self._tick(job_id, 4)
        self.assertEqual(len(self._receipts()), 1)
        self.assertEqual(len(json.loads(scheduler.jobs_path().read_text())), 1)

        self.assertIn("Stopped", followups.stop(self.IDENTITY, self.TASK))
        self.assertEqual(json.loads(followups.registry_path().read_text())["watches"], [])
        self.assertEqual(json.loads(scheduler.jobs_path().read_text()), [])
        self.assertIsNone(scheduler.find_job(job_id))
        self.snapshot.write_text('{"balance": 30}', encoding="utf-8")
        self.assertEqual(scheduler.Scheduler(sessions=None).tick(), [])
        self.assertEqual(len(self._receipts()), 1)

    def test_cancelled_task_is_pruned_by_real_tick_without_delivery(self):
        followups.register(self.IDENTITY, self.TASK, "json_file", str(self.snapshot), "balance")
        job_id = scheduler.list_jobs()[0].id
        tasks.update(self.IDENTITY, self.TASK, "cancelled")
        self.snapshot.write_text('{"balance": 20}', encoding="utf-8")
        # poll() removes the now-unauthorized watch and the associated job;
        # scheduler completion must not resurrect the deleted job metadata.
        self.assertTrue(scheduler.run_now(job_id))
        runner = scheduler.Scheduler(sessions=None)
        try:
            self.assertEqual(runner.tick(), [job_id])
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and (runner.busy or scheduler.find_job(job_id)):
                time.sleep(0.02)
            self.assertEqual(runner.last_error, "")
            self.assertIsNone(scheduler.find_job(job_id))
        finally:
            runner.stop(timeout=5)
        self.assertEqual(json.loads(followups.registry_path().read_text())["watches"], [])
        self.assertEqual(json.loads(scheduler.jobs_path().read_text()), [])
        self.assertEqual(self._receipts(), [])


if __name__ == "__main__":
    unittest.main()