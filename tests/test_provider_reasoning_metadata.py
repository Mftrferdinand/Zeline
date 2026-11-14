"""Offline round-trip contract; reasoning values are synthetic protocol markers."""
import copy
import json
import unittest
from unittest import mock

import test_agent as fixtures


MARKER = "SYNTHETIC_REASONING_METADATA_AB"


def tool_message():
    return {"role": "assistant", "content": "", "reasoning_content": MARKER,
            "tool_calls": [{"id": "synthetic-call", "type": "function",
                            "function": {"name": "manage_skill", "arguments": '{"action":"list"}'}}]}


class ProviderReasoningMetadataTests(unittest.TestCase):
    setUp = fixtures.AgentLoopTests.setUp
    tearDown = fixtures.AgentLoopTests.tearDown

    def test_stream_decode_preserves_split_reasoning_without_displaying_it(self):
        agent = self.agent_module.Zeline(tool_profile="safe")
        displayed = []
        deltas = [{"reasoning_content": "SYNTHETIC_REASONING_"},
                  {"reasoning_content": "METADATA_AB"},
                  {"tool_calls": [{"index": 0, **tool_message()["tool_calls"][0]}]}]
        lines = ["data: " + json.dumps({"choices": [{"delta": d}]}) for d in deltas]
        lines += ['data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}', "data: [DONE]"]
        with mock.patch.object(agent, "_stream_lines", return_value=iter(lines)), \
             mock.patch.object(agent, "_close_response"), mock.patch.object(agent, "_record_usage"):
            message = agent._consume_openai_stream(object(), displayed.append)
        self.assertEqual(displayed, [])
        self.assertEqual(message.get("reasoning_content"), MARKER)

    def test_nonstream_decode_preserves_reasoning(self):
        agent = self.agent_module.Zeline(tool_profile="safe")
        response = fixtures.FakeResponse({"choices": [{"message": tool_message(), "finish_reason": "tool_calls"}]})
        with mock.patch.object(self.agent_module.requests, "post", return_value=response):
            message = agent._call_llm(force_stream=False)
        self.assertEqual(message.get("reasoning_content"), MARKER)

    def test_send_preserves_reasoning_in_tool_call_history(self):
        agent = self.agent_module.Zeline(tool_profile="safe")
        with mock.patch.object(agent, "_call_llm", side_effect=[tool_message(), {"role": "assistant", "content": "Synthetic completion."}]), \
             mock.patch.object(agent.executor, "run", return_value="Synthetic result"):
            agent.send("Synthetic offline audit")
        call = next(m for m in agent.messages if m.get("tool_calls"))
        self.assertEqual(call.get("reasoning_content"), MARKER)

    def test_reflection_preserves_reasoning_before_next_provider_round(self):
        agent = self.agent_module.Zeline(tool_profile="full")
        snapshots = []
        def reply(*args, **kwargs):
            snapshots.append(copy.deepcopy(agent.messages))
            return tool_message() if len(snapshots) == 1 else {"role": "assistant", "content": "NO_ACTION"}
        with mock.patch.object(agent, "_call_llm", side_effect=reply), \
             mock.patch.object(agent.executor, "run", return_value="Synthetic result"), \
             mock.patch.object(self.agent_module.lessons, "unresolved_lessons", return_value=[]):
            agent.reflect(min_tool_calls=0)
        self.assertEqual(len(snapshots), 2)
        call = next(m for m in snapshots[1] if m.get("tool_calls"))
        self.assertEqual(call.get("reasoning_content"), MARKER)

    def test_request_serialization_preserves_already_stored_reasoning(self):
        agent = self.agent_module.Zeline(tool_profile="safe")
        agent.messages.extend([{"role": "user", "content": "Synthetic input"}, tool_message(),
                               {"role": "tool", "tool_call_id": "synthetic-call", "content": "Synthetic result"}])
        response = fixtures.FakeResponse({"choices": [{"message": {"role": "assistant", "content": "Done"}}]})
        with mock.patch.object(self.agent_module.requests, "post", return_value=response) as post:
            agent._call_llm(force_stream=False)
        # JSON round-trip checks actual serialized request shape, not object identity.
        payload = json.loads(json.dumps(post.call_args.kwargs["json"]))
        call = next(m for m in payload["messages"] if m.get("tool_calls"))
        self.assertEqual(call.get("reasoning_content"), MARKER)
        self.assertNotIn("finish_reason", call)
        self.assertNotIn("stop_reason", call)


if __name__ == "__main__":
    unittest.main()
