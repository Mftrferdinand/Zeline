"""Invocation cancellation remains bound to its owning turn."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock


class ExecutionContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = self.temp.name
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.tools = importlib.import_module("zeline.tools")
        self.executor = self.tools.ToolExecutor("owner", "full", self.temp.name)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old
        self.temp.cleanup()

    def test_call_does_not_adopt_the_next_turn_stop_predicate(self):
        cancelled = [False]
        self.executor._should_stop = lambda: cancelled[0]
        seen = []

        def dispatch(_name, _args):
            cancelled[0] = True
            self.executor._should_stop = lambda: False
            seen.append(self.executor._cancelled())
            return "finished"

        with mock.patch.object(self.executor, "_execute", side_effect=dispatch):
            self.executor.run("read_file", {})
        self.assertEqual(seen, [True])
        self.assertFalse(self.executor._cancelled(), "invocation context must be cleared afterwards")

    def test_context_keeps_the_original_budget_and_observer(self):
        budget_before = mock.Mock()
        budget_after = mock.Mock()
        observer_before = mock.Mock()
        observer_after = mock.Mock()
        self.executor._run_budget = budget_before
        self.executor._skill_telemetry = observer_before
        context = self.executor.capture_context()
        self.executor._run_budget = budget_after
        self.executor._skill_telemetry = observer_after
        with mock.patch.object(self.executor, "_execute", return_value="result"):
            self.executor.run("read_file", {}, _context=context)
        budget_before.consume.assert_called_once_with("tool_calls")
        budget_after.consume.assert_not_called()
        observer_before.observe.assert_called_once()
        observer_after.observe.assert_not_called()

    def test_bound_context_also_wraps_plain_two_argument_run_callbacks(self):
        self.executor._should_stop = lambda: True
        context = self.executor.capture_context()
        self.executor._should_stop = lambda: False
        with self.executor.invocation_context(context):
            self.assertTrue(self.executor._cancelled())
            self.assertIn("CANCELLED", self.executor.run("read_file", {}))
        self.assertFalse(self.executor._cancelled())

    def test_queued_call_uses_explicit_captured_turn_context(self):
        cancelled = [False]
        self.executor._should_stop = lambda: cancelled[0]
        context = self.executor.capture_context()
        cancelled[0] = True
        self.executor._should_stop = lambda: False
        with mock.patch.object(self.executor, "_execute") as execute:
            result = self.executor.run("read_file", {}, _context=context)
        execute.assert_not_called()
        self.assertIn("CANCELLED", result)


if __name__ == "__main__":
    unittest.main()
