"""Dispatch limits bound real request/tool paths without contacting a provider."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


class ExecutionLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"ZELINE_HOME": self.tmp.name, "ZELINE_API_KEY": "fixture",
                                     "ZELINE_BASE_URL": "http://127.0.0.1:1/v1", "ZELINE_MODEL": "fixture"})
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.module = importlib.import_module("zeline.agent")
        self.module.config.STREAM_RESPONSES = False
        self.module.config.FALLBACK_MODELS = ()
        self.module.config.FALLBACK_MODEL = ""
        self.budget = importlib.import_module("zeline.run_budget")
        self.agent = self.module.Zeline(workspace=self.tmp.name, tool_profile="full")

    @staticmethod
    def response(message=None, status=200):
        response = Mock(status_code=status, ok=status < 400, encoding="utf-8")
        response.text = json.dumps({"choices": [{"message": message or {"content": "done"}}]})
        return response

    @staticmethod
    def calls(name, args=None, count=1):
        return {"content": "", "tool_calls": [
            {"id": str(i), "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}
            for i in range(count)
        ]}

    def test_request_limit_stops_before_second_provider_request(self):
        self.module.config.MAX_PROVIDER_REQUESTS_PER_TURN = 1
        response = self.response(self.calls("runtime_info"))
        with patch.object(self.module.requests, "post", return_value=response) as post:
            result = self.agent.send("fixture")
        self.assertEqual(post.call_count, 1)
        self.assertIn("provider_requests limit reached (1)", result)
        self.assertIsNone(self.agent.executor._run_budget)

    def test_retry_attempts_share_same_allowance(self):
        self.module.config.MAX_PROVIDER_REQUESTS_PER_TURN = 1
        with patch.object(self.module.requests, "post", return_value=self.response(status=503)) as post, \
             patch.object(self.module.time, "sleep"):
            result = self.agent.send("fixture")
        self.assertEqual(post.call_count, 1)
        self.assertIn("budget exhausted", result)

    def test_tool_limit_stops_second_side_effect(self):
        self.module.config.MAX_TOOL_CALLS_PER_TURN = 1
        with patch.object(self.agent, "_call_llm", return_value=self.calls("run_shell", count=2)), \
             patch.object(self.agent.executor, "_execute", return_value="exit=0\nfixture") as execute:
            result = self.agent.send("fixture")
        self.assertEqual(execute.call_count, 1)
        self.assertIn("tool_calls limit reached (1)", result)
        self.assertFalse(importlib.import_module("zeline.tool_protocol").unanswered_call_ids(self.agent.messages))
        tool_results = {
            message["tool_call_id"]: message["content"]
            for message in self.agent.messages if message.get("role") == "tool"
        }
        self.assertIn("exit=0", tool_results["0"])
        self.assertIn("UNKNOWN", tool_results["1"])
        persistence_module = importlib.import_module("zeline.session_store")
        persistence = persistence_module.SessionPersistence(Path(self.tmp.name) / "budget-session.db")
        persistence.save("budget-audit", self.agent.export_history(), title="budget audit")
        rebuilt = self.module.Zeline(workspace=self.tmp.name, tool_profile="full")
        restored_history, _title = persistence.load("budget-audit")
        rebuilt.load_history(restored_history)
        restored = {m["tool_call_id"]: m["content"] for m in rebuilt.messages if m.get("role") == "tool"}
        self.assertEqual(restored, tool_results)

    def test_successful_tool_result_survives_ui_callback_failure(self):
        def callback(*_args):
            raise RuntimeError("UI callback failed")
        with patch.object(self.agent, "_call_llm", return_value=self.calls("run_shell")), \
             patch.object(self.agent.executor, "_execute", return_value="completed mutation evidence"):
            with self.assertRaises(RuntimeError):
                self.agent.send("fixture", on_tool_result=callback)
        recorded = [m["content"] for m in self.agent.messages if m.get("role") == "tool"]
        self.assertIn("completed mutation evidence", recorded)

    def test_budget_result_callback_cannot_erase_completed_evidence(self):
        self.module.config.MAX_TOOL_CALLS_PER_TURN = 1
        def callback(*_args):
            raise RuntimeError("UI callback failed")
        with patch.object(self.agent, "_call_llm", return_value=self.calls("run_shell", count=2)), \
             patch.object(self.agent.executor, "_execute", return_value="completed mutation evidence"):
            with self.assertRaises(RuntimeError):
                self.agent.send("fixture", on_tool_result=callback)
        recorded = [m["content"] for m in self.agent.messages if m.get("role") == "tool"]
        self.assertIn("completed mutation evidence", recorded)

    def test_parallel_evidence_survives_checkpoint_failure(self):
        def checkpoint(messages):
            if any(message.get("role") == "tool" for message in messages):
                raise OSError("checkpoint backend unavailable")
        with patch.object(self.agent, "_call_llm", return_value=self.calls("runtime_info", count=2)) as provider, \
             patch.object(self.agent.executor, "run", return_value="parallel checkpoint evidence") as execute:
            with self.assertRaises(OSError):
                self.agent.send("fixture", on_checkpoint=checkpoint)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(execute.call_count, 2)
        recorded = [message["content"] for message in self.agent.messages if message.get("role") == "tool"]
        exported = [message["content"] for message in self.agent.export_history() if message.get("role") == "tool"]
        self.assertEqual(recorded, ["parallel checkpoint evidence", "parallel checkpoint evidence"])
        self.assertEqual(exported, recorded)

    def test_serial_evidence_survives_checkpoint_failure(self):
        def checkpoint(messages):
            if any(message.get("role") == "tool" for message in messages):
                raise OSError("checkpoint backend unavailable")
        with patch.object(self.agent, "_call_llm", return_value=self.calls("run_shell")) as provider, \
             patch.object(self.agent.executor, "run", return_value="serial checkpoint evidence") as execute:
            with self.assertRaises(OSError):
                self.agent.send("fixture", on_checkpoint=checkpoint)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(execute.call_count, 1)
        recorded = [message["content"] for message in self.agent.messages if message.get("role") == "tool"]
        self.assertEqual(recorded, ["serial checkpoint evidence"])

    def test_serial_callback_failure_repairs_unexecuted_calls(self):
        for failure in ("checkpoint", "result"):
            with self.subTest(failure=failure):
                self.agent.messages = self.agent.messages[:1]
                def checkpoint(messages):
                    if any(message.get("role") == "tool" for message in messages):
                        raise OSError("checkpoint backend unavailable")
                def callback(*_args):
                    raise OSError("result callback unavailable")
                kwargs = {"on_checkpoint": checkpoint} if failure == "checkpoint" else {"on_tool_result": callback}
                # A result callback runs after batching, or in the partial-batch
                # budget path; exercise that path with one unexecuted call.
                self.module.config.MAX_TOOL_CALLS_PER_TURN = 1
                with patch.object(self.agent, "_call_llm", return_value=self.calls("run_shell", count=2)) as provider, \
                     patch.object(self.agent.executor, "_execute", return_value="completed serial evidence") as execute:
                    with self.assertRaises(OSError):
                        self.agent.send("fixture", **kwargs)
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(execute.call_count, 1)
                protocol = importlib.import_module("zeline.tool_protocol")
                self.assertFalse(protocol.unanswered_call_ids(self.agent.messages))
                exported = self.agent.export_history()
                self.assertFalse(protocol.unanswered_call_ids(exported))
                results = [message for message in exported if message.get("role") == "tool"]
                self.assertEqual(len(results), 2)
                self.assertEqual(results[0]["content"], "completed serial evidence")
                self.assertEqual(results[0]["tool_call_id"], "0")
                self.assertEqual(results[1]["tool_call_id"], "1")
                self.assertIn("UNKNOWN", results[1]["content"])

    def test_parallel_tool_budget_preserves_completed_results(self):
        self.module.config.MAX_TOOL_CALLS_PER_TURN = 1
        with patch.object(self.agent, "_call_llm", return_value=self.calls("runtime_info", count=2)), \
             patch.object(self.agent.executor, "_execute", return_value="parallel evidence") as execute:
            result = self.agent.send("fixture")
        self.assertEqual(execute.call_count, 1)
        self.assertIn("budget exhausted", result)
        recorded = [message["content"] for message in self.agent.messages if message.get("role") == "tool"]
        self.assertEqual(sum("parallel evidence" in item for item in recorded), 1)
        self.assertEqual(sum("UNKNOWN" in item for item in recorded), 1)

    def test_parallel_budget_failure_drains_later_successful_future(self):
        error = self.budget.BudgetExceeded("fixture limit")
        def run(_name, args):
            if args["index"] == 0:
                raise error
            return "later successful evidence"
        message = self.calls("runtime_info", count=2)
        for index, call in enumerate(message["tool_calls"]):
            call["function"]["arguments"] = json.dumps({"index": index})
        with patch.object(self.agent, "_call_llm", return_value=message), \
             patch.object(self.agent.executor, "run", side_effect=run):
            result = self.agent.send("fixture")
        self.assertIn("budget exhausted", result)
        recorded = {m["tool_call_id"]: m["content"] for m in self.agent.messages if m.get("role") == "tool"}
        self.assertIn("UNKNOWN", recorded["0"])
        self.assertEqual(recorded["1"], "later successful evidence")

    def test_parent_and_parallel_children_share_request_allowance(self):
        self.module.config.MAX_PROVIDER_REQUESTS_PER_TURN = 2
        delegation = self.calls("delegate_task", {"tasks": [{"goal": "one"}, {"goal": "two"}]})
        responses = [self.response(delegation), self.response({"content": "child evidence"})]
        with patch.object(self.module.requests, "post", side_effect=responses) as post:
            result = self.agent.send("delegate fixture")
        self.assertEqual(post.call_count, 2)
        self.assertIn("budget exhausted", result)

    def test_next_turn_receives_fresh_allowance(self):
        self.module.config.MAX_PROVIDER_REQUESTS_PER_TURN = 1
        with patch.object(self.module.requests, "post", return_value=self.response()) as post:
            self.assertEqual(self.agent.send("one"), "done")
            self.assertEqual(self.agent.send("two"), "done")
        self.assertEqual(post.call_count, 2)

    def test_parallel_reservations_cannot_overrun_limit(self):
        budget = self.budget.RunBudget(provider_requests=3)
        def reserve(_):
            try:
                budget.consume("provider_requests")
                return True
            except self.budget.BudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(50))), 3)
        self.assertEqual(budget.used["provider_requests"], 3)

    def test_invalid_limit_fails_before_network(self):
        for value in (-1, True, "3", 1.5, 100001):
            self.module.config.MAX_PROVIDER_REQUESTS_PER_TURN = value
            with patch.object(self.module.requests, "post") as post:
                self.assertIn("execution budget exhausted", self.agent.send("fixture"))
            post.assert_not_called()

    def test_verifier_problems_are_failure_for_learning(self):
        delegation = importlib.import_module("zeline.delegation")
        outcomes = importlib.import_module("zeline.outcomes")
        results = delegation.run_tasks([delegation.Task("fixture")], spawn=lambda *_: "work evidence")
        result = delegation.render(results, verification="VERDICT: problems\nmissing test evidence")
        self.assertEqual(outcomes.tool_outcome(result), "failure")
        self.assertIn("work evidence", result)

    def test_unrecognized_verdict_is_not_accepted(self):
        delegation = importlib.import_module("zeline.delegation")
        results = delegation.run_tasks([delegation.Task("fixture")], spawn=lambda *_: "work evidence")
        result = delegation.render(results, verification="probably fine")
        self.assertTrue(result.startswith("ERROR: verification is inconclusive"))
        self.assertIn("Not verified", result)

    def test_child_error_response_is_not_counted_as_success(self):
        delegation = importlib.import_module("zeline.delegation")
        results = delegation.run_tasks([delegation.Task("fixture")], spawn=lambda *_: "ERROR: execution budget exhausted")
        self.assertFalse(results[0].ok)
        self.assertTrue(delegation.render(results).startswith("ERROR:"))


if __name__ == "__main__":
    unittest.main()
