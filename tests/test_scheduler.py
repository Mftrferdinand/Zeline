"""Contract tests for scheduled jobs.

A scheduler is easy to write and easy to write *wrongly*, so the tests are aimed
at the four classic failure modes rather than at the happy path:

- **No catch-up stampede.** A gateway that was off for three days must fire a
  daily job once on return, not three times.
- **No overlapping runs.** A job still running when its next tick arrives is
  skipped, not started again — an agent turn can take minutes.
- **Output survives a failed delivery.** The result is written to disk before
  delivery is attempted, so an unreachable chat never destroys the work.
- **Nothing in the loop can be fatal.** A job that raises, a corrupt jobs file,
  and a broken schedule all leave the scheduler running.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    cfg = importlib.import_module("zeline.config")
    scheduler = importlib.import_module("zeline.scheduler")
    cli = importlib.import_module("zeline.cli")
    return cfg, scheduler, cli


class FakeSessions:
    """Stands in for SessionStore, recording what the scheduler asked for."""

    def __init__(self, reply="done", fail=False, delay=0.0):
        self.calls: list[dict] = []
        self.reply = reply
        self.fail = fail
        self.delay = delay
        self.started = threading.Event()

    def send(self, identity, text, tool_profile, system_extra="", **kwargs):
        self.calls.append({
            "identity": identity, "text": text,
            "profile": tool_profile, "extra": system_extra,
        })
        self.started.set()
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("agent blew up")
        return self.reply


class CronBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.cron, self.cli = fresh(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()


class ScheduleParsingTests(CronBase):
    def test_intervals_in_every_spelling_people_use(self):
        cases = {
            "30m": 1800, "every 30m": 1800, "45 minutes": 2700,
            "2h": 7200, "every 2 hours": 7200, "1d": 86400, "every 3 days": 259200,
        }
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.cron.parse_schedule(text).seconds, seconds)

    def test_daily_times(self):
        for text in ("09:00", "at 09:00", "daily at 09:00", "every day at 09:00"):
            with self.subTest(text=text):
                parsed = self.cron.parse_schedule(text)
                self.assertTrue(parsed.is_daily)
                self.assertEqual((parsed.hour, parsed.minute), (9, 0))

    def test_sub_minute_intervals_are_refused(self):
        """The tick loop cannot honour them, and the agent would run nonstop."""
        with self.assertRaises(self.cron.CronError) as caught:
            self.cron.parse_schedule("30s")
        self.assertIn("1 minute", str(caught.exception))

    def test_impossible_times_are_refused(self):
        for text in ("25:00", "09:99"):
            with self.subTest(text=text), self.assertRaises(self.cron.CronError):
                self.cron.parse_schedule(text)

    def test_gibberish_is_refused_with_examples(self):
        with self.assertRaises(self.cron.CronError) as caught:
            self.cron.parse_schedule("whenever I feel like it")
        message = str(caught.exception)
        self.assertIn("30m", message)
        self.assertIn("09:00", message)

    def test_empty_is_refused(self):
        with self.assertRaises(self.cron.CronError):
            self.cron.parse_schedule("   ")

    def test_next_after_for_an_interval(self):
        parsed = self.cron.parse_schedule("15m")
        now = time.time()
        self.assertAlmostEqual(parsed.next_after(now), now + 900, delta=1)

    def test_a_daily_time_already_passed_moves_to_tomorrow(self):
        parsed = self.cron.parse_schedule("09:00")
        afternoon = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
        following = datetime.fromtimestamp(parsed.next_after(afternoon.timestamp()))
        self.assertEqual((following.hour, following.minute), (9, 0))
        self.assertEqual(following.date(), (afternoon + timedelta(days=1)).date())

    def test_a_daily_time_still_ahead_stays_today(self):
        parsed = self.cron.parse_schedule("23:30")
        morning = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
        following = datetime.fromtimestamp(parsed.next_after(morning.timestamp()))
        self.assertEqual(following.date(), morning.date())


class JobStoreTests(CronBase):
    def test_adding_a_job_arms_it(self):
        job = self.cron.add_job("30m", "do the thing")
        self.assertEqual(job.id, "job1")
        self.assertGreater(job.next_run, time.time())
        self.assertEqual([j.id for j in self.cron.list_jobs()], ["job1"])

    def test_ids_do_not_collide(self):
        ids = [self.cron.add_job("1h", f"task {i}").id for i in range(3)]
        self.assertEqual(len(set(ids)), 3)

    def test_a_job_needs_a_prompt(self):
        with self.assertRaises(self.cron.CronError) as caught:
            self.cron.add_job("30m", "   ")
        self.assertIn("prompt", str(caught.exception))

    def test_a_bad_schedule_is_rejected_before_anything_is_written(self):
        with self.assertRaises(self.cron.CronError):
            self.cron.add_job("nonsense", "do it")
        self.assertEqual(self.cron.list_jobs(), [])

    def test_pause_resume_remove(self):
        self.cron.add_job("30m", "x")
        self.assertTrue(self.cron.set_enabled("job1", False))
        self.assertFalse(self.cron.find_job("job1").enabled)
        self.assertTrue(self.cron.set_enabled("job1", True))
        self.assertTrue(self.cron.find_job("job1").enabled)
        self.assertTrue(self.cron.remove_job("job1"))
        self.assertIsNone(self.cron.find_job("job1"))

    def test_resuming_rearms_from_now(self):
        """Otherwise a long-paused job fires instantly on a stale timestamp."""
        self.cron.add_job("1d", "x")
        self.cron.set_enabled("job1", False)
        jobs = self.cron._read_jobs()
        jobs[0].next_run = time.time() - 100_000
        self.cron._write_jobs(jobs)
        self.cron.set_enabled("job1", True)
        self.assertGreater(self.cron.find_job("job1").next_run, time.time())

    def test_run_now_arms_and_enables(self):
        self.cron.add_job("1d", "x")
        self.cron.set_enabled("job1", False)
        self.cron.run_now("job1")
        job = self.cron.find_job("job1")
        self.assertTrue(job.enabled)
        self.assertIn("job1", [j.id for j in self.cron.due_jobs()])

    def test_removing_a_missing_job_is_reported(self):
        self.assertFalse(self.cron.remove_job("ghost"))
        self.assertFalse(self.cron.set_enabled("ghost", True))
        self.assertFalse(self.cron.run_now("ghost"))

    def test_a_corrupt_jobs_file_is_treated_as_empty(self):
        self.cron.add_job("30m", "x")
        self.cron.jobs_path().write_text("{not json", encoding="utf-8")
        self.assertEqual(self.cron.list_jobs(), [])
        # And it recovers: adding still works.
        self.assertEqual(self.cron.add_job("30m", "y").id, "job1")

    def test_unknown_keys_from_a_newer_version_are_ignored(self):
        """A jobs.json written by a newer Zeline must not brick an older one."""
        self.cron._ensure_dir(self.cron.cron_dir())
        self.cron.jobs_path().write_text(json.dumps([{
            "id": "job1", "schedule": "30m", "prompt": "x",
            "some_future_field": {"nested": True},
        }]), encoding="utf-8")
        jobs = self.cron.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].prompt, "x")

    def test_jobs_file_is_private(self):
        self.cron.add_job("30m", "x")
        if os.name == "posix":
            import stat
            self.assertEqual(stat.S_IMODE(self.cron.jobs_path().stat().st_mode), 0o600)

    def test_paused_jobs_are_never_due(self):
        self.cron.add_job("30m", "x")
        self.cron.run_now("job1")
        self.cron.set_enabled("job1", False)
        self.assertEqual(self.cron.due_jobs(), [])

    def test_disabled_by_config(self):
        saved = self.config.config_copy()
        saved["tools"]["cron"] = False
        self.config.save_config(saved)
        self.assertFalse(self.cron.enabled())


class ExecutionTests(CronBase):
    def scheduler(self, sessions):
        return self.cron.Scheduler(sessions, tick_seconds=0.05)

    def test_a_due_job_runs_the_prompt_as_an_agent_turn(self):
        sessions = FakeSessions(reply="the report")
        self.cron.add_job("30m", "write the report")
        self.cron.run_now("job1")
        scheduler = self.scheduler(sessions)
        scheduler.tick()
        self.assertTrue(sessions.started.wait(5))
        self._settle(scheduler)
        self.assertEqual(sessions.calls[0]["text"], "write the report")

    def test_cron_runs_use_their_own_identity(self):
        """A nightly job must not appear in the operator's chat history."""
        sessions = FakeSessions()
        self.cron.add_job("30m", "x")
        self.cron.run_now("job1")
        scheduler = self.scheduler(sessions)
        scheduler.tick()
        self.assertTrue(sessions.started.wait(5))
        self._settle(scheduler)
        self.assertEqual(sessions.calls[0]["identity"], "cron:job1")

    def test_the_prompt_tells_the_agent_nobody_is_watching(self):
        sessions = FakeSessions()
        self.cron.add_job("30m", "x")
        self.cron.run_now("job1")
        scheduler = self.scheduler(sessions)
        scheduler.tick()
        self.assertTrue(sessions.started.wait(5))
        self._settle(scheduler)
        extra = sessions.calls[0]["extra"]
        self.assertIn("SCHEDULED", extra)
        self.assertIn("never ask", extra)

    def test_output_is_written_to_disk(self):
        sessions = FakeSessions(reply="findings: all clear")
        self.cron.add_job("30m", "check things")
        self.cron.run_now("job1")
        scheduler = self.scheduler(sessions)
        scheduler.tick()
        self._settle(scheduler)
        files = list(self.cron.output_dir().glob("job1-*.md"))
        self.assertEqual(len(files), 1)
        body = files[0].read_text(encoding="utf-8")
        self.assertIn("findings: all clear", body)
        self.assertIn("check things", body)

    def test_a_job_that_raises_is_recorded_not_fatal(self):
        sessions = FakeSessions(fail=True)
        self.cron.add_job("30m", "x")
        self.cron.run_now("job1")
        scheduler = self.scheduler(sessions)
        scheduler.tick()
        self._settle(scheduler)
        job = self.cron.find_job("job1")
        self.assertTrue(job.last_status.startswith("error"))
        self.assertEqual(job.failures, 1)
        self.assertGreater(job.next_run, time.time())

    def test_a_missed_window_fires_once_not_once_per_missed_slot(self):
        """The gateway was off for three days; a daily job must run once."""
        sessions = FakeSessions()
        self.cron.add_job("1d", "daily thing")
        jobs = self.cron._read_jobs()
        jobs[0].next_run = time.time() - 3 * 86400
        self.cron._write_jobs(jobs)
        scheduler = self.scheduler(sessions)
        started = scheduler.tick()
        self.assertEqual(started, ["job1"])
        self._settle(scheduler)
        self.assertEqual(len(sessions.calls), 1)
        # And it is now armed in the future, not still in the past.
        self.assertGreater(self.cron.find_job("job1").next_run, time.time())
        self.assertEqual(scheduler.tick(), [])

    def test_an_overlapping_run_is_skipped_not_queued(self):
        """An agent turn can take minutes; copies must not pile up."""
        sessions = FakeSessions(delay=1.0)
        self.cron.add_job("30m", "slow thing")
        self.cron.run_now("job1")
        scheduler = self.scheduler(sessions)
        scheduler.tick()
        self.assertTrue(sessions.started.wait(5))
        self.cron.run_now("job1")          # becomes due again mid-run
        self.assertEqual(scheduler.tick(), [])
        self._settle(scheduler, timeout=8)
        self.assertEqual(len(sessions.calls), 1)
        job = self.cron.find_job("job1")
        self.assertEqual(job.skips, 1)
        # A skip must not overwrite the real run's status with a scheduling note.
        self.assertIn("ok", job.last_status)
        self.assertEqual(job.runs, 1)

    def test_several_jobs_can_run_in_one_tick(self):
        sessions = FakeSessions()
        for index in range(3):
            self.cron.add_job("30m", f"job body {index}")
            self.cron.run_now(f"job{index + 1}")
        scheduler = self.scheduler(sessions)
        self.assertEqual(len(scheduler.tick()), 3)
        self._settle(scheduler)
        self.assertEqual(len(sessions.calls), 3)

    def test_a_skip_is_visible_in_the_listing(self):
        """A rising skip count is how an operator learns the interval is too short."""
        self.cron.add_job("30m", "x")
        jobs = self.cron._read_jobs()
        jobs[0].skips = 4
        self.cron._write_jobs(jobs)
        self.assertIn("4 skipped", self.cron.find_job("job1").describe())

    def test_run_counters_advance(self):
        sessions = FakeSessions()
        self.cron.add_job("30m", "x")
        self.cron.run_now("job1")
        scheduler = self.scheduler(sessions)
        scheduler.tick()
        self._settle(scheduler)
        self.assertEqual(self.cron.find_job("job1").runs, 1)

    def _settle(self, scheduler, timeout: float = 5.0):
        deadline = time.time() + timeout
        while scheduler.busy and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(scheduler.busy, [], "a job never finished")


