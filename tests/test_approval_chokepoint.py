"""Contract tests for the approval choke point.

Every model-requested tool call — serial branch, parallel branch, reflect(),
sub-agents, cron turns — must pass through the single gate in
``ToolExecutor.run()``, driven by the policy installed for the turn's
context. These tests pin that:

- a Destructive tool requested via reflect() is gated (the old bypass);
- tools invoked from pool threads (the parallel branch) are gated too;
- the gate asks through the ask_user tool with picker rendering intact;
- ask_user itself never re-enters the gate (no infinite regress);
- the verdict contract holds: "allow"/"allow once" = once,
  "allow_session"/"allow sesi ini" = Worker-B session cache, else deny;
- timeout/no-answer degrades to deny, never to approval;
- a policy that raises fails closed.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh_modules(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    tools = importlib.import_module("zeline.tools")
    approvals = importlib.import_module("zeline.approvals")
    interaction = importlib.import_module("zeline.interaction")
    return tools, approvals, interaction


class ChokePointBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved = os.environ.get("ZELINE_HOME")
        self.tools, self.approvals, self.interaction = fresh_modules(self.home)
        self.workspace = self.home / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.executor = self.tools.ToolExecutor(
            "cli:test", profile="full", workspace=str(self.workspace)
        )
        # Spy on real dispatch without running anything dangerous.
        self.dispatched: list[str] = []
        real_dispatch = self.executor._dispatch

        def spy(name, args):
            self.dispatched.append(name)
            if name == "ask_user":
                return self.ask_answer
            if name == "run_shell":
                return "shell-output"
            return real_dispatch(name, args)

        self.ask_answer = "Allow"
        self.executor._dispatch = spy  # type: ignore[method-assign]

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)


class RecordingPolicy:
    """A policy that records every decision request and answers on script."""

    def __init__(self, script=(), on_tool=None):
        self.calls: list[tuple[str, dict]] = []
        self.script = list(script)
        self.on_tool = on_tool
        self.rendered: list[tuple[str, dict]] = []
        if on_tool is not None:
            inner = on_tool

            def hooked(name, args):
                self.rendered.append((name, args))
                return inner(name, args)

            self.on_tool = hooked

    def decide(self, executor, name, args):
        self.calls.append((name, dict(args)))
        if self.script:
            return self.script.pop(0)
        return "deny"


class GateSemanticsTests(ChokePointBase):
    def test_no_policy_denies_mutating_tools_fail_closed(self):
        """Tanpa policy yang terpasang, tool mutasi di-DENY (fail closed).

        Ini PERUBAHAN perilaku yang disengaja (verdict owner, hardening):
        absennya policy menandakan jalur kode yang tidak seharusnya terjadi
        di produksi (mis. reflect() tanpa send()), jadi respons amannya
        adalah menolak — bukan mengizinkan diam-diam seperti dulu.
        """
        self.assertIsNone(self.executor.approval_policy)
        # Destructive ...
        result = self.executor.run("run_shell", {"command": "echo hi"})
        self.assertTrue(result.startswith("ERROR: tool 'run_shell' was not approved"))
        self.assertNotIn("run_shell", self.dispatched)
        # ... Write juga (skenario reflect(): manage_skill/add_memory) ...
        result = self.executor.run("add_memory", {"text": "x"})
        self.assertTrue(result.startswith("ERROR: tool 'add_memory' was not approved"))
        self.assertNotIn("add_memory", self.dispatched)
        # ... dan Network.
        result = self.executor.run("gmail_send", {"to": "a@b.c", "body": "x"})
        self.assertTrue(result.startswith("ERROR: tool 'gmail_send' was not approved"))
        self.assertNotIn("gmail_send", self.dispatched)

    def test_no_policy_still_allows_pure_reads(self):
        """Fallback tanpa policy tetap mengizinkan read murni (introspeksi)."""
        self.assertIsNone(self.executor.approval_policy)
        result = self.executor.run("list_memory", {})
        # list_memory adalah Read: tidak di-deny oleh fallback.
        self.assertFalse(str(result).startswith("ERROR: tool 'list_memory' was not approved"))

    def test_deny_never_reaches_dispatch(self):
        self.executor.approval_policy = RecordingPolicy(["deny"])
        result = self.executor.run("run_shell", {"command": "rm -rf /"})
        self.assertTrue(result.startswith("ERROR: tool 'run_shell' was not approved"))
        self.assertNotIn("run_shell", self.dispatched)
        self.assertEqual(
            self.executor.approval_policy.calls, [("run_shell", {"command": "rm -rf /"})]
        )

    def test_allow_once_runs_exactly_once(self):
        policy = RecordingPolicy(["allow", "deny"])
        self.executor.approval_policy = policy
        self.assertEqual(
            self.executor.run("run_shell", {"command": "echo hi"}), "shell-output"
        )
        # "allow" is single-use: the second call asks again and is denied.
        result = self.executor.run("run_shell", {"command": "echo hi"})
        self.assertIn("was not approved", result)
        self.assertEqual(len(policy.calls), 2)

    def test_no_answer_is_deny_not_assumed_consent(self):
        self.executor.approval_policy = RecordingPolicy(
            ["NO ANSWER: the user did not reply within 180s. Proceed."]
        )
        result = self.executor.run("run_shell", {"command": "rm -rf /"})
        self.assertIn("was not approved", result)
        self.assertNotIn("run_shell", self.dispatched)

    def test_cancelled_is_deny(self):
        self.executor.approval_policy = RecordingPolicy(
            ["CANCELLED: the user cancelled this question."]
        )
        result = self.executor.run("run_shell", {"command": "rm -rf /"})
        self.assertIn("was not approved", result)

    def test_garbage_verdict_is_deny(self):
        self.executor.approval_policy = RecordingPolicy(["maybe"])
        result = self.executor.run("run_shell", {"command": "rm -rf /"})
        self.assertIn("was not approved", result)

    def test_a_raising_policy_fails_closed(self):
        class Broken:
            on_tool = None

            def decide(self, executor, name, args):
                raise RuntimeError("policy bug")

        self.executor.approval_policy = Broken()
        result = self.executor.run("run_shell", {"command": "rm -rf /"})
        self.assertIn("was not approved", result)
        self.assertNotIn("run_shell", self.dispatched)

    def test_session_verdict_is_recorded_in_worker_b_cache(self):
        """The gate's half of the verdict contract: a "session" verdict from
        any policy is recorded in Worker B's session cache (the consulting
        half lives in InteractiveApprovalPolicy, pinned below)."""
        policy = RecordingPolicy(["Allow sesi ini"])
        self.executor.approval_policy = policy
        result = self.executor.run("run_shell", {"command": "echo one"})
        self.assertEqual(result, "shell-output")
        self.assertTrue(self.approvals.session_allowed("cli:test", "run_shell"))
        self.approvals.clear_session_allows("cli:test")

    def test_gate_is_thread_safe_for_the_parallel_branch(self):
        """The parallel tool branch runs executor.run() on pool threads; the
        gate (and its reentrancy guard) must behave per-thread."""
        policy = RecordingPolicy(["deny"] * 20)
        self.executor.approval_policy = policy
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(
                pool.map(
                    lambda i: self.executor.run("run_shell", {"command": f"echo {i}"}),
                    range(10),
                )
            )
        self.assertTrue(all("was not approved" in r for r in results))
        self.assertNotIn("run_shell", self.dispatched)
        self.assertEqual(len(policy.calls), 10)


class InteractivePolicyTests(ChokePointBase):
    def test_destructive_tool_asks_through_ask_user_with_picker_rendering(self):
        """The full interactive flow: approval_question decides, the picker
        renders via on_tool, the raw verdict drives the outcome."""
        rendered: list[tuple[str, dict]] = []
        policy = self.tools.InteractiveApprovalPolicy(
            on_tool=lambda name, args: rendered.append((name, args))
        )
        self.executor.approval_policy = policy
        self.ask_answer = "Allow"
        result = self.executor.run("run_shell", {"command": "ls"})
        self.assertEqual(result, "shell-output")
        # Picker rendering happened exactly like a model-initiated ask_user.
        self.assertEqual(len(rendered), 1)
        name, args = rendered[0]
        self.assertEqual(name, "ask_user")
        self.assertIn("run_shell", args["question"])
        self.assertEqual(list(args["options"]), list(self.approvals.APPROVAL_OPTIONS))
        self.assertIn("ask_user", self.dispatched)

    def test_read_tool_never_asks(self):
        policy = self.tools.InteractiveApprovalPolicy(on_tool=mock.Mock())
        self.executor.approval_policy = policy
        result = self.executor.run("read_file", {"path": "notes.txt"})
        self.assertNotIn("was not approved", result)
        self.assertNotIn("ask_user", self.dispatched)
        policy.on_tool.assert_not_called()

    def test_ask_user_itself_never_reenters_the_gate(self):
        """The approval machinery calls run("ask_user") from inside decide();
        without the reentrancy guard that would recurse forever. decide()
        must be entered exactly once for the outer call."""
        entered: list[str] = []

        class CountingPolicy(self.tools.InteractiveApprovalPolicy):
            def decide(self, executor, name, args):
                entered.append(name)
                return super().decide(executor, name, args)

        self.executor.approval_policy = CountingPolicy(on_tool=lambda n, a: None)
        self.ask_answer = "Allow"
        result = self.executor.run("run_shell", {"command": "ls"})
        self.assertEqual(result, "shell-output")
        self.assertEqual(entered, ["run_shell"])

    def test_deny_returns_the_legacy_denial_text(self):
        policy = self.tools.InteractiveApprovalPolicy(on_tool=lambda n, a: None)
        self.executor.approval_policy = policy
        self.ask_answer = "Deny"
        result = self.executor.run("run_shell", {"command": "ls"})
        self.assertEqual(
            result,
            "ERROR: tool 'run_shell' was not approved by the "
            "operator and was not executed. Either ask the "
            "operator what to do, or use a safer tool.",
        )
        self.assertNotIn("run_shell", [d for d in self.dispatched if d != "ask_user"])

    def test_session_verdict_grants_without_reasking(self):
        policy = self.tools.InteractiveApprovalPolicy(on_tool=lambda n, a: None)
        self.executor.approval_policy = policy
        self.ask_answer = "Allow sesi ini"
        self.executor.run("run_shell", {"command": "echo one"})
        self.ask_answer = "Deny"  # would deny if asked again
        result = self.executor.run("run_shell", {"command": "echo two"})
        self.assertEqual(result, "shell-output")
        # The cache is keyed by tool name: a different tool still asks.
        denied = self.executor.run("browser", {"action": "open", "url": "x"})
        self.assertIn("was not approved", denied)
        self.approvals.clear_session_allows("cli:test")


class GrantPolicyTests(ChokePointBase):
    def _grant_executor(self, **kwargs):
        policy = self.tools.GrantApprovalPolicy(**kwargs)
        self.executor.approval_policy = policy
        return policy

    def test_default_grants_are_read_plus_workspace_write(self):
        policy = self._grant_executor()
        self.assertEqual(policy.granted_risks, {"read", "write"})
        self.assertEqual(policy.granted_tools, frozenset())

    def test_granted_tool_name_runs_without_prompt(self):
        self._grant_executor(tools=["run_shell"])
        result = self.executor.run("run_shell", {"command": "echo hi"})
        self.assertEqual(result, "shell-output")

    def test_ungranted_destructive_tool_is_denied_loudly(self):
        policy = self._grant_executor()
        result = self.executor.run("run_shell", {"command": "rm -rf /"})
        self.assertIn("was not approved", result)
        self.assertNotIn("run_shell", self.dispatched)
        self.assertEqual(len(policy.denials), 1)
        name, reason = policy.denials[0]
        self.assertEqual(name, "run_shell")
        self.assertIn("destructive", reason)

    def test_write_inside_workspace_allowed_write_outside_denied(self):
        policy = self._grant_executor()
        ok = self.executor.run("write_file", {"path": "notes/todo.txt", "content": "x"})
        self.assertNotIn("was not approved", ok)
        bad = self.executor.run("write_file", {"path": "/tmp/evil.txt", "content": "x"})
        self.assertIn("was not approved", bad)
        self.assertIn("workspace", policy.denials[-1][1])

    def test_unknown_tool_needs_an_explicit_name_grant(self):
        policy = self._grant_executor()
        result = self.executor.run("mcp__ghost__tool", {})
        self.assertIn("was not approved", result)
        self.assertIn("by name", policy.denials[0][1])
        # ...but an explicit name grant allowlists it (fail closed, escapable).
        policy2 = self._grant_executor(tools=["mcp__ghost__tool"])
        self.executor._dispatch = lambda name, args: "ghost-ok"
        self.assertEqual(self.executor.run("mcp__ghost__tool", {}), "ghost-ok")

    def test_grant_policy_never_consults_the_interactive_session_cache(self):
        """A leftover 'allow sesi ini' from a chat must not widen an
        unattended run: the grant policy decides from grants alone."""
        self.approvals.grant_session_allow("cli:test", "run_shell")
        try:
            policy = self._grant_executor()
            result = self.executor.run("run_shell", {"command": "rm -rf /"})
            self.assertIn("was not approved", result)
            self.assertEqual(len(policy.denials), 1)
        finally:
            self.approvals.clear_session_allows("cli:test")

    def test_grant_policy_never_asks_the_operator(self):
        """No picker, no blocking wait at 3 AM: decide() is pure."""
        policy = self._grant_executor()
        with mock.patch.object(
            self.executor, "ask_operator", side_effect=AssertionError("must not ask")
        ):
            result = self.executor.run("run_shell", {"command": "rm -rf /"})
        self.assertIn("was not approved", result)

    def test_grant_policy_denies_ask_user_fast_without_waiting(self):
        """Nobody watches a cron run: asking would block the worker on the
        ask timeout and then deny anyway. The grant policy denies at once."""
        policy = self._grant_executor()
        with mock.patch.object(
            self.executor, "_dispatch", side_effect=AssertionError("must not dispatch")
        ):
            result = self.executor.run("ask_user", {"question": "x?"})
        self.assertIn("was not approved", result)
        self.assertEqual(policy.denials[0][0], "ask_user")
        self.assertIn("nobody can answer", policy.denials[0][1])

    def test_explicit_name_grant_still_wins_over_the_ask_user_rule(self):
        """Grants are literal: an explicit ask_user name grant is honored."""
        policy = self._grant_executor(tools=["ask_user"])
        self.executor._dispatch = lambda name, args: "asked"  # type: ignore[method-assign]
        self.assertEqual(self.executor.run("ask_user", {"question": "x?"}), "asked")
        self.assertEqual(policy.denials, [])

    def test_from_job_snapshots_grants(self):
        job = mock.Mock()
        job.grants = {"tools": ["run_shell"], "risk": ["read"]}
        policy = self.tools.GrantApprovalPolicy.from_job(job)
        job.grants = {"tools": [], "risk": []}  # edited mid-run: must not matter
        self.assertIn("run_shell", policy.granted_tools)
        self.executor.approval_policy = policy
        self.assertEqual(
            self.executor.run("run_shell", {"command": "echo hi"}), "shell-output"
        )


class FakeProviderResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        import json as _json

        self.text = _json.dumps(payload)
        self.status_code = status_code
        self.ok = status_code < 400
        self.encoding = "utf-8"


class SendInstallsPolicyTests(unittest.TestCase):
    """End-to-end through a real send() turn: the turn installs the
    interactive policy, so a destructive tool the model requests goes through
    the ask_user picker — and a denial blocks the tool with the legacy text."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = {
            key: os.environ.get(key)
            for key in ("ZELINE_HOME", "ZELINE_API_KEY", "ZELINE_BASE_URL", "ZELINE_MODEL")
        }
        os.environ["ZELINE_HOME"] = str(Path(self._tmp.name) / "state")
        os.environ["ZELINE_API_KEY"] = "test-key"
        os.environ["ZELINE_BASE_URL"] = "http://provider.test/v1"
        os.environ["ZELINE_MODEL"] = "test-model"
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.agent_module = importlib.import_module("zeline.agent")
        self.agent_module.config.STREAM_RESPONSES = False
        self.workspace = Path(self._tmp.name) / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def _agent(self):
        agent = self.agent_module.Zeline(
            identity="cli:e2e", tool_profile="full", workspace=str(self.workspace)
        )
        real_dispatch = agent.executor._dispatch

        def spy(name, args):
            if name == "run_shell":
                return "shell-output"
            return real_dispatch(name, args)

        agent.executor._dispatch = spy  # type: ignore[method-assign]
        return agent

    @staticmethod
    def _tool_turn(name: str, arguments: str) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    }
                }
            ]
        }

    def _run_turn(self, answer: str):
        agent = self._agent()
        rendered: list[str] = []
        results: list[tuple[str, str]] = []
        done = {"choices": [{"message": {"role": "assistant", "content": "beres"}}]}
        with mock.patch.object(
            self.agent_module.requests,
            "post",
            side_effect=[
                FakeProviderResponse(self._tool_turn("run_shell", '{"command":"echo hi"}')),
                FakeProviderResponse(done),
            ],
        ), mock.patch("zeline.interaction.ask", return_value=answer) as ask:
            agent.send(
                "jalankan shell",
                on_tool=lambda name, args: rendered.append(name),
                on_tool_result=lambda name, args, result: results.append((name, result)),
            )
        return agent, rendered, results, ask

    def test_destructive_tool_in_a_real_turn_asks_and_runs_on_allow(self):
        agent, rendered, results, ask = self._run_turn("Allow")
        # The policy was installed by send(): the picker rendered through the
        # ask_user tool exactly as before the choke-point move.
        self.assertIn("ask_user", rendered)
        self.assertIn(("run_shell", "shell-output"), results)
        ask.assert_called_once()
        self.assertIsInstance(
            agent.executor.approval_policy, self.agent_module.InteractiveApprovalPolicy
        )

    def test_deny_in_a_real_turn_blocks_the_tool_with_legacy_text(self):
        _agent, _rendered, results, _ask = self._run_turn("Deny")
        run_shell_results = [r for n, r in results if n == "run_shell"]
        self.assertEqual(len(run_shell_results), 1)
        self.assertEqual(
            run_shell_results[0],
            "ERROR: tool 'run_shell' was not approved by the "
            "operator and was not executed. Either ask the "
            "operator what to do, or use a safer tool.",
        )

    def test_parallel_branch_tools_pass_through_the_gate(self):
        """Two read-only tools in one round take the parallel branch; the
        gate still sees each call (here: allowed without asking)."""
        agent = self._agent()
        rendered: list[str] = []
        results: list[tuple[str, str]] = []
        calls = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-a",
                                "type": "function",
                                "function": {"name": "runtime_info", "arguments": "{}"},
                            },
                            {
                                "id": "call-b",
                                "type": "function",
                                "function": {"name": "list_memory", "arguments": "{}"},
                            },
                        ],
                    }
                }
            ]
        }
        done = {"choices": [{"message": {"role": "assistant", "content": "beres"}}]}
        with mock.patch.object(
            self.agent_module.requests,
            "post",
            side_effect=[FakeProviderResponse(calls), FakeProviderResponse(done)],
        ), mock.patch("zeline.interaction.ask") as ask:
            agent.send(
                "cek dua hal",
                on_tool=lambda name, args: rendered.append(name),
                on_tool_result=lambda name, args, result: results.append((name, result)),
            )
        self.assertEqual(sorted(n for n, _ in results), ["list_memory", "runtime_info"])
        ask.assert_not_called()  # read-only: no question asked
        self.assertNotIn("ask_user", rendered)


if __name__ == "__main__":
    unittest.main()
