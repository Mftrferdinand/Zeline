"""Substantial-turn reflection must run after admission releases the user turn."""
import importlib
import threading
import unittest
from unittest import mock

from tests import test_turn_admission as admission


class ReflectionDeliveryTests(unittest.TestCase):
    setUp = admission.TurnAdmissionTests.setUp

    def test_reflection_is_not_lost_when_background_thread_starts_immediately(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        session = self.store.get_or_create(self.identity, "full")
        real_thread = threading.Thread

        def thread(*args, **kwargs):
            if kwargs.get("name", "").startswith("zeline-reflect-"):
                return mock.Mock(start=kwargs["target"])
            return real_thread(*args, **kwargs)

        summary = "📙 <b>Self-Improvement review:</b> Saved verified learning evidence."
        with mock.patch.object(session.agent, "send", return_value="Task complete"), \
             mock.patch.object(session.agent, "reflect", return_value=summary) as reflect, \
             mock.patch.object(telegram, "_api_call", return_value={"ok": True}) as api, \
             mock.patch.object(telegram, "_LiveStatus"), \
             mock.patch.object(telegram, "_start_working_heartbeat", return_value=mock.Mock()), \
             mock.patch.object(telegram.threading, "Thread", side_effect=thread):
            worker = telegram._start_agent_reply("fixture", self.store, chat_id=1,
                identity=self.identity, text="substantial task", tool_profile="full", reply_to_message_id=42)
            worker.join(5)
        self.assertFalse(worker.is_alive())
        reflect.assert_called_once()
        delivered = [c.kwargs for c in api.call_args_list if c.args[1] == "sendMessage"]
        self.assertTrue(any(p.get("text") == summary for p in delivered))

    def test_eligible_review_without_changes_is_visible_not_a_fake_improvement(self):
        module = importlib.import_module("zeline.agent")
        agent = module.Zeline(identity=self.identity, tool_profile="full")
        agent.last_turn_tool_calls = 8
        with mock.patch.object(agent, "_call_llm", return_value={"content": "NO_ACTION"}):
            summary = agent.reflect()
        self.assertIsNotNone(summary)
        self.assertIn("📙", summary)
        self.assertIn("unchanged", summary)
        self.assertNotIn("📘", summary)
        self.assertNotIn("📗", summary)

    def test_provider_failure_is_visible_and_does_not_reset_pending_learning(self):
        module = importlib.import_module("zeline.agent")
        agent = module.Zeline(identity=self.identity, tool_profile="full")
        agent._tool_calls_since_reflection = 8
        with mock.patch.object(agent, "_call_llm", side_effect=module.ZelineError("provider unavailable")):
            summary = agent.reflect()
        self.assertIsNotNone(summary)
        self.assertIn("incomplete", summary)
        self.assertEqual(agent._tool_calls_since_reflection, 8)

    def test_reflection_saves_a_real_memory_visible_to_the_next_agent(self):
        module = importlib.import_module("zeline.agent")
        agent = module.Zeline(identity=self.identity, tool_profile="full")
        agent.last_turn_tool_calls = 8
        fact = "HTTP response status is checked before a request is treated as successful."
        save = {"content": "", "tool_calls": [{"id": "learn", "type": "function",
            "function": {"name": "add_memory", "arguments": __import__("json").dumps({"fact": fact})}}]}
        with mock.patch.object(agent, "_call_llm", side_effect=[save, {"content": "NO_ACTION"}]):
            summary = agent.reflect()
        self.assertIn("📙", summary)
        self.assertIn("📗", summary)
        self.assertNotIn("📘", summary)
        records = agent.executor.memory.records()
        self.assertTrue(any(r["text"] == fact and r["source"] == "reflection" for r in records))
        next_agent = module.Zeline(identity=self.identity, tool_profile="full")

        def next_reply(**kwargs):
            # Memory is selected per user turn, not stuffed into the system prompt.
            self.assertIn(fact, next_agent._turn_memory_context)
            return {"content": "I will verify the HTTP response status."}

        with mock.patch.object(next_agent, "_call_llm", side_effect=next_reply):
            next_agent.send("Check the HTTP response status before treating the request as successful.")
        with mock.patch.object(agent, "_call_llm") as provider:
            self.assertIsNone(agent.reflect())
        provider.assert_not_called()

    def test_failed_attempt_is_kept_as_unresolved_not_a_fake_success(self):
        module = importlib.import_module("zeline.agent")
        agent = module.Zeline(identity=self.identity, tool_profile="full")
        agent.last_turn_tool_calls = 8
        result = agent.executor.run("read_file", {"path": "missing-learning-fixture.txt"})
        self.assertTrue(result.startswith("ERROR"))
        with mock.patch.object(agent, "_call_llm", return_value={"content": "NO_ACTION"}):
            summary = agent.reflect()
        self.assertIn("still need a verified fix", summary)
        records = module.lessons.unresolved_lessons(self.identity)
        self.assertTrue(records)
        self.assertFalse(module.lessons.resolved_lessons(self.identity))
        self.assertNotIn("📗", summary)

    def test_waiting_for_human_does_not_consume_the_active_work_budget(self):
        module = importlib.import_module("zeline.agent")
        agent = module.Zeline(identity=self.identity, tool_profile="full")
        clock = [0.0]
        ask = {"content": "", "tool_calls": [{"id": "question", "type": "function",
            "function": {"name": "ask_user", "arguments": '{"question":"Proceed?"}'}}]}

        def execute(name, args):
            clock[0] += module.config.MAX_TURN_SECONDS + 60
            return "Proceed"

        with mock.patch.object(module.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(agent.executor, "run", side_effect=execute), \
             mock.patch.object(agent, "_call_llm", side_effect=[ask, {"content": "Continuing normally"}]) as provider:
            agent.send("Ask before doing the next step")
        self.assertFalse(any(c.kwargs.get("use_tools") is False for c in provider.call_args_list),
                         "A human pause incorrectly exhausted the agent's work budget")

    def test_slow_tool_execution_still_consumes_the_active_work_budget(self):
        module = importlib.import_module("zeline.agent")
        agent = module.Zeline(identity=self.identity, tool_profile="full")
        clock = [0.0]
        tool = {"content": "", "tool_calls": [{"id": "request", "type": "function",
            "function": {"name": "http_request", "arguments": '{"url":"https://example.test"}'}}]}

        def execute(name, args):
            clock[0] += module.config.MAX_TURN_SECONDS + 60
            return "Status: 200"

        with mock.patch.object(module.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(agent.executor, "run", side_effect=execute), \
             mock.patch.object(agent, "_call_llm", side_effect=[tool, {"content": "Budget reached"}]) as provider:
            agent.send("Fetch the requested page")
        self.assertTrue(any(c.kwargs.get("use_tools") is False for c in provider.call_args_list),
                        "Ordinary slow tools must not bypass execution limits")

    def test_single_deep_research_call_qualifies_even_below_five_tools(self):
        module = importlib.import_module("zeline.agent")
        agent = module.Zeline(identity=self.identity, tool_profile="full")
        research = {"content": "", "tool_calls": [{"id": "research", "type": "function",
            "function": {"name": "deep_research", "arguments": '{"query":"public engineering docs"}'}}]}
        with mock.patch.object(agent, "_call_llm", side_effect=[research, {"content": "Research complete"}]), \
             mock.patch.object(agent.executor, "run", return_value="Source-backed research output"):
            agent.send("Research public engineering documentation")
        with mock.patch.object(agent, "_call_llm", return_value={"content": "NO_ACTION"}) as provider:
            summary = agent.reflect()
        provider.assert_called_once()
        self.assertIsNotNone(summary)
