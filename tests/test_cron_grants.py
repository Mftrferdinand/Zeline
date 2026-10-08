"""Contract tests for cron capability pre-authorization.

A cron job runs with nobody watching, so it cannot ask per tool call. Instead
the operator approves the job's capabilities ONCE at creation time, and the
run is governed by that grant:

- new jobs declare capabilities (tool names and/or risk classes) or get the
  minimal default (read + workspace-confined write);
- grants persist in jobs.json and survive a read/write round-trip;
- jobs written before grants existed (or with corrupt grants) degrade to the
  default, never to a crash and never to wider capability;
- at run time the grant policy decides without prompting; anything outside
  the grant is DENIED and the denial is recorded loudly in the job's status;
- creating a job asks for the grant first: deny = no job, not a dead entry.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
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
    config = importlib.import_module("zeline.config")
    scheduler = importlib.import_module("zeline.scheduler")
    tools = importlib.import_module("zeline.tools")
    return config, scheduler, tools


class _AllowAllPolicy:
    """Test-only: mensimulasikan policy yang dipasang send() di produksi.

    Test-test di bawah menguji flow internal tool schedule_task (capability
    picker), bukan gate-nya — jadi gate dilewati dengan allow-all, seperti
    policy interaktif yang sudah di-approve. Tanpa policy, fallback
    fail-closed (verdict owner) akan me-deny schedule_task sebelum flow
    internalnya berjalan.
    """

    on_tool = None

    def decide(self, executor, name, args):
        return "allow"


class CronGrantsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.cron, self.tools = fresh(self.home)
        self.workspace = self.home / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def jobs_json(self) -> list:
        return json.loads(self.cron.jobs_path().read_text(encoding="utf-8"))


class GrantDeclarationTests(CronGrantsBase):
    def test_default_grants_are_minimal(self):
        job = self.cron.add_job("1h", "do the thing")
        self.assertEqual(job.grants, {"tools": [], "risk": ["read", "write"]})

    def test_explicit_grants_are_kept(self):
        job = self.cron.add_job(
            "1h", "do the thing", grants={"tools": ["run_shell"], "risk": ["read", "network"]}
        )
        self.assertEqual(job.grants["tools"], ["run_shell"])
        self.assertEqual(job.grants["risk"], ["network", "read"])

    def test_grants_survive_a_jobs_json_round_trip(self):
        self.cron.add_job("1h", "x", grants={"tools": ["run_shell"], "risk": ["destructive"]})
        raw = self.jobs_json()
        self.assertEqual(raw[0]["grants"]["tools"], ["run_shell"])
        reread = self.cron._read_jobs()[0]
        self.assertEqual(reread.grants["tools"], ["run_shell"])
        self.assertEqual(reread.grants["risk"], ["destructive"])

    def test_unknown_risk_names_are_dropped_not_fatal(self):
        job = self.cron.add_job("1h", "x", grants={"risk": ["read", "teleport"]})
        self.assertEqual(job.grants["risk"], ["read"])

    def test_risk_names_are_case_insensitive(self):
        job = self.cron.add_job("1h", "x", grants={"risk": ["READ", "Write"]})
        self.assertEqual(job.grants["risk"], ["read", "write"])

    def test_legacy_job_without_grants_gets_the_default(self):
        """jobs.json written before grants existed must not crash and must
        not gain wider capability."""
        self.cron.add_job("1h", "legacy")
        raw = self.jobs_json()
        del raw[0]["grants"]
        self.cron.jobs_path().write_text(json.dumps(raw), encoding="utf-8")
        reread = self.cron._read_jobs()[0]
        self.assertEqual(reread.grants, {"tools": [], "risk": ["read", "write"]})

    def test_corrupt_grants_degrade_to_default_never_crash(self):
        for corrupt in ("just-a-string", 42, ["a", "list"], None):
            with self.subTest(corrupt=corrupt):
                job = self.cron.Job(
                    id="jobX", schedule="1h", prompt="x", grants=corrupt
                )
                self.assertEqual(job.grants, {"tools": [], "risk": ["read", "write"]})

    def test_corrupt_grant_values_inside_the_dict_are_ignored(self):
        job = self.cron.Job(
            id="jobX",
            schedule="1h",
            prompt="x",
            grants={"tools": "run_shell", "risk": {"read": True}, "extra": object()},
        )
        self.assertEqual(job.grants, {"tools": [], "risk": []})

    def test_explicitly_empty_grants_stay_empty_fail_closed(self):
        """An explicit {} is a deliberate deny-all, not a silent default."""
        job = self.cron.add_job("1h", "x", grants={})
        self.assertEqual(job.grants, {"tools": [], "risk": []})

    def test_unknown_jobs_json_keys_still_ignored(self):
        self.cron.add_job("1h", "x")
        raw = self.jobs_json()
        raw[0]["future_field"] = "from-a-newer-version"
        self.cron.jobs_path().write_text(json.dumps(raw), encoding="utf-8")
        reread = self.cron._read_jobs()[0]
        self.assertEqual(reread.grants, {"tools": [], "risk": ["read", "write"]})


class GrantRuntimeTests(CronGrantsBase):
    def _run_with_policy(self, job, calls):
        """Drive tool calls through a real executor + the job's grant policy,
        the way _run_agent does, without a provider."""
        executor = self.tools.ToolExecutor(
            "cron:job1", profile="full", workspace=str(self.workspace)
        )
        ran: list[str] = []
        real_dispatch = executor._dispatch

        def spy(name, args):
            ran.append(name)
            if name == "run_shell":
                return "shell-output"
            return real_dispatch(name, args)

        executor._dispatch = spy  # type: ignore[method-assign]
        policy = self.tools.GrantApprovalPolicy.from_job(job)
        executor.approval_policy = policy
        results = [executor.run(name, args) for name, args in calls]
        return results, ran, policy

    def test_job_with_shell_grant_runs_without_prompt(self):
        job = self.cron.add_job("1h", "x", grants={"tools": ["run_shell"]})
        results, ran, policy = self._run_with_policy(
            job, [("run_shell", {"command": "echo hi"})]
        )
        self.assertEqual(results, ["shell-output"])
        self.assertEqual(ran, ["run_shell"])
        self.assertEqual(policy.denials, [])

    def test_job_without_grant_gets_a_loud_denial(self):
        job = self.cron.add_job("1h", "x")
        results, ran, policy = self._run_with_policy(
            job, [("run_shell", {"command": "rm -rf /"})]
        )
        self.assertIn("was not approved", results[0])
        self.assertNotIn("run_shell", ran)
        self.assertEqual(len(policy.denials), 1)

    def test_run_agent_installs_a_grant_policy_not_an_interactive_one(self):
        """The scheduler must never hand a cron turn the interactive policy
        (which would block on a picker nobody can tap)."""
        job = self.cron.add_job("1h", "x", grants={"tools": ["run_shell"]})
        seen: dict = {}

        class Sessions:
            def send(self, *, identity, text, tool_profile, system_extra="",
                     approval_policy=None):
                seen["policy"] = approval_policy
                return "done"

        scheduler = self.cron.Scheduler(Sessions(), tick_seconds=60)
        text, denials = scheduler._run_agent(job)
        self.assertEqual(text, "done")
        self.assertIsInstance(seen["policy"], self.tools.GrantApprovalPolicy)
        self.assertNotIsInstance(seen["policy"], self.tools.InteractiveApprovalPolicy)
        self.assertEqual(denials, [])

    def test_denials_land_in_the_job_status_loudly(self):
        """Deny-loud: the operator sees WHAT was blocked in `cron list`,
        not a mysteriously empty run."""
        job = self.cron.add_job("1h", "x")  # default grants: no shell
        tools_module = self.tools
        workspace = str(self.workspace)

        class Sessions:
            def send(self, *, identity, text, tool_profile, system_extra="",
                     approval_policy=None):
                # Simulate the agent turn attempting a shell call.
                executor = tools_module.ToolExecutor(
                    identity, profile="full", workspace=workspace
                )
                executor.approval_policy = approval_policy
                executor._dispatch = lambda name, args: "never"  # type: ignore[method-assign]
                result = executor.run("run_shell", {"command": "rm -rf /"})
                return f"turn tried shell: {result[:40]}"

        scheduler = self.cron.Scheduler(Sessions(), tick_seconds=60)
        with mock.patch.object(self.cron, "deliver", return_value=(True, "local")):
            scheduler._execute(job, moment=0.0)
        updated = self.cron.find_job(job.id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertIn("DENIED by job grants", updated.last_status)
        self.assertIn("run_shell", updated.last_status)

    def test_no_denials_means_no_denial_noise_in_status(self):
        job = self.cron.add_job("1h", "x", grants={"tools": ["run_shell"]})

        class Sessions:
            def send(self, **kwargs):
                return "all good"

        scheduler = self.cron.Scheduler(Sessions(), tick_seconds=60)
        with mock.patch.object(self.cron, "deliver", return_value=(True, "local")):
            scheduler._execute(job, moment=0.0)
        updated = self.cron.find_job(job.id)
        assert updated is not None
        self.assertNotIn("DENIED", updated.last_status)


class GrantCreationFlowTests(CronGrantsBase):
    def _executor(self):
        executor = self.tools.ToolExecutor(
            "telegram:4242", profile="full", workspace=str(self.workspace)
        )
        # Flow internal tool diuji di sini; gate dilewati seperti di produksi.
        executor.approval_policy = _AllowAllPolicy()
        return executor

    def test_approve_arms_the_job_with_its_grants(self):
        executor = self._executor()
        with mock.patch(
            "zeline.interaction.ask", return_value="Allow"
        ) as ask:
            result = executor.run(
                "schedule_task",
                {
                    "action": "add",
                    "schedule": "1h",
                    "prompt": "nightly check",
                    "grants": {"tools": ["run_shell"]},
                },
            )
        self.assertIn("Created job1", result)
        ask.assert_called_once()
        question = ask.call_args[0][1]
        self.assertIn("UNATTENDED", question)
        self.assertIn("run_shell", question)
        job = self.cron.find_job("job1")
        assert job is not None
        self.assertTrue(job.enabled)
        self.assertEqual(job.grants["tools"], ["run_shell"])

    def test_deny_discards_the_job_entirely(self):
        """Deny = no job, not a paused zombie entry."""
        executor = self._executor()
        with mock.patch("zeline.interaction.ask", return_value="Deny"):
            result = executor.run(
                "schedule_task", {"action": "add", "schedule": "1h", "prompt": "x"}
            )
        self.assertIn("Not created", result)
        self.assertIn("discarded", result)
        self.assertEqual(self.cron.list_jobs(), [])

    def test_timeout_is_deny_not_assumed_consent(self):
        executor = self._executor()
        with mock.patch(
            "zeline.interaction.ask",
            return_value="NO ANSWER: the user did not reply within 180s.",
        ):
            result = executor.run(
                "schedule_task", {"action": "add", "schedule": "1h", "prompt": "x"}
            )
        self.assertIn("Not created", result)
        self.assertEqual(self.cron.list_jobs(), [])

    def test_a_failed_ask_is_not_reported_as_a_denial(self):
        """If the approval question itself cannot be asked, the job is still
        discarded (fail closed) — but the message says so honestly."""
        executor = self._executor()
        with mock.patch(
            "zeline.interaction.ask", side_effect=RuntimeError("render blew up")
        ):
            result = executor.run(
                "schedule_task", {"action": "add", "schedule": "1h", "prompt": "x"}
            )
        self.assertIn("Not created", result)
        self.assertIn("could not be asked", result)
        self.assertNotIn("was denied", result)
        self.assertEqual(self.cron.list_jobs(), [])

    def test_default_grants_are_shown_when_nothing_declared(self):
        executor = self._executor()
        with mock.patch(
            "zeline.interaction.ask", return_value="Allow"
        ) as ask:
            executor.run(
                "schedule_task", {"action": "add", "schedule": "1h", "prompt": "x"}
            )
        question = ask.call_args[0][1]
        self.assertIn("read, write", question)

    def test_show_and_list_render_capabilities(self):
        executor = self._executor()
        with mock.patch("zeline.interaction.ask", return_value="Allow"):
            executor.run(
                "schedule_task",
                {
                    "action": "add",
                    "schedule": "1h",
                    "prompt": "x",
                    "grants": {"tools": ["run_shell"], "risk": ["network"]},
                },
            )
        listing = executor.run("schedule_task", {"action": "list"})
        self.assertIn("run_shell", listing)
        shown = executor.run("schedule_task", {"action": "show", "job_id": "job1"})
        self.assertIn("run_shell", shown)
        self.assertIn("network", shown)


if __name__ == "__main__":
    unittest.main()
