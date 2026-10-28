"""Deterministic limits for one shared follow-up scheduler tick."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from zeline import config, followups, tasks


class FollowupBudgetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        for attribute, value in (
            ("DATA_DIR", self.home),
            ("GATEWAYS", {"telegram": {"owner_identity": "12345"}}),
        ):
            override = patch.object(config, attribute, value)
            override.start()
            self.addCleanup(override.stop)
        self.owner = "telegram:12345"

    def test_distinct_email_filters_share_one_inbox_fetch_per_poll(self):
        watches = []
        for index in range(2):
            title = f"Campaign {index}"
            tasks.update(self.owner, title, "completed")
            watches.append({
                "task": title, "kind": "email", "source": "inbox",
                "field": f"sender{index}@example.com|Campaign {index}",
                "baseline": [], "expires_at": time.time() + 3600,
            })
        fixture = type("Result", (), {"returncode": 0, "stdout": json.dumps([])})()
        with patch.object(followups, "_load", return_value={"watches": watches}), \
             patch.object(followups.subprocess, "run", return_value=fixture) as fetch:
            self.assertEqual(followups.poll(), "")
        self.assertEqual(fetch.call_count, 1, "one mailbox snapshot should serve all task filters")

    def test_invalid_mailbox_snapshot_is_not_refetched_for_each_filter(self):
        watches = []
        for index in range(3):
            title = f"Mail watch {index}"
            tasks.update(self.owner, title, "completed")
            watches.append({"task": title, "kind": "email", "source": "inbox",
                            "field": f"sender{index}@example.com|Campaign {index}",
                            "baseline": [], "expires_at": time.time() + 3600})
        fixture = type("Result", (), {"returncode": 0,
                                      "stdout": '{"envelopes": {}}'})()
        with patch.object(followups, "_load", return_value={"watches": watches}), \
             patch.object(followups.subprocess, "run", return_value=fixture) as fetch:
            self.assertEqual(followups.poll(), "")
        self.assertEqual(fetch.call_count, 1)

    def test_simultaneous_events_leave_bounded_delivery_backlog_retryable(self):
        watches = []
        for index in range(3):
            title = f"Reward {index}"
            tasks.update(self.owner, title, "completed")
            snapshot = self.home / f"balance-{index}.json"
            snapshot.write_text('{"balance": 2}', encoding="utf-8")
            watches.append({"task": title, "kind": "json_file", "source": str(snapshot),
                            "field": "balance", "baseline": 1, "expires_at": time.time() + 3600})
        followups._save({"watches": watches})
        with patch.object(followups.scheduler, "_deliver_telegram", return_value=(True, "ok")) as send:
            followups.poll(deliver=True)
            self.assertLessEqual(send.call_count, 2)
            self.assertEqual(sum(item["baseline"] == 1 for item in followups._load()["watches"]), 1)
            followups.poll(deliver=True)
            self.assertEqual(send.call_count, 3)
            self.assertTrue(all(item["baseline"] == 2 for item in followups._load()["watches"]))

    def test_failed_early_delivery_does_not_starve_later_watch(self):
        watches = []
        for index in range(3):
            title = f"Reward {index}"
            tasks.update(self.owner, title, "completed")
            snapshot = self.home / f"balance-{index}.json"
            snapshot.write_text('{"balance": 2}', encoding="utf-8")
            watches.append({"task": title, "kind": "json_file", "source": str(snapshot),
                            "field": "balance", "baseline": 1, "expires_at": time.time() + 3600})
        followups._save({"watches": watches})
        seen = []
        def deliver(_owner, message, **_kwargs):
            seen.append(message)
            return ("Reward 2" in message, "synthetic")
        with patch.object(followups.scheduler, "_deliver_telegram", side_effect=deliver):
            followups.poll(deliver=True)
            followups.poll(deliver=True)
        self.assertTrue(any("Reward 2" in item for item in seen))
        self.assertEqual(next(w["baseline"] for w in followups._load()["watches"]
                              if w["task"] == "Reward 2"), 2)

    def test_twenty_email_watches_share_each_snapshot_and_drain_bounded_backlog(self):
        watches = []
        envelopes = []
        for index in range(20):
            title = f"Mail watch {index}"
            tasks.update(self.owner, title, "completed")
            watches.append({"task": title, "kind": "email", "source": "inbox",
                            "field": f"sender{index}@example.com|Campaign {index}",
                            "baseline": [], "expires_at": time.time() + 3600})
            envelopes.append({"id": f"message-{index}",
                              "from": {"addr": f"sender{index}@example.com"},
                              "subject": f"Campaign {index}"})
        followups._save({"watches": watches})
        fixture = type("Result", (), {"returncode": 0,
                                      "stdout": json.dumps(envelopes)})()
        with patch.object(followups.subprocess, "run", return_value=fixture) as fetch, \
             patch.object(followups.scheduler, "_deliver_telegram", return_value=(True, "synthetic")) as send:
            for tick in range(10):
                followups.poll(deliver=True)
                self.assertEqual(send.call_count, 2 * (tick + 1))
                self.assertEqual(fetch.call_count, tick + 1)
                self.assertEqual(len(followups._load()["watches"]), 20)
        self.assertTrue(all(item["baseline"] for item in followups._load()["watches"]))

    def test_poll_fails_fast_on_registry_lock_contention(self):
        real_connect = followups.sqlite3.connect
        lock_waits = []

        def connect(path, *args, **kwargs):
            if Path(path).name == "owner-lock.sqlite3":
                lock_waits.append(kwargs.get("timeout"))
            return real_connect(path, *args, **kwargs)

        with patch.object(followups.sqlite3, "connect", side_effect=connect):
            self.assertEqual(followups.poll(), "")
        self.assertEqual(len(lock_waits), 1)
        self.assertLessEqual(lock_waits[0], 5)

    def test_followup_delivery_has_a_bounded_request_timeout(self):
        title = "Campaign reward"
        tasks.update(self.owner, title, "completed")
        balance = self.home / "balance.json"
        balance.write_text('{"balance": 2}', encoding="utf-8")
        watch = {"task": title, "kind": "json_file", "source": str(balance),
                 "field": "balance", "baseline": 1, "expires_at": time.time() + 3600}
        with patch.object(followups, "_load", return_value={"watches": [watch]}), \
             patch.object(followups, "_save"), \
             patch.object(followups.scheduler, "_deliver_telegram", return_value=(True, "ok")) as send:
            self.assertIn("1 → 2", followups.poll(deliver=True))
        self.assertLessEqual(send.call_args.kwargs["timeout"], 8)
        self.assertEqual(send.call_args.kwargs["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
