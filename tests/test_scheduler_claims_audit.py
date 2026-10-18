"""Regression coverage for execution ownership and stale scheduler snapshots."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest import mock

try:
    from tests.test_scheduler import CronBase, FakeSessions, SOURCE_ROOT
except ModuleNotFoundError:
    from test_scheduler import CronBase, FakeSessions, SOURCE_ROOT


class ClaimsAuditTests(CronBase):
    def child(self, code):
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=SOURCE_ROOT,
            env={**os.environ, "ZELINE_HOME": str(self.home)},
            text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_live_claim_does_not_expire_across_processes(self):
        claim = self.cron._try_claim("job1", time.time() - 100000)
        self.assertTrue(claim)
        try:
            self.assertEqual(self.child(
                "import time; from zeline import scheduler as s; "
                "print(bool(s._try_claim('job1', time.time())))"), "False")
        finally:
            self.cron._release_claim("job1", claim)

    def test_slow_live_runner_blocks_future_tick_in_other_process(self):
        entered, release = threading.Event(), threading.Event()

        class Sessions:
            def send(self, **kwargs):
                entered.set()
                if not release.wait(15):
                    raise RuntimeError("test runner was not released")
                return "done"

        job = self.cron.add_job("1h", "slow")
        self.cron.run_now(job.id)
        runner = self.cron.Scheduler(Sessions())
        try:
            self.assertEqual(runner.tick(), [job.id])
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.child(
                "import time; from zeline import scheduler as s; "
                "print(s.Scheduler(None).tick(time.time()+100000))"), "[]")
        finally:
            release.set()
            runner.stop(5)
        self.assertEqual(self.cron.find_job(job.id).runs, 1)

    def test_dead_process_claim_is_immediately_reclaimable(self):
        self.assertEqual(self.child(
            "import time, os; from zeline import scheduler as s; "
            "claim=s._try_claim('job1', time.time()); "
            "print(bool(claim), flush=True); os._exit(0)"), "True")
        claim = self.cron._try_claim("job1", time.time())
        self.assertTrue(claim, "dead owner must not block until a lease expires")
        self.cron._release_claim("job1", claim)

    def test_stale_release_cannot_release_new_owner(self):
        old = self.cron._try_claim("job1", 1)
        self.cron._release_claim("job1", old)
        current = self.cron._try_claim("job1", 2)
        try:
            self.cron._release_claim("job1", old)
            self.assertEqual(self.child(
                "from zeline import scheduler as s; print(bool(s._try_claim('job1', 3)))"), "False")
        finally:
            self.cron._release_claim("job1", current)

    def test_stale_due_snapshot_cannot_start_changed_job(self):
        for change in ("removed", "replaced", "paused", "rescheduled", "completed"):
            with self.subTest(change=change):
                self.cron._write_jobs([])
                job = self.cron.add_job("1h", "original")
                self.cron.run_now(job.id)
                snapshot = self.cron.due_jobs()
                if change in ("removed", "replaced"):
                    self.cron.remove_job(job.id)
                    if change == "replaced":
                        replacement = self.cron.add_job("1h", "replacement")
                        self.cron.run_now(replacement.id)
                elif change == "paused":
                    self.cron.set_enabled(job.id, False)
                elif change == "rescheduled":
                    self.cron.set_enabled(job.id, True)
                else:
                    first = self.cron.Scheduler(FakeSessions())
                    self.assertEqual(first.tick(), [job.id])
                    first.stop(5)
                sessions = FakeSessions()
                runner = self.cron.Scheduler(sessions)
                try:
                    with mock.patch.object(self.cron, "due_jobs", return_value=snapshot):
                        started = runner.tick()
                    runner.stop(5)
                    self.assertEqual(started, [])
                    self.assertEqual(sessions.calls, [])
                finally:
                    runner.stop(5)

    def test_failed_worker_start_releases_ownership_and_busy_state(self):
        job = self.cron.add_job("1h", "task")
        self.cron.run_now(job.id)
        runner = self.cron.Scheduler(FakeSessions())
        with mock.patch.object(self.cron.threading.Thread, "start", side_effect=RuntimeError("no thread")):
            with self.assertRaisesRegex(RuntimeError, "no thread"):
                runner.tick()
        self.assertEqual(runner.busy, [])
        self.assertEqual(runner._workers, [])
        self.assertEqual(self.cron.find_job(job.id).runs, 0)
        try:
            self.assertEqual(runner.tick(), [job.id])
        finally:
            runner.stop(5)

    def test_recovery_ignores_leftover_deterministic_temp(self):
        self.cron._ensure_dir(self.cron.cron_dir())
        original = b"{broken original"
        self.cron.jobs_path().write_bytes(original)
        backup = self.cron.cron_dir() / f"jobs.recovery-{hashlib.sha256(original).hexdigest()}.json"
        leftover = Path(str(backup) + ".tmp")
        leftover.write_bytes(b"interrupted write")
        self.cron.add_job("1h", "recovery")
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(leftover.read_bytes(), b"interrupted write")
        self.assertEqual(len(self.cron.list_jobs()), 1)

    def test_failed_recovery_preserves_original_and_cleans_temp(self):
        self.cron._ensure_dir(self.cron.cron_dir())
        original = b"{broken original"
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation):
                self.cron.jobs_path().write_bytes(original)
                with mock.patch.object(self.cron.os, operation, side_effect=OSError("disk failure")):
                    with self.assertRaisesRegex(OSError, "disk failure"):
                        self.cron.add_job("1h", "recovery")
                self.assertEqual(self.cron.jobs_path().read_bytes(), original)
                self.assertEqual(list(self.cron.cron_dir().glob("jobs.recovery-*")), [])

    def test_only_agent_error_envelope_is_failure(self):
        script = self.home / "literal.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("print('ERROR is a successful script result')\n")
        for script_only in (False, True):
            with self.subTest(script_only=script_only):
                job = self.cron.add_job("1h", "task" if not script_only else "",
                                        no_agent=script_only, script=str(script) if script_only else "")
                runner = self.cron.Scheduler(FakeSessions(reply="ERROR: provider failed"))
                runner._execute(job, time.time())
                result = self.cron.find_job(job.id)
                self.assertEqual(result.failures, 0 if script_only else 1)
                self.assertEqual(result.last_status.startswith("ok"), script_only)
                self.assertEqual(bool(result.last_output), script_only)

    def test_agent_failure_cannot_feed_downstream_context(self):
        upstream = self.cron.add_job("1h", "upstream")
        runner = self.cron.Scheduler(FakeSessions(reply="ERROR: execution budget exhausted"))
        runner._execute(upstream, time.time())
        downstream = self.cron.add_job("1h", "downstream", context_from=[upstream.id])
        with self.assertRaisesRegex(self.cron.CronError, "no successful latest result"):
            runner._run_agent(downstream)

    def test_unowned_release_cannot_release_claim(self):
        claim = self.cron._try_claim("job1", 1)
        try:
            self.cron._release_claim("job1")
            self.assertFalse(self.cron._try_claim("job1", 2))
        finally:
            self.cron._release_claim("job1", claim)
