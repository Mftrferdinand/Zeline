"""Contract tests for ``schedule_task``: the agent can schedule its own work.

Zeline's scheduler already ran inside the gateway process, but the only door to it
was `zeline cron` in a terminal. On a phone that means "remind me every morning"
could not be arranged in the conversation where it was asked for — measured before
this change, the string "cron" appeared nowhere in the Telegram gateway and no tool
in TOOL_DEFS could reach the scheduler.

These tests pin the parts that are easy to get quietly wrong: which chat a job
reports to, that every rejection explains itself, that a job's own file uploads land
in the right place and the channel does not outlive the run, and that a public
gateway cannot schedule anything.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


class ScheduleTaskToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="zl-sched-"))
        self._old = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = str(self.home)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)
        self.cron = importlib.import_module("zeline.scheduler")
        self.tools = importlib.import_module("zeline.tools")
        self.workspace = self.home / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.chat = self.tools.ToolExecutor(
            "telegram:4242", profile="full", workspace=str(self.workspace)
        )

    def tearDown(self) -> None:
        import shutil

        if self._old is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)

    def run_tool(self, **kwargs) -> str:
        return self.chat.run("schedule_task", kwargs)

    # -- registration
    def test_the_tool_is_owner_only(self):
        definition = next(d for d in self.tools.TOOL_DEFS if d.name == "schedule_task")
        # A public gateway must not be able to make the agent run unattended work.
        self.assertEqual(sorted(definition.profiles), ["full"])
        self.assertEqual(definition.schema()["function"]["parameters"]["required"], ["action"])
        for profile in ("safe", "workspace"):
            with self.subTest(profile=profile):
                denied = self.tools.ToolExecutor(
                    "telegram:public", profile=profile, workspace=str(self.workspace)
                ).run("schedule_task", {"action": "list"})
                self.assertIn("not allowed for profile", denied.lower())

    # -- the decision that matters most
    def test_a_job_created_in_a_chat_reports_back_to_that_chat(self):
        """Defaulting to `local` would make a scheduled job look like it never ran."""
        self.run_tool(action="add", schedule="09:00", prompt="Summarise commits")
        job = self.cron.list_jobs()[0]
        self.assertEqual(job.deliver, "telegram:4242")

    def test_local_delivery_is_honoured_when_asked_for(self):
        """A job whose output is a file or a commit has nothing to say in chat."""
        result = self.run_tool(
            action="add", schedule="2h", prompt="Rotate the backups", deliver="local"
        )
        self.assertEqual(self.cron.list_jobs()[0].deliver, "local")
        self.assertIn("saved in", result)

    def test_a_cli_session_falls_back_to_local(self):
        """`cli:local` is not a delivery target; sending there would fail every run."""
        executor = self.tools.ToolExecutor(
            "cli:local", profile="full", workspace=str(self.workspace)
        )
        executor.run("schedule_task", {"action": "add", "schedule": "1h", "prompt": "x"})
        self.assertEqual(self.cron.list_jobs()[0].deliver, "local")

    def test_an_explicit_other_chat_is_kept(self):
        self.run_tool(action="add", schedule="1h", prompt="x", deliver="telegram:999")
        self.assertEqual(self.cron.list_jobs()[0].deliver, "telegram:999")

    # -- rejections must be actionable, not generic
    def test_every_rejection_says_what_to_do_instead(self):
        cases = {
            "could not understand the schedule": {
                "action": "add", "schedule": "soon", "prompt": "x",
            },
            "shortest supported interval": {"action": "add", "schedule": "30s", "prompt": "x"},
            "needs a prompt": {"action": "add", "schedule": "1h"},
            "unknown action": {"action": "explode"},
            "needs a job_id": {"action": "pause"},
        }
        for expected, kwargs in cases.items():
            with self.subTest(expected=expected):
                result = self.run_tool(**kwargs)
                self.assertTrue(result.startswith("ERROR schedule_task"), result)
                self.assertIn(expected, result)

    def test_an_unknown_job_id_lists_the_real_ones(self):
        """Otherwise the model guesses ids and burns turns."""
        self.run_tool(action="add", schedule="1h", prompt="first")
        self.run_tool(action="add", schedule="2h", prompt="second")
        result = self.run_tool(action="show", job_id="job99")
        self.assertIn("no job 'job99'", result)
        self.assertIn("job1, job2", result)

    def test_a_disabled_scheduler_is_reported_before_anything_is_written(self):
        """Silently accepting a job that can never fire is the worst outcome."""
        with mock.patch.object(self.cron, "enabled", return_value=False):
            result = self.run_tool(action="add", schedule="1h", prompt="x")
        self.assertIn("disabled in this install", result)
        self.assertEqual(self.cron.list_jobs(), [])

    # -- lifecycle
    def test_pause_resume_run_remove_round_trip(self):
        self.run_tool(action="add", schedule="1h", prompt="x")
        self.assertIn("Paused job1", self.run_tool(action="pause", job_id="job1"))
        self.assertFalse(self.cron.find_job("job1").enabled)
        self.assertIn("Resumed job1", self.run_tool(action="resume", job_id="job1"))
        self.assertTrue(self.cron.find_job("job1").enabled)
        self.assertIn("next scheduler tick", self.run_tool(action="run", job_id="job1"))
        self.assertEqual(self.cron.find_job("job1").next_run, 0.0)
        self.assertIn("Removed job1", self.run_tool(action="remove", job_id="job1"))
        self.assertIsNone(self.cron.find_job("job1"))

    def test_listing_shows_the_target_and_the_task(self):
        """The two things an operator checks: where it goes and what it does."""
        self.run_tool(action="add", schedule="09:00", prompt="Summarise yesterday's commits")
        listing = self.run_tool(action="list")
        self.assertIn("telegram:4242", listing)
        self.assertIn("Summarise yesterday's commits", listing)
        self.assertIn("daily at 09:00", listing)
        self.assertIn("gateway process is running", listing)


class NextRunWordingTests(unittest.TestCase):
    """`next_run` holds two values that are not times, and both mislead if formatted."""

    def setUp(self) -> None:
        self.cron = importlib.import_module("zeline.scheduler")

    def _job(self, **kwargs):
        return self.cron.Job(id="job1", schedule="1h", prompt="x", **kwargs)

    def test_armed_to_run_now_does_not_read_as_never(self):
        """run_now() sets next_run=0.0; format_time(0) is the string "never"."""
        job = self._job(next_run=0.0)
        self.assertIn("next tick", self.cron.describe_next_run(job))
        self.assertEqual(self.cron.format_time(0.0), "never")

    def test_an_overdue_job_reads_as_due_now_not_as_a_past_date(self):
        job = self._job(next_run=time.time() - 3600)
        self.assertEqual(self.cron.describe_next_run(job), "due now")

    def test_a_paused_job_says_paused_rather_than_a_time_it_will_not_honour(self):
        job = self._job(next_run=time.time() + 3600, enabled=False)
        self.assertEqual(self.cron.describe_next_run(job), "paused")

    def test_a_future_run_is_a_plain_local_timestamp(self):
        moment = time.time() + 7200
        job = self._job(next_run=moment)
        self.assertEqual(self.cron.describe_next_run(job), self.cron.format_time(moment))


class CronFileDeliveryTests(unittest.TestCase):
    """A scheduled job must be able to hand over a file it produced.

    `send_file` resolves the calling identity, and a job runs as `cron:<id>` — which
    is not a chat. Without a channel registered for the run, a nightly job that
    renders a chart creates the PNG and has no way to deliver it.
    """

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="zl-crondeliv-"))
        self._old = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = str(self.home)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)
        self.cron = importlib.import_module("zeline.scheduler")
        self.delivery = importlib.import_module("zeline.delivery")

    def tearDown(self) -> None:
        import shutil

        if self._old is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)

    def _scheduler(self, capture: list):
        class Sessions:
            def send(_self, *, identity, text, tool_profile, system_extra=""):
                capture.append(
                    {
                        "identity": identity,
                        "has_channel": self.delivery.has_channel(identity),
                        "profile": tool_profile,
                        "system_extra": system_extra,
                    }
                )
                return "done"

        return self.cron.Scheduler(Sessions(), tick_seconds=1)

    def test_a_telegram_job_gets_a_file_channel_for_the_run_only(self):
        job = self.cron.add_job("1h", "render a chart", "telegram:4242")
        seen: list = []
        self._scheduler(seen)._run_agent(job)
        self.assertEqual(seen[0]["identity"], "cron:job1")
        self.assertTrue(seen[0]["has_channel"], "the job could not deliver a file")
        # Left registered, a later turn or another job could push a file into this chat.
        self.assertFalse(self.delivery.has_channel("cron:job1"))

    def test_a_local_job_gets_no_channel(self):
        """There is no chat to upload to; send_file should say so, not fail obscurely."""
        job = self.cron.add_job("1h", "rotate backups", "local")
        seen: list = []
        self._scheduler(seen)._run_agent(job)
        self.assertFalse(seen[0]["has_channel"])

    def test_the_channel_is_released_even_when_the_turn_raises(self):
        job = self.cron.add_job("1h", "boom", "telegram:4242")

        class Exploding:
            def send(self, **kwargs):
                raise RuntimeError("model down")

        scheduler = self.cron.Scheduler(Exploding(), tick_seconds=1)
        with self.assertRaises(RuntimeError):
            scheduler._run_agent(job)
        self.assertFalse(self.delivery.has_channel("cron:job1"))

    def test_the_scheduled_run_is_told_nobody_can_answer_a_question(self):
        job = self.cron.add_job("1h", "x", "local")
        seen: list = []
        self._scheduler(seen)._run_agent(job)
        self.assertIn("never ask one", seen[0]["system_extra"])

    def test_a_failed_upload_never_kills_the_job(self):
        with mock.patch.dict(
            self.cron.config.GATEWAYS, {"telegram": {"token": "t"}}, clear=True
        ), mock.patch("zeline.gateways.telegram._send_produced_file", side_effect=OSError("no net")):
            self.assertFalse(
                self.cron._deliver_file_telegram("4242", Path("x.png"), "", "image")
            )

    def test_a_missing_token_or_chat_is_refused_quietly(self):
        with mock.patch.dict(self.cron.config.GATEWAYS, {}, clear=True):
            self.assertFalse(self.cron._deliver_file_telegram("4242", Path("x.png"), "", "image"))
        with mock.patch.dict(
            self.cron.config.GATEWAYS, {"telegram": {"token": "t"}}, clear=True
        ):
            self.assertFalse(self.cron._deliver_file_telegram("", Path("x.png"), "", "image"))


class ProgressFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.telegram = importlib.import_module("zeline.gateways.telegram")

    def test_each_action_gets_its_own_line_not_a_generic_tool_label(self):
        cases = {
            ("add", "09:00", ""): "⏰ Scheduling a job: 09:00",
            ("list", "", ""): "⏰ Checking scheduled jobs…",
            ("pause", "", "job1"): "⏸ Pausing scheduled job job1",
            ("resume", "", "job1"): "▶️ Resuming scheduled job job1",
            ("run", "", "job2"): "⚡ Running scheduled job now job2",
            ("remove", "", "job3"): "🗑 Removing scheduled job job3",
            ("show", "", "job1"): "⏰ Reading scheduled job job1",
        }
        for (action, schedule, job_id), expected in cases.items():
            with self.subTest(action=action):
                line = self.telegram._tool_progress_text(
                    "schedule_task",
                    {"action": action, "schedule": schedule, "job_id": job_id},
                )
                self.assertEqual(line, expected)
                self.assertFalse(line.startswith("🔧"))


if __name__ == "__main__":
    unittest.main()
