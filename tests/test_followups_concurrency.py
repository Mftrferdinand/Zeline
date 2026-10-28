"""Deterministic registry interleavings, without network or forked threads."""
import json
import multiprocessing
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from zeline import config, followups, tasks

OWNER = "telegram:12345"
FIRST = "Track first reward"
SECOND = "Track second reward"


def _register_child(data, entered, release, results):
    """Spawn target: fresh interpreter, isolated configuration and data."""
    from zeline import config, followups, tasks

    config.DATA_DIR = Path(data)
    config.GATEWAYS = {"telegram": {"owner_identity": "12345"}}
    config.CRON = True
    tasks.update(OWNER, FIRST, "completed")
    original = followups._save

    def paused(value):
        entered.set()
        if not release.wait(8):
            raise TimeoutError("parent did not release child")
        return original(value)

    try:
        with patch.object(followups, "_save", side_effect=paused):
            followups.register(OWNER, FIRST, "json_file", str(Path(data) / "balance.json"), "balance")
        results.put(None)
    except BaseException as exc:
        results.put(repr(exc))


class FollowupConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="followups-race-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.snapshot = self.data / "balance.json"
        self.snapshot.write_text('{"balance": 10}', encoding="utf-8")
        patches = (
            patch.object(config, "DATA_DIR", self.data),
            patch.object(config, "GATEWAYS", {"telegram": {"owner_identity": "12345"}}),
            patch.object(config, "CRON", True),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        tasks.update(OWNER, FIRST, "completed")
        tasks.update(OWNER, SECOND, "completed")

    def _register(self, task):
        return followups.register(OWNER, task, "json_file", str(self.snapshot), "balance")

    def _tasks(self):
        return {watch["task"] for watch in json.loads(followups.registry_path().read_text())["watches"]}

    def test_public_calls_accept_keyword_identity(self):
        self.assertIn("Monitoring", followups.register(
            identity=OWNER, task=FIRST, kind="json_file",
            source=str(self.snapshot), field="balance"))
        self.assertIn(FIRST, followups.list_watches(identity=OWNER))
        self.assertEqual(followups.stop(identity=OWNER, task=FIRST),
                         f"Stopped monitoring {FIRST}.")

    def test_two_thread_registrations_retain_both_watches(self):
        entered = threading.Event()
        release = threading.Event()
        original = followups._save
        errors = []
        second_started = threading.Event()

        def paused(value):
            if value["watches"][0]["task"] == FIRST:
                entered.set()
                if not release.wait(8):
                    raise TimeoutError("first save was not released")
            return original(value)

        def worker(task, started=None):
            if started:
                started.set()
            try:
                self._register(task)
            except BaseException as exc:
                errors.append(exc)

        with patch.object(followups, "_save", side_effect=paused):
            first = threading.Thread(target=worker, args=(FIRST,))
            second = threading.Thread(target=worker, args=(SECOND, second_started))
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                second.start()
                self.assertTrue(second_started.wait(5))
                # On the old code second completes while the first save is paused.
                second.join(timeout=0.5)
            finally:
                release.set()
                first.join(timeout=8)
                if second.ident is not None:
                    second.join(timeout=8)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self._tasks(), {FIRST, SECOND})
        self.assertEqual(len(followups.scheduler.list_jobs()), 1)

    def test_spawned_registration_cannot_overwrite_parent_watch(self):
        context = multiprocessing.get_context("spawn")
        entered, release = context.Event(), context.Event()
        results = context.Queue()
        child = context.Process(target=_register_child, args=(str(self.data), entered, release, results))
        child.start()
        try:
            self.assertTrue(entered.wait(8))
            parent_started = threading.Event()
            errors = []

            def parent():
                parent_started.set()
                try:
                    self._register(SECOND)
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=parent)
            worker.start()
            try:
                self.assertTrue(parent_started.wait(5))
                # Old code completes parent registration before child saves its stale copy.
                worker.join(timeout=0.7)
            finally:
                release.set()
                worker.join(timeout=8)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertIsNone(results.get(timeout=8))
            child.join(timeout=8)
            self.assertEqual(child.exitcode, 0)
            self.assertEqual(self._tasks(), {FIRST, SECOND})
            self.assertEqual(len(followups.scheduler.list_jobs()), 1)
        finally:
            release.set()
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
            results.close()

    def test_stop_removal_failure_keeps_retryable_watch(self):
        self._register(FIRST)
        with patch.object(followups, "_remove_job", side_effect=OSError("scheduler unavailable")):
            with self.assertRaisesRegex(OSError, "scheduler unavailable"):
                followups.stop(OWNER, FIRST)
        self.assertEqual(self._tasks(), {FIRST})
        self.assertEqual(len(followups.scheduler.list_jobs()), 1)

    def test_stop_cannot_remove_job_created_for_new_watch(self):
        self._register(FIRST)
        entered, release = threading.Event(), threading.Event()
        original = followups._remove_job
        results = []
        errors = []

        def paused(identity):
            entered.set()
            if not release.wait(8):
                raise TimeoutError("stop job removal not released")
            return original(identity)

        def stop():
            try:
                results.append(followups.stop(OWNER, FIRST))
            except BaseException as exc:
                errors.append(exc)

        def register():
            try:
                results.append(self._register(SECOND))
            except BaseException as exc:
                errors.append(exc)

        with patch.object(followups, "_remove_job", side_effect=paused):
            first = threading.Thread(target=stop)
            second = threading.Thread(target=register)
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                second.start()
                second.join(timeout=0.5)
            finally:
                release.set()
                first.join(timeout=8)
                if second.ident is not None:
                    second.join(timeout=8)
        self.assertEqual(errors, [])
        self.assertEqual(self._tasks(), {SECOND})
        self.assertEqual(len(followups.scheduler.list_jobs()), 1)

    def test_poll_retries_orphan_job_cleanup_after_remove_fails(self):
        self._register(FIRST)
        tasks.update(OWNER, FIRST, "cancelled")
        with patch.object(followups, "_remove_job", side_effect=OSError("scheduler unavailable")):
            with self.assertRaisesRegex(OSError, "scheduler unavailable"):
                followups.poll()
        self.assertEqual(self._tasks(), set())
        self.assertEqual(len(followups.scheduler.list_jobs()), 1)
        followups.poll()
        self.assertEqual(followups.scheduler.list_jobs(), [])

    def test_poll_cannot_restore_watch_after_stop(self):
        self._register(FIRST)
        self.snapshot.write_text('{"balance": 20}', encoding="utf-8")
        entered, release = threading.Event(), threading.Event()
        original = followups._save
        errors = []
        results = []

        def paused(value):
            if value["watches"] and value["watches"][0]["baseline"] == 20:
                entered.set()
                if not release.wait(8):
                    raise TimeoutError("poll save not released")
            return original(value)

        def poll():
            try:
                followups.poll()
            except BaseException as exc:
                errors.append(exc)

        def stop():
            try:
                results.append(followups.stop(OWNER, FIRST))
            except BaseException as exc:
                errors.append(exc)

        with patch.object(followups, "_save", side_effect=paused):
            first = threading.Thread(target=poll)
            second = threading.Thread(target=stop)
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                second.start()
                second.join(timeout=0.5)
            finally:
                release.set()
                first.join(timeout=8)
                if second.ident is not None:
                    second.join(timeout=8)
        self.assertEqual(errors, [])
        self.assertEqual(results, [f"Stopped monitoring {FIRST}."])
        self.assertEqual(self._tasks(), set())
        self.assertEqual(followups.scheduler.list_jobs(), [])


if __name__ == "__main__":
    unittest.main()
