"""Exercise narration through the real agent loop with isolated fake transport."""
import copy
import json
import threading
import unittest
from unittest import mock

from tests import test_agent as support


def response(text, call_id=None):
    message = {"role": "assistant", "content": text}
    if call_id:
        message["tool_calls"] = [{"id": call_id, "type": "function", "function": {
            "name": "browser", "arguments": json.dumps({"action": "open", "url": "https://example.test"})}}]
    return support.FakeResponse({"choices": [{"message": message}]})


class NarrationDeliveryTests(unittest.TestCase):
    setUp = support.AgentLoopTests.setUp
    tearDown = support.AgentLoopTests.tearDown

    def test_routine_bubbles_are_hidden_without_changing_tools_or_history(self):
        routine = "Saya buka dulu webnya."
        finding = "Login ditolak karena session kedaluwarsa; alur login ulang diperlukan."
        agent = self.agent_module.Zeline(identity="telegram:narration-fixture", tool_profile="safe")
        narration, calls, results, checkpoints = [], [], [], []
        with mock.patch.object(self.agent_module.requests, "post", side_effect=[
                response(routine, "one"), response(finding, "two"), response("Hasil terverifikasi.")]), \
                mock.patch.object(agent.executor, "run", return_value="fixture observation") as execute:
            answer = agent.send("Inspect the fixture", on_narration=narration.append,
                                on_tool=lambda name, args: calls.append((name, args)),
                                on_tool_result=lambda name, args, result: results.append((name, args, result)),
                                on_checkpoint=lambda messages: checkpoints.append(copy.deepcopy(messages)))
        self.assertEqual(narration, [finding])
        self.assertEqual(answer, "Hasil terverifikasi.")
        self.assertEqual(execute.call_count, 2)
        self.assertEqual([name for name, _ in calls], ["browser", "browser"])
        self.assertEqual([r[2] for r in results], ["fixture observation", "fixture observation"])
        self.assertTrue(any(m.get("content") == routine and m.get("tool_calls") for m in agent.messages))
        self.assertTrue(any(any(m.get("content") == routine and m.get("tool_calls") for m in snapshot)
                            for snapshot in checkpoints))
        self.assertTrue(any([m.get("tool_call_id") for m in snapshot if m["role"] == "tool"]
                            == ["one", "two"] for snapshot in checkpoints))
        self.assertEqual([m["tool_call_id"] for m in agent.messages if m["role"] == "tool"], ["one", "two"])

    def test_duplicate_findings_are_limited_to_the_current_turn(self):
        finding = "The fixture endpoint returned HTTP 404; the replacement route is required."
        agent = self.agent_module.Zeline(identity="telegram:narration-repeat", tool_profile="safe")
        narration = []
        with mock.patch.object(self.agent_module.requests, "post", side_effect=[
                response(finding, "one"), response(finding, "two"), response("First answer."),
                response(finding, "three"), response("Second answer.")]), \
                mock.patch.object(agent.executor, "run", return_value="fixture observation"):
            self.assertEqual(agent.send("First fixture", on_narration=narration.append), "First answer.")
            self.assertEqual(narration, [finding])
            self.assertEqual(agent.send("Second fixture", on_narration=narration.append), "Second answer.")
        self.assertEqual(narration, [finding, finding])

    def test_recurring_blocker_after_recovery_is_not_hidden(self):
        failure = "Login failed: session expired."
        recovered = "Login succeeded; session restored."
        agent = self.agent_module.Zeline(identity="telegram:narration-recovery", tool_profile="safe")
        narration = []
        with mock.patch.object(self.agent_module.requests, "post", side_effect=[
                response(failure, "one"), response(recovered, "two"), response(failure, "three"),
                response("The session expired again; recovery is not durable.")]), \
                mock.patch.object(agent.executor, "run", return_value="fixture observation") as execute:
            answer = agent.send("Inspect the fixture", on_narration=narration.append)
        self.assertEqual(narration, [failure, recovered, failure])
        self.assertEqual(answer, "The session expired again; recovery is not durable.")
        self.assertEqual(execute.call_count, 3)
        self.assertEqual([m["tool_call_id"] for m in agent.messages if m["role"] == "tool"],
                         ["one", "two", "three"])

    def test_new_user_steering_can_report_the_same_blocker_again(self):
        finding = "Login ditolak karena kredensial tidak valid."
        agent = self.agent_module.Zeline(identity="telegram:narration-steer", tool_profile="safe")
        narration = []
        steering = iter(["Retry using the newly provided fixture credential", None])
        with mock.patch.object(self.agent_module.requests, "post", side_effect=[
                response(finding, "one"), response(finding, "two"), response("The blocker remains.")]), \
                mock.patch.object(agent.executor, "run", return_value="fixture observation"):
            self.assertEqual(agent.send("Inspect the fixture", on_narration=narration.append,
                                        take_steer=lambda: next(steering)), "The blocker remains.")
        self.assertEqual(narration, [finding, finding])

    def test_prompt_requires_meaningful_updates_not_routine_tool_announcements(self):
        prompt = self.agent_module.config.SYSTEM_PROMPT_TEMPLATE
        self.assertIn("Do not announce routine tool actions", prompt)
        self.assertIn("use the tool call with no accompanying chat text", prompt)
        self.assertIn("findings, blockers, decisions", prompt)
        self.assertIn("Never imply success before observing a tool result", prompt)
        self.assertNotIn("short narration → write_file", prompt)

    def test_final_streaming_deltas_are_not_narration_filtered(self):
        pieces = ["Saya ", "buka dulu webnya."]

        class StreamResponse(support.FakeResponse):
            def iter_lines(self, **kwargs):
                for piece in pieces:
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]})
                yield "data: [DONE]"

        agent = self.agent_module.Zeline(identity="telegram:narration-stream", tool_profile="safe")
        agent.stream_responses = True
        narration, deltas = [], []
        with mock.patch.object(self.agent_module.requests, "post", return_value=StreamResponse({})), \
                mock.patch.object(self.agent_module.NarrationGate, "select") as select:
            answer = agent.send("Quote the exact fixture text", on_narration=narration.append,
                                on_stream_delta=deltas.append)
        self.assertEqual(answer, "".join(pieces))
        self.assertEqual(deltas, pieces)
        self.assertEqual(narration, [])
        select.assert_not_called()

    def test_cancellation_after_model_reply_does_not_emit_or_dispatch(self):
        cancelled = threading.Event()
        agent = self.agent_module.Zeline(identity="telegram:narration-cancel", tool_profile="safe")
        narration, calls = [], []

        def model_reply(**kwargs):
            cancelled.set()
            return json.loads(response("Credentials rejected; a new login is required.", "cancelled").text)["choices"][0]["message"]

        with mock.patch.object(agent, "_call_llm", side_effect=model_reply), \
                mock.patch.object(agent.executor, "run") as execute, \
                mock.patch.object(self.agent_module.NarrationGate, "select") as select:
            answer = agent.send("Inspect the fixture", on_narration=narration.append,
                                on_tool=lambda *args: calls.append(args), should_stop=cancelled.is_set)
        self.assertEqual(answer, self.agent_module.CANCELLED_REPLY)
        self.assertEqual(narration, [])
        self.assertEqual(calls, [])
        select.assert_not_called()
        execute.assert_not_called()

    def test_final_answer_never_passes_through_the_narration_filter(self):
        text = "Saya buka dulu webnya."
        agent = self.agent_module.Zeline(identity="telegram:narration-final", tool_profile="safe")
        narration = []
        with mock.patch.object(self.agent_module.requests, "post", return_value=response(text)):
            self.assertEqual(agent.send("Quote this exact sentence", on_narration=narration.append), text)
        self.assertEqual(narration, [])