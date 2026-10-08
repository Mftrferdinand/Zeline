"""Regression tests for the spawn_worker privilege-escalation fix.

Before the fix, ``spawn_worker`` was a Write-class tool whose ``grants``
argument was never shown to the operator and never actually approved:
``approval_question("spawn_worker", {"grants": {"tools": ["run_shell"],
"risk": ["destructive"]}})`` returned ``None``, so the interactive policy
silently allowed it, and a cron job with default grants could spawn a
destructive worker with no prompt at all.

These tests pin the corrected behavior:

- ``spawn_worker`` is Install-class: the interactive policy always asks,
  and the question shows the exact normalized worker grant declaration.
- Deny blocks the spawn (no worker starts); allow runs it with exactly
  the declared grants.
- "Allow sesi ini" is scoped to the identical grant declaration — a
  broader declaration is asked again.
- A grant-policy context (cron job) spawns without a prompt only when the
  spawn itself is inside its pre-approved grants (cron pre-authorization
  keeps working), and the worker's grants can never exceed the caller's
  grants (loud reject, not silent trim).
- No policy, or an unauditable policy, fails closed.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

DESTRUCTIVE_GRANTS = {"tools": ["run_shell"], "risk": ["destructive"]}


def fresh_modules(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    tools = importlib.import_module("zeline.tools")
    approvals = importlib.import_module("zeline.approvals")
    supervisor = importlib.import_module("zeline.supervisor")
    return tools, approvals, supervisor


class SpawnWorkerGrantsBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved = os.environ.get("ZELINE_HOME")
        self.tools, self.approvals, self.supervisor_mod = fresh_modules(self.home)
        self.workspace = self.home / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.executor = self.tools.ToolExecutor(
            "cli:test", profile="full", workspace=str(self.workspace)
        )
        # Spy on dispatch: answer the operator picker ourselves, never run
        # anything dangerous, and count every ask_user call.
        self.ask_answer = "Deny"
        self.ask_calls: list[dict] = []
        real_dispatch = self.executor._dispatch

        def spy(name, args):
            if name == "ask_user":
                self.ask_calls.append(dict(args))
                return self.ask_answer
            return real_dispatch(name, args)

        self.executor._dispatch = spy  # type: ignore[method-assign]
        # Record spawn_worker calls without starting real worker threads.
        self.spawned: list[dict] = []
        pool = self.supervisor_mod.get_supervisor("cli:test")

        def fake_spawn(task, *, grants=None, accept_if="", depends_on=None, **kwargs):
            self.spawned.append(
                {"task": task, "grants": grants, "accept_if": accept_if}
            )
            return "w_test1"

        self._real_spawn = pool.spawn
        pool.spawn = fake_spawn  # type: ignore[method-assign]
        self._pool = pool

    def tearDown(self) -> None:
        self._pool.spawn = self._real_spawn  # type: ignore[method-assign]
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def install_interactive(self):
        self.executor.approval_policy = self.tools.InteractiveApprovalPolicy()

    def normalized(self, grants):
        return self.supervisor_mod.Supervisor._normalize_grants(grants)


class TestApprovalQuestionShowsGrants(SpawnWorkerGrantsBase):
    def test_spawn_worker_always_asks(self):
        q = self.executor.approval_question(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        self.assertIsNotNone(q, "spawn_worker must ask (Install-class)")

    def test_question_shows_exact_grant_declaration(self):
        q = self.executor.approval_question(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        assert q is not None
        self.assertIn("run_shell", q)
        self.assertIn("destructive", q)
        self.assertIn("Worker grants", q)
        self.assertIn("can never ask", q)

    def test_question_without_grants_shows_read_only_default(self):
        q = self.executor.approval_question("spawn_worker", {"task": "riset"})
        assert q is not None
        self.assertIn("read", q)
        self.assertIn("(none by name)", q)

    def test_grants_key_canonicalizes_declarations(self):
        key = self.tools._spawn_grants_key
        self.assertEqual(
            key("spawn_worker", {"grants": {"risk": ["destructive"]}}),
            key("spawn_worker", {"grants": {"risk": ["destructive", "destructive"]}}),
        )
        self.assertNotEqual(
            key("spawn_worker", {"grants": {"risk": ["destructive"]}}),
            key("spawn_worker", {"grants": {"risk": ["read"]}}),
        )
        self.assertNotEqual(
            key("spawn_worker", {"grants": {"risk": ["destructive"]}}),
            key("spawn_worker", {"grants": {"tools": ["run_shell"], "risk": ["destructive"]}}),
        )
        # Other tools are untouched: empty discriminator, old behavior.
        self.assertEqual(key("run_shell", {"command": "x"}), "")
        self.assertEqual(key("run_shell", {"command": "y"}), "")


class TestInteractivePolicy(SpawnWorkerGrantsBase):
    """Probe 1 & 2: without approval -> asked/denied; with approval -> runs."""

    def test_deny_blocks_spawn_no_worker_starts(self):
        self.install_interactive()
        self.ask_answer = "Deny"
        result = self.executor.run(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        self.assertIn("was not approved", result)
        self.assertEqual(self.spawned, [])
        self.assertEqual(len(self.ask_calls), 1)

    def test_allow_runs_worker_with_declared_grants(self):
        self.install_interactive()
        self.ask_answer = "Allow"
        result = self.executor.run(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        self.assertIn("w_test1", result)
        self.assertEqual(len(self.spawned), 1)
        declared = self.spawned[0]["grants"]
        self.assertEqual(self.normalized(declared)["tools"], ["run_shell"])
        self.assertEqual(self.normalized(declared)["risk"], ["destructive"])

    def test_session_allow_scoped_to_identical_grants(self):
        self.install_interactive()
        self.ask_answer = "Allow sesi ini"
        g_read = {"risk": ["read"]}
        # First spawn: asked once, then cached for this declaration.
        self.executor.run("spawn_worker", {"task": "a", "grants": dict(g_read)})
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(len(self.ask_calls), 1)
        # Identical declaration: fast-path, no new question.
        self.executor.run("spawn_worker", {"task": "b", "grants": dict(g_read)})
        self.assertEqual(len(self.spawned), 2)
        self.assertEqual(len(self.ask_calls), 1)
        # Broader declaration: MUST ask again — the old session allow for a
        # read-only spawn must never cover a destructive one.
        self.executor.run(
            "spawn_worker", {"task": "c", "grants": dict(DESTRUCTIVE_GRANTS)}
        )
        self.assertEqual(len(self.spawned), 3)
        self.assertEqual(len(self.ask_calls), 2)
        # The read-only declaration is still cached from the first approval.
        self.executor.run("spawn_worker", {"task": "d", "grants": dict(g_read)})
        self.assertEqual(len(self.spawned), 4)
        self.assertEqual(len(self.ask_calls), 2)


class TestGrantPolicyContext(SpawnWorkerGrantsBase):
    """Probe 3: cron pre-authorization keeps working."""

    def test_cron_policy_spawns_within_grants_without_prompt(self):
        policy = self.tools.GrantApprovalPolicy(
            tools=["spawn_worker"], risk_classes=["read", "write"]
        )
        self.executor.approval_policy = policy
        result = self.executor.run("spawn_worker", {"task": "laporan"})
        self.assertIn("w_test1", result)
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(self.ask_calls, [], "no prompt in a grant context")
        # Default worker grants are read-only, inside the job's read+write.
        self.assertEqual(
            self.normalized(self.spawned[0]["grants"]),
            {"tools": [], "risk": ["read"]},
        )

    def test_cron_policy_with_install_risk_spawns_without_prompt(self):
        policy = self.tools.GrantApprovalPolicy(
            tools=[], risk_classes=["read", "write", "install"]
        )
        self.executor.approval_policy = policy
        result = self.executor.run("spawn_worker", {"task": "laporan"})
        self.assertIn("w_test1", result)
        self.assertEqual(self.ask_calls, [])

    def test_cron_default_grants_deny_spawn_worker(self):
        # The audit's exploit chain: default read+write job grants must NOT
        # be able to spawn a worker (Install-class is not granted).
        policy = self.tools.GrantApprovalPolicy()
        self.executor.approval_policy = policy
        result = self.executor.run(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        self.assertIn("was not approved", result)
        self.assertEqual(self.spawned, [])
        self.assertTrue(
            any(name == "spawn_worker" for name, _ in policy.denials),
            "denial must be recorded loudly on the grant policy",
        )


class TestCallerGrantCap(SpawnWorkerGrantsBase):
    """Probe 4: worker grants can never exceed the caller's grants."""

    def _rich_cron_policy(self):
        return self.tools.GrantApprovalPolicy(
            tools=["spawn_worker"], risk_classes=["read", "write", "install"]
        )

    def test_worker_grants_beyond_caller_are_rejected_loudly(self):
        self.executor.approval_policy = self._rich_cron_policy()
        result = self.executor.run(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        # The spawn itself is inside the job's grants (Install-class), but
        # the WORKER's destructive grants exceed the job's — rejected here.
        self.assertTrue(result.startswith("ERROR: spawn_worker denied"))
        self.assertIn("exceed", result)
        self.assertEqual(self.spawned, [], "no worker may start")

    def test_worker_grants_within_caller_are_allowed(self):
        policy = self.tools.GrantApprovalPolicy(
            tools=["spawn_worker", "run_shell"],
            risk_classes=["read", "write", "destructive"],
        )
        self.executor.approval_policy = policy
        result = self.executor.run(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        self.assertIn("w_test1", result)
        self.assertEqual(len(self.spawned), 1)

    def test_tool_covered_by_caller_risk_class_is_allowed(self):
        # run_shell is Destructive-class; the caller grants the risk class
        # (not the name) — still within the caller's capability.
        policy = self.tools.GrantApprovalPolicy(
            tools=["spawn_worker"], risk_classes=["read", "write", "destructive"]
        )
        self.executor.approval_policy = policy
        result = self.executor.run(
            "spawn_worker",
            {"task": "riset", "grants": {"tools": ["run_shell"]}},
        )
        self.assertIn("w_test1", result)

    def test_empty_grants_declaration_spawns_deny_all_worker(self):
        self.executor.approval_policy = self._rich_cron_policy()
        result = self.executor.run("spawn_worker", {"task": "t", "grants": {}})
        self.assertIn("w_test1", result)
        self.assertEqual(
            self.normalized(self.spawned[0]["grants"]),
            {"tools": [], "risk": []},
        )

    def test_malformed_grants_fall_back_to_read_only(self):
        self.executor.approval_policy = self._rich_cron_policy()
        result = self.executor.run(
            "spawn_worker", {"task": "t", "grants": "oops-not-a-dict"}
        )
        self.assertIn("w_test1", result)
        self.assertEqual(
            self.normalized(self.spawned[0]["grants"]),
            {"tools": [], "risk": ["read"]},
        )

    def test_unknown_risk_class_is_dropped_not_smuggled(self):
        self.executor.approval_policy = self._rich_cron_policy()
        result = self.executor.run(
            "spawn_worker",
            {"task": "t", "grants": {"risk": ["destructive", "root"]}},
        )
        self.assertTrue(result.startswith("ERROR: spawn_worker denied"))
        self.assertIn("destructive", result)
        self.assertEqual(self.spawned, [])


class TestFailClosed(SpawnWorkerGrantsBase):
    def test_no_policy_denies_spawn_worker(self):
        self.assertIsNone(self.executor.approval_policy)
        result = self.executor.run(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        self.assertIn("was not approved", result)
        self.assertEqual(self.spawned, [])

    def test_unknown_policy_kind_denies_spawn_worker(self):
        class MysteryPolicy(self.tools.ApprovalPolicy):
            def decide(self, executor, name, args):
                return "allow"  # claims everything is fine

        self.executor.approval_policy = MysteryPolicy()
        result = self.executor.run(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        self.assertTrue(result.startswith("ERROR: spawn_worker denied"))
        self.assertEqual(self.spawned, [])

    def test_interactive_context_needs_no_grant_cap(self):
        # In an interactive turn the operator's per-call approval IS the cap:
        # destructive grants are fine once explicitly approved.
        self.install_interactive()
        self.ask_answer = "Allow"
        result = self.executor.run(
            "spawn_worker",
            {"task": "riset", "grants": dict(DESTRUCTIVE_GRANTS)},
        )
        self.assertIn("w_test1", result)


if __name__ == "__main__":
    unittest.main()
