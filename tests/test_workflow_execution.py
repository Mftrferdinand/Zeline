"""E2E tests for the workflow execution engine (zeline/workflows.py).

Uses a MockAgent (duck-typed .send) — no model needed.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


class MockAgent:
    """Duck-typed agent: records prompts, returns canned responses."""

    def __init__(self, delay: float = 0.0, fail_on: str | None = None):
        self.prompts: list[str] = []
        self.delay = delay
        self.fail_on = fail_on

    def send(self, text: str) -> str:
        self.prompts.append(text)
        if self.delay:
            time.sleep(self.delay)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("mock agent failure")
        return f"done: {text[:40]}"


def _patch_dirs(testcase):
    """Isolate ~/.zeline/workflows to a temp dir."""
    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    import zeline.workflows as wf
    p = mock.patch.object(wf, "_wf_dir", return_value=Path(tmp.name))
    p.start()
    testcase.addCleanup(p.stop)
    # Clear in-memory registries between tests
    wf._EXECUTIONS.clear()
    wf._EXEC_RUNTIME.clear()
    return wf


def _make_three_node_wf(wf):
    """task -> approval -> task"""
    return wf.save_workflow(
        None, "E2E test",
        [
            {"id": "n1", "type": "task", "label": "First", "prompt": "do step one"},
            {"id": "n2", "type": "approval", "label": "Check", "prompt": "approve step one?"},
            {"id": "n3", "type": "task", "label": "Second", "prompt": "do step two"},
            {"id": "n4", "type": "note", "label": "docs"},
        ],
        [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"},
            {"from": "n3", "to": "n4"},
        ],
    )


def _wait_for(wf, exec_id, statuses, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ex = wf.get_execution(exec_id)
        if ex and ex["status"] in statuses:
            return ex
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {statuses}; last={wf.get_execution(exec_id)}")


class ExecutionTests(unittest.TestCase):
    def test_full_run_with_approval(self):
        wf = _patch_dirs(self)
        wid = _make_three_node_wf(wf)
        agent = MockAgent()
        approvals = []
        exec_id = wf.execute_workflow(
            wid, agent, node_timeout=10, approval_timeout=10,
            on_approval=lambda eid, nid, prompt: approvals.append((eid, nid, prompt)),
        )
        # Task 1 runs, then pauses at approval
        ex = _wait_for(wf, exec_id, {"waiting_approval"})
        self.assertEqual(ex["nodes"]["n1"]["status"], "done")
        self.assertEqual(ex["nodes"]["n2"]["status"], "waiting_approval")
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0][1], "n2")
        # Only first task prompt sent so far
        self.assertEqual(agent.prompts, ["do step one"])

        # Approve -> runs to completion
        self.assertTrue(wf.resume_workflow(exec_id, approved=True))
        ex = _wait_for(wf, exec_id, {"done"})
        self.assertEqual(ex["nodes"]["n2"]["status"], "done")
        self.assertEqual(ex["nodes"]["n3"]["status"], "done")
        self.assertEqual(ex["nodes"]["n4"]["status"], "skipped")  # note
        self.assertEqual(agent.prompts, ["do step one", "do step two"])
        self.assertTrue(ex["ended_at"] > 0)

    def test_approval_denied_cancels(self):
        wf = _patch_dirs(self)
        wid = _make_three_node_wf(wf)
        agent = MockAgent()
        exec_id = wf.execute_workflow(wid, agent, node_timeout=10, approval_timeout=10)
        _wait_for(wf, exec_id, {"waiting_approval"})
        self.assertTrue(wf.resume_workflow(exec_id, approved=False))
        ex = _wait_for(wf, exec_id, {"cancelled"})
        self.assertEqual(ex["nodes"]["n2"]["status"], "cancelled")
        self.assertEqual(ex["nodes"]["n3"]["status"], "pending")  # never ran
        self.assertEqual(agent.prompts, ["do step one"])

    def test_cycle_rejected(self):
        wf = _patch_dirs(self)
        wid = wf.save_workflow(
            None, "Cyclic",
            [{"id": "a", "type": "task", "prompt": "x"},
             {"id": "b", "type": "task", "prompt": "y"}],
            [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
        )
        with self.assertRaises(ValueError) as ctx:
            wf.execute_workflow(wid, MockAgent())
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_task_failure_fails_workflow(self):
        wf = _patch_dirs(self)
        wid = wf.save_workflow(
            None, "Failing",
            [{"id": "n1", "type": "task", "prompt": "boom-task"},
             {"id": "n2", "type": "task", "prompt": "never runs"}],
            [{"from": "n1", "to": "n2"}],
        )
        agent = MockAgent(fail_on="boom-task")
        exec_id = wf.execute_workflow(wid, agent, node_timeout=10)
        ex = _wait_for(wf, exec_id, {"failed"})
        self.assertEqual(ex["nodes"]["n1"]["status"], "failed")
        self.assertIn("mock agent failure", ex["nodes"]["n1"]["error"])
        self.assertEqual(ex["nodes"]["n2"]["status"], "pending")
        self.assertEqual(agent.prompts, ["boom-task"])

    def test_task_timeout_fails_node(self):
        wf = _patch_dirs(self)
        wid = wf.save_workflow(
            None, "Slow",
            [{"id": "n1", "type": "task", "prompt": "slow task"}],
            [],
        )
        agent = MockAgent(delay=30)  # hangs longer than timeout
        exec_id = wf.execute_workflow(wid, agent, node_timeout=1)
        ex = _wait_for(wf, exec_id, {"failed"}, timeout=15)
        self.assertEqual(ex["nodes"]["n1"]["status"], "failed")
        self.assertIn("timed out", ex["nodes"]["n1"]["error"])

    def test_pause_and_resume(self):
        wf = _patch_dirs(self)
        wid = wf.save_workflow(
            None, "Pausable",
            [{"id": "n1", "type": "task", "prompt": "slow one"},
             {"id": "n2", "type": "task", "prompt": "second"}],
            [{"from": "n1", "to": "n2"}],
        )
        agent = MockAgent(delay=2)
        exec_id = wf.execute_workflow(wid, agent, node_timeout=30)
        time.sleep(0.3)  # let n1 start
        self.assertTrue(wf.pause_workflow(exec_id))
        ex = wf.get_execution(exec_id)
        self.assertEqual(ex["status"], "paused")
        # n2 must not start while paused
        time.sleep(2.5)
        ex = wf.get_execution(exec_id)
        self.assertEqual(ex["nodes"]["n2"]["status"], "pending")
        self.assertTrue(wf.resume_workflow(exec_id))
        ex = _wait_for(wf, exec_id, {"done"}, timeout=30)
        self.assertEqual(agent.prompts, ["slow one", "second"])

    def test_unknown_workflow_rejected(self):
        wf = _patch_dirs(self)
        with self.assertRaises(ValueError):
            wf.execute_workflow("nope-missing", MockAgent())

    def test_pause_invalid_state(self):
        wf = _patch_dirs(self)
        self.assertFalse(wf.pause_workflow("exec_missing"))
        self.assertFalse(wf.resume_workflow("exec_missing"))

    def test_list_executions(self):
        wf = _patch_dirs(self)
        wid = _make_three_node_wf(wf)
        agent = MockAgent()
        exec_id = wf.execute_workflow(wid, agent, node_timeout=10, approval_timeout=10)
        _wait_for(wf, exec_id, {"waiting_approval"})
        lst = wf.list_executions(wid)
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["exec_id"], exec_id)
        self.assertEqual(lst[0]["status"], "waiting_approval")
        self.assertTrue(wf.resume_workflow(exec_id, approved=True))
        _wait_for(wf, exec_id, {"done"})
        # get_execution returns a snapshot copy
        snap = wf.get_execution(exec_id)
        snap["status"] = "mutated"
        self.assertEqual(wf.get_execution(exec_id)["status"], "done")

    def test_duplicate_node_ids_rejected(self):
        """W1: duplicate node IDs must raise, not silently drop a node."""
        wf = _patch_dirs(self)
        with self.assertRaises(ValueError) as ctx:
            wf.save_workflow(
                None, "Dup",
                [
                    {"id": "a", "type": "task", "label": "A", "prompt": "x"},
                    {"id": "a", "type": "task", "label": "A2", "prompt": "y"},
                ],
                [],
            )
        self.assertIn("duplicate node id", str(ctx.exception).lower())

    def test_save_workflow_atomic_write(self):
        """W2: save_workflow must write atomically (tmp + rename)."""
        wf = _patch_dirs(self)
        wid = wf.save_workflow(
            None, "Atomic",
            [{"id": "n1", "type": "task", "prompt": "x"}],
            [],
        )
        p = Path(wf._wf_dir()) / f"{wid}.json"
        self.assertTrue(p.is_file())
        # No stray tmp files left behind
        leftovers = list(Path(wf._wf_dir()).glob("*.tmp"))
        self.assertEqual(leftovers, [])
        # Round-trips cleanly
        data = wf.get_workflow(wid)
        self.assertEqual(data["name"], "Atomic")
        self.assertEqual(len(data["nodes"]), 1)

    def test_stale_running_becomes_interrupted(self):
        """W3: persisted non-terminal status with no live thread -> interrupted."""
        import json
        wf = _patch_dirs(self)
        wid = _make_three_node_wf(wf)
        agent = MockAgent()
        exec_id = wf.execute_workflow(wid, agent, node_timeout=10, approval_timeout=10)
        _wait_for(wf, exec_id, {"waiting_approval"})
        # Simulate a process restart: drop the in-memory runtime, keep the file
        wf._EXECUTIONS.clear()
        wf._EXEC_RUNTIME.clear()
        ex = wf.get_execution(exec_id)
        self.assertEqual(ex["status"], "interrupted")
        self.assertEqual(ex["nodes"]["n1"]["status"], "done")  # terminal stays
        self.assertEqual(ex["nodes"]["n2"]["status"], "interrupted")
        self.assertEqual(ex["nodes"]["n3"]["status"], "interrupted")
        # list_executions reconciles too
        lst = wf.list_executions(wid)
        self.assertEqual(lst[0]["status"], "interrupted")

    def test_stale_status_live_thread_unaffected(self):
        """W3 must not rewrite status while the execution is actually live."""
        wf = _patch_dirs(self)
        wid = _make_three_node_wf(wf)
        agent = MockAgent()
        exec_id = wf.execute_workflow(wid, agent, node_timeout=10, approval_timeout=10)
        ex = _wait_for(wf, exec_id, {"waiting_approval"})
        # In-memory entry exists -> no reconciliation
        self.assertEqual(ex["status"], "waiting_approval")
        self.assertTrue(wf.resume_workflow(exec_id, approved=True))
        _wait_for(wf, exec_id, {"done"})

    def test_approval_timeout_still_fails(self):
        """Approval gate with no decision must still time out (not hang)."""
        wf = _patch_dirs(self)
        wid = wf.save_workflow(
            None, "Timeout",
            [{"id": "n1", "type": "approval", "label": "ok?"}],
            [],
        )
        exec_id = wf.execute_workflow(wid, MockAgent(), approval_timeout=1)
        ex = _wait_for(wf, exec_id, {"failed"}, timeout=10)
        self.assertIn("timed out", ex["nodes"]["n1"]["error"])

    def test_eviction_keeps_last_100_terminal(self):
        """W-A1: _EXECUTIONS evicts oldest terminal entries beyond 100."""
        wf = _patch_dirs(self)
        for i in range(105):
            eid = f"exec_evict_{i:03d}"
            wf._EXECUTIONS[eid] = {
                "exec_id": eid, "wf_id": "wf_x", "wf_name": "x",
                "status": "done", "started_at": 1.0, "ended_at": 2.0,
                "nodes": {}, "log": [],
            }
        self.assertEqual(len(wf._EXECUTIONS), 105)
        with wf._EXEC_LOCK:
            wf._evict_old_executions_locked()
        self.assertEqual(len(wf._EXECUTIONS), 100)
        # Oldest evicted, newest kept (insertion order).
        self.assertNotIn("exec_evict_000", wf._EXECUTIONS)
        self.assertNotIn("exec_evict_004", wf._EXECUTIONS)
        self.assertIn("exec_evict_005", wf._EXECUTIONS)
        self.assertIn("exec_evict_104", wf._EXECUTIONS)

    def test_eviction_never_touches_live(self):
        """W-A1: live (non-terminal) executions are never evicted."""
        wf = _patch_dirs(self)
        for i in range(105):
            eid = f"exec_live_{i:03d}"
            wf._EXECUTIONS[eid] = {
                "exec_id": eid, "wf_id": "wf_x", "wf_name": "x",
                "status": "running", "started_at": 1.0, "ended_at": 0.0,
                "nodes": {}, "log": [],
            }
        with wf._EXEC_LOCK:
            wf._evict_old_executions_locked()
        self.assertEqual(len(wf._EXECUTIONS), 105)

    def test_evicted_execution_readable_from_disk(self):
        """W-A1: evicted entries remain readable via disk fallback."""
        wf = _patch_dirs(self)
        wid = wf.save_workflow(
            None, "Evict", [{"id": "n1", "type": "note", "label": "x"}], [])
        exec_id = wf.execute_workflow(wid, MockAgent())
        _wait_for(wf, exec_id, {"done"})
        # Flood with fake terminal entries, then evict.
        for i in range(105):
            eid = f"exec_fill_{i:03d}"
            wf._EXECUTIONS[eid] = {
                "exec_id": eid, "wf_id": wid, "wf_name": "Evict",
                "status": "done", "started_at": 1.0, "ended_at": 2.0,
                "nodes": {}, "log": [],
            }
        with wf._EXEC_LOCK:
            wf._evict_old_executions_locked()
        self.assertNotIn(exec_id, wf._EXECUTIONS)
        ex = wf.get_execution(exec_id)
        self.assertIsNotNone(ex)
        self.assertEqual(ex["status"], "done")

    def test_save_rejects_non_list_nodes(self):
        """W-A2: nodes must be a list (ValueError, not AttributeError)."""
        wf = _patch_dirs(self)
        with self.assertRaises(ValueError):
            wf.save_workflow(None, "Bad", "not-a-list", [])

    def test_save_rejects_non_dict_node(self):
        """W-A2: every node must be a dict (ValueError)."""
        wf = _patch_dirs(self)
        with self.assertRaises(ValueError):
            wf.save_workflow(None, "Bad",
                             [{"id": "n1", "type": "note"}, "oops"], [])

    def test_save_rejects_non_list_edges(self):
        """W-A2: edges must be a list (ValueError)."""
        wf = _patch_dirs(self)
        with self.assertRaises(ValueError):
            wf.save_workflow(None, "Bad",
                             [{"id": "n1", "type": "note"}], "not-a-list")


if __name__ == "__main__":
    unittest.main()
