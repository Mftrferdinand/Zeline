"""Regression coverage for the follow-up review of PR #309."""
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


class RuntimeReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        env = patch.dict(os.environ, {"ZELINE_HOME": str(self.home / "state"),
                                     "ZELINE_API_KEY": "fixture", "ZELINE_MODEL": "fixture"})
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.cron = importlib.import_module("zeline.scheduler")

    @staticmethod
    def stamp(value):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()

    def test_jakarta_daily_time_roundtrip_and_resume(self):
        now = self.stamp("2026-10-18T00:00:00")
        with patch.object(self.cron.time, "time", return_value=now):
            job = self.cron.add_job("09:00", "report", timezone="Asia/Jakarta")
            self.assertEqual(job.next_run, self.stamp("2026-10-18T02:00:00"))
            reloaded = self.cron.find_job(job.id)
            self.assertEqual(reloaded.timezone, "Asia/Jakarta")
            self.assertIn("09:00 Asia/Jakarta", self.cron.describe_next_run(reloaded))
            self.cron.set_enabled(job.id, False)
            self.cron.set_enabled(job.id, True)
            self.assertEqual(self.cron.find_job(job.id).next_run, job.next_run)
        self.assertEqual(job.parsed().next_after(job.next_run), self.stamp("2026-10-19T02:00:00"))

    def test_spring_forward_skips_nonexistent_time(self):
        schedule = self.cron.parse_schedule("02:30", "America/New_York")
        self.assertEqual(schedule.next_after(self.stamp("2026-03-08T05:00:00")),
                         self.stamp("2026-03-09T06:30:00"))

    def test_fall_back_fires_only_first_fold(self):
        schedule = self.cron.parse_schedule("01:30", "America/New_York")
        first = self.stamp("2026-11-01T05:30:00")
        self.assertEqual(schedule.next_after(first - 1), first)
        self.assertEqual(schedule.next_after(first), self.stamp("2026-11-02T06:30:00"))

    def test_interval_is_elapsed_time_and_legacy_jobs_still_load(self):
        for zone in ("", "UTC", "Asia/Jakarta"):
            self.assertEqual(self.cron.parse_schedule("1d", zone).next_after(100), 86500)
        self.cron._write_jobs([self.cron.Job("job1", "09:00", "legacy")])
        data = json.loads(self.cron.jobs_path().read_text())
        data[0].pop("timezone")
        self.cron.jobs_path().write_text(json.dumps(data))
        self.assertEqual(self.cron.find_job("job1").timezone, "")

    def test_invalid_timezone_rejected_before_state_write(self):
        for zone in ("Unknown/City", "../UTC", 7, None):
            with self.assertRaises(self.cron.CronError):
                self.cron.add_job("09:00", "report", timezone=zone)
        self.assertFalse(self.cron.jobs_path().exists())

    def test_cli_and_native_tool_preserve_timezone(self):
        cli = importlib.import_module("zeline.cli")
        with redirect_stdout(io.StringIO()) as captured:
            result = cli.main(["cron", "add", "09:00", "report", "--timezone", "Asia/Jakarta"])
        self.assertEqual(result, 0)
        self.assertIn("Asia/Jakarta", captured.getvalue())
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("cli:local", profile="full", workspace=self.home)
        result = executor.run("schedule_task", {"action": "add", "schedule": "09:00",
                                               "prompt": "second", "timezone": "UTC"})
        self.assertIn("Created", result)
        self.assertEqual([j.timezone for j in self.cron.list_jobs()], ["Asia/Jakarta", "UTC"])
        schemas = executor.all_schemas
        schema = next(s for s in schemas if s["function"]["name"] == "schedule_task")
        self.assertIn("timezone", schema["function"]["parameters"]["properties"])

    def test_old_running_job_cannot_overwrite_replacement(self):
        old = self.cron.add_job("1h", "old")
        replacement = []
        def replace_while_running(job):
            self.cron.remove_job(job.id)
            replacement.append(self.cron.add_job("2h", "replacement"))
            return "old output"
        scheduler = self.cron.Scheduler(Mock())
        with patch.object(scheduler, "_run_agent", side_effect=replace_while_running), \
             patch.object(self.cron, "deliver", return_value=(True, "fixture")):
            scheduler._execute(old, 100)
        current = self.cron.find_job(old.id)
        self.assertEqual(current.id, replacement[0].id)
        self.assertNotEqual(old.generation, current.generation)
        self.assertEqual(current.runs, 0)
        self.assertEqual(current.last_output, "")
        self.assertEqual(current.next_run, replacement[0].next_run)

    def test_stale_skip_cannot_change_replacement(self):
        old = self.cron.add_job("1h", "old")
        self.cron.remove_job(old.id)
        replacement = self.cron.add_job("2h", "new")
        self.cron._record_skip(old.id, next_run=1, expected_generation=self.cron._generation(old))
        current = self.cron.find_job(replacement.id)
        self.assertEqual(current.skips, 0)
        self.assertEqual(current.next_run, replacement.next_run)

    def test_legacy_generation_fence_allows_own_completion(self):
        job = self.cron.Job("job1", "1h", "legacy", created_at=123)
        self.cron._write_jobs([job])
        self.cron._record_run(job.id, status="ok", when=2, next_run=3,
                              expected_generation=self.cron._generation(job))
        self.assertEqual(self.cron.find_job(job.id).runs, 1)

    def persistence(self):
        module = importlib.import_module("zeline.session_store")
        return module.SessionPersistence(self.home / "sessions.db")

    def test_completed_turn_commits_window_and_archive_together(self):
        store = self.persistence()
        messages = [{"role": "user", "content": "question"}, {"role": "assistant", "content": "answer"}]
        store.save_turn("fixture", messages, "question", "answer", "title")
        self.assertEqual(store.load("fixture"), (messages, "title"))
        with closing(store._connect()) as conn:
            rows = conn.execute("SELECT role, content FROM archive ORDER BY id").fetchall()
        self.assertEqual(rows, [("user", "question"), ("assistant", "answer")])

    def test_archive_failure_rolls_back_session_and_partial_archive(self):
        store = self.persistence()
        old = [{"role": "user", "content": "old"}]
        store.save("fixture", old, "old title")
        with closing(store._connect()) as conn, conn:
            conn.execute("CREATE TRIGGER fail_answer BEFORE INSERT ON archive "
                         "WHEN NEW.role = 'assistant' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        with self.assertRaises(Exception):
            store.save_turn("fixture", [{"role": "user", "content": "new"}], "new", "answer", "new title")
        self.assertEqual(store.load("fixture"), (old, "old title"))
        with closing(store._connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM archive").fetchone()[0], 0)

    def test_failed_save_warns_without_leaking_details_and_recovers(self):
        persistence = self.persistence()
        sessions = importlib.import_module("zeline.sessions")
        store = sessions.SessionStore(persistence=persistence)
        session = store.get_or_create("fixture", "safe")
        with patch.object(session.agent, "send", return_value="finished"), \
             patch.object(persistence, "save_turn", side_effect=OSError("SECRET-CONTENT")), \
             self.assertLogs("zeline.sessions", level="WARNING") as logs:
            reply = store.send("fixture", "question", "safe")
        self.assertIn("finished", reply)
        self.assertIn("gagal disimpan", reply)
        self.assertNotIn("SECRET-CONTENT", reply + str(logs.output) + str(store.status("fixture")))
        self.assertTrue(store.status("fixture")["persistence_error"])
        with patch.object(session.agent, "send", return_value="next answer"):
            self.assertEqual(store.send("fixture", "next", "safe"), "next answer")
        self.assertEqual(store.status("fixture")["persistence_error"], "")


if __name__ == "__main__":
    unittest.main()