class DeliveryTests(CronBase):
    def test_local_delivery_is_a_success(self):
        job = self.cron.add_job("30m", "x", "local")
        delivered, detail = self.cron.deliver(job, "text")
        self.assertTrue(delivered)
        self.assertIn("locally", detail)

    def test_an_unknown_target_is_reported_not_raised(self):
        job = self.cron.add_job("30m", "x", "carrier-pigeon:42")
        delivered, detail = self.cron.deliver(job, "text")
        self.assertFalse(delivered)
        self.assertIn("unknown delivery target", detail)

    def test_telegram_without_a_token_is_reported(self):
        job = self.cron.add_job("30m", "x", "telegram:12345")
        delivered, detail = self.cron.deliver(job, "text")
        self.assertFalse(delivered)
        self.assertIn("token", detail)

    def test_telegram_delivery_uses_the_gateway_sender(self):
        saved = self.config.config_copy()
        saved["gateways"]["telegram"]["token"] = "123:abc"
        self.config.save_config(saved)
        job = self.cron.add_job("30m", "x", "telegram:999")
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_api_call", return_value={"ok": True}) as sender:
            delivered, detail = self.cron.deliver(job, "the result")
        self.assertTrue(delivered, detail)
        self.assertEqual(sender.call_args.kwargs["chat_id"], "999")
        self.assertIn("the result", sender.call_args.kwargs["text"])

    def test_a_failed_delivery_does_not_lose_the_output(self):
        """Delivery fails for reasons unrelated to the work; the work stays."""
        sessions = FakeSessions(reply="valuable findings")
        self.cron.add_job("30m", "x", "telegram:999")   # no token configured
        self.cron.run_now("job1")
        scheduler = self.cron.Scheduler(sessions, tick_seconds=0.05)
        scheduler.tick()
        deadline = time.time() + 5
        while scheduler.busy and time.time() < deadline:
            time.sleep(0.05)
        files = list(self.cron.output_dir().glob("job1-*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("valuable findings", files[0].read_text(encoding="utf-8"))
        status = self.cron.find_job("job1").last_status
        self.assertIn("undelivered", status)
        self.assertNotIn("error", status)   # the work succeeded; only delivery did not

    def test_a_raising_delivery_backend_is_contained(self):
        saved = self.config.config_copy()
        saved["gateways"]["telegram"]["token"] = "123:abc"
        self.config.save_config(saved)
        job = self.cron.add_job("30m", "x", "telegram:999")
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_api_call", side_effect=RuntimeError("network gone")):
            delivered, detail = self.cron.deliver(job, "text")
        self.assertFalse(delivered)
        self.assertIn("network gone", detail)

    def test_two_runs_in_the_same_second_do_not_overwrite_each_other(self):
        """Found while verifying: a second-resolution stamp alone collides."""
        job = self.cron.add_job("30m", "x")
        first = self.cron.save_output(job, "first result")
        second = self.cron.save_output(job, "second result")
        self.assertNotEqual(first, second)
        self.assertIn("first result", first.read_text(encoding="utf-8"))
        self.assertIn("second result", second.read_text(encoding="utf-8"))

    def test_old_output_is_pruned(self):
        job = self.cron.add_job("30m", "x")
        with mock.patch.object(self.cron, "MAX_OUTPUT_FILES", 3):
            for index in range(6):
                self.cron.save_output(job, f"run {index}")
                time.sleep(0.01)
        self.assertLessEqual(len(list(self.cron.output_dir().glob("*.md"))), 3)


class LoopTests(CronBase):
    def test_the_loop_starts_and_stops(self):
        scheduler = self.cron.Scheduler(FakeSessions(), tick_seconds=0.05)
        self.assertTrue(scheduler.start())
        self.assertTrue(scheduler.alive)
        scheduler.stop()
        self.assertFalse(scheduler.alive)

    def test_starting_twice_does_not_start_two_threads(self):
        scheduler = self.cron.Scheduler(FakeSessions(), tick_seconds=0.05)
        scheduler.start()
        first = scheduler._thread
        scheduler.start()
        self.assertIs(scheduler._thread, first)
        scheduler.stop()

    def test_the_loop_refuses_to_start_when_disabled(self):
        saved = self.config.config_copy()
        saved["tools"]["cron"] = False
        self.config.save_config(saved)
        scheduler = self.cron.Scheduler(FakeSessions(), tick_seconds=0.05)
        self.assertFalse(scheduler.start())
        self.assertFalse(scheduler.alive)

    def test_a_failing_tick_does_not_kill_the_loop(self):
        """The loop must outlive any single failure inside it."""
        scheduler = self.cron.Scheduler(FakeSessions(), tick_seconds=0.05)
        calls = {"n": 0}

        def exploding_tick(now=None):
            calls["n"] += 1
            raise RuntimeError("tick exploded")

        scheduler.tick = exploding_tick
        scheduler.start()
        deadline = time.time() + 2
        while calls["n"] < 3 and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(scheduler.alive)
        self.assertGreaterEqual(calls["n"], 3)
        self.assertIn("tick exploded", scheduler.last_error)
        scheduler.stop()

    def test_the_loop_actually_fires_a_due_job(self):
        sessions = FakeSessions()
        self.cron.add_job("30m", "loop test")
        self.cron.run_now("job1")
        scheduler = self.cron.Scheduler(sessions, tick_seconds=0.05)
        scheduler.start()
        try:
            self.assertTrue(sessions.started.wait(5))
        finally:
            scheduler.stop()
        self.assertEqual(sessions.calls[0]["text"], "loop test")


class CliTests(CronBase):
    def test_list_on_an_empty_store_explains_the_syntax(self):
        self.assertEqual(self.cli.cmd_cron("list"), 0)

    def test_add_then_list_then_remove(self):
        self.assertEqual(self.cli.cmd_cron("add", schedule="09:00", prompt="morning report"), 0)
        self.assertEqual(self.cli.cmd_cron("list"), 0)
        self.assertEqual(self.cli.cmd_cron("show", job_id="job1"), 0)
        self.assertEqual(self.cli.cmd_cron("remove", job_id="job1"), 0)
        self.assertEqual(self.cron.list_jobs(), [])

    def test_add_without_arguments_shows_usage(self):
        self.assertEqual(self.cli.cmd_cron("add"), 2)

    def test_add_with_a_bad_schedule_exits_nonzero(self):
        self.assertEqual(self.cli.cmd_cron("add", schedule="soon", prompt="x"), 1)

    def test_acting_on_a_missing_job_exits_nonzero(self):
        for action in ("show", "run", "pause", "resume", "remove"):
            with self.subTest(action=action):
                self.assertEqual(self.cli.cmd_cron(action, job_id="ghost"), 1)

    def test_verbs_without_an_id_show_usage(self):
        for action in ("show", "run", "pause", "resume", "remove"):
            with self.subTest(action=action):
                self.assertEqual(self.cli.cmd_cron(action), 2)

    def test_pause_and_resume_change_state(self):
        self.cli.cmd_cron("add", schedule="30m", prompt="x")
        self.cli.cmd_cron("pause", job_id="job1")
        self.assertFalse(self.cron.find_job("job1").enabled)
        self.cli.cmd_cron("resume", job_id="job1")
        self.assertTrue(self.cron.find_job("job1").enabled)

    def test_unknown_action_lists_the_valid_ones(self):
        self.assertEqual(self.cli.cmd_cron("teleport"), 2)

    def test_add_with_delivery_records_the_target(self):
        self.cli.cmd_cron("add", schedule="30m", prompt="x", deliver="telegram:555")
        self.assertEqual(self.cron.find_job("job1").deliver, "telegram:555")

    def test_cli_exposes_the_subcommands(self):
        parser = self.cli.build_parser()
        namespace = parser.parse_args(["cron", "add", "30m", "do it", "--deliver", "telegram:1"])
        self.assertEqual(namespace.cron_action, "add")
        self.assertEqual(namespace.schedule, "30m")
        self.assertEqual(namespace.prompt, "do it")
        self.assertEqual(namespace.deliver, "telegram:1")
        for verb in ("show", "run", "pause", "resume", "remove"):
            with self.subTest(verb=verb):
                self.assertEqual(parser.parse_args(["cron", verb, "job1"]).job_id, "job1")


if __name__ == "__main__":
    unittest.main()
