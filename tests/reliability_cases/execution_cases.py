"""Real file tools through the normal agent loop; only provider I/O is scripted."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class ExecutionReliabilityIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.root), "ZELINE_HOME": str(self.root / "state")})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                del sys.modules[name]
        self.config = importlib.import_module("zeline.config")
        self.agent_module = importlib.import_module("zeline.agent")
        self.config.API_KEY = "offline-fixture"
        self.config.BASE_URL = "https://offline.invalid/v1"
        self.config.MODEL = "fixture-model"
        self.config.STREAM_RESPONSES = False
        self.config.FALLBACK_MODELS = ()
        self.config.FALLBACK_MODEL = ""

    def test_real_tools_finish_without_replaying_write_after_truncated_text(self):
        target = self.root / "artifact.txt"
        calls = []
        executed = []
        def tool(call_id, name, args):
            return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
        responses = [
            (tool("write-once", "write_file", {"path": str(target), "content": "Verified tool output.\n"}), "tool_calls"),
            (tool("read-once", "read_file", {"path": str(target)}), "tool_calls"),
            ({"role": "assistant", "content": "The file was written and read; verification "}, "length"),
            ({"role": "assistant", "content": "completed. Exact contents: Verified tool output."}, "stop"),
        ]
        class Response:
            status_code = 200
            ok = True
            encoding = "utf-8"
            def __init__(self, message, reason):
                self.text = json.dumps({"choices": [{"message": message, "finish_reason": reason}]})
            def close(self):
                pass
        def post(*args, **kwargs):
            calls.append(kwargs["json"])
            if not responses:
                raise AssertionError("Unexpected extra provider round")
            return Response(*responses.pop(0))
        agent = self.agent_module.Zeline(identity="fixture:real-tools", workspace=str(self.root), tool_profile="full")
        agent.stream_responses = False
        real_run = agent.executor.run
        def run(name, arguments, *args, **kwargs):
            executed.append(name)
            return real_run(name, arguments, *args, **kwargs)
        with patch.object(self.agent_module.requests, "post", side_effect=post), patch.object(agent.executor, "run", side_effect=run):
            reply = agent.send("Use write_file to create the requested file, read_file to verify it, then report its exact contents.")
        self.assertEqual(target.read_text(), "Verified tool output.\n")
        self.assertEqual(executed, ["write_file", "read_file"], "Continuation replayed a completed tool")
        self.assertEqual(len(calls), 4, "Truncated text was silently finalized instead of continued")
        initial_system = calls[0]["messages"][0]["content"]
        for payload in calls:
            self.assertTrue(payload["messages"][0]["content"].startswith(initial_system), "Continuation invalidated the stable prompt prefix")
            for message in payload["messages"]:
                self.assertFalse(any(key.startswith('_') or key in {'finish_reason', 'stop_reason'} for key in message), "Internal completion metadata leaked into provider message history")
        self.assertIn("completed", reply)
        self.assertIn("Verified tool output.", reply)
        self.assertEqual(sum(m.get("role") == "tool" for m in agent.messages), 2)


    def test_forced_partial_summary_is_reported_incomplete(self):
        agent = self.agent_module.Zeline(identity='fixture:forced-final',workspace=str(self.root),tool_profile='workspace')
        with patch.object(agent,'_call_llm',return_value={'content':'Unverified partial completion','finish_reason':'length'}):
            reply = agent._force_final_answer()
        self.assertIn('not complete',reply)
        self.assertFalse(agent.last_turn_complete)
        self.assertEqual(agent.messages[-1]['content'],reply)

    def test_partial_tool_arguments_are_bounded_without_dispatch(self):
        self.config.MAX_TOOL_ROUNDS = 5
        agent = self.agent_module.Zeline(identity='fixture:partial-tools',workspace=str(self.root),tool_profile='workspace')
        partial = {'content':'','finish_reason':'length','tool_calls':[{'id':'partial','type':'function','function':{'name':'write_file','arguments':'{'}}]}
        snapshots = []
        with patch.object(agent,'_call_llm',side_effect=lambda **kw:json.loads(json.dumps(partial))) as model, patch.object(agent.executor,'run') as execute:
            reply = agent.send('Write a file.',on_checkpoint=lambda messages:snapshots.append(json.loads(json.dumps(messages))))
        self.assertIn('not complete',reply)
        self.assertFalse(agent.last_turn_complete)
        self.assertEqual(model.call_count,3)
        execute.assert_not_called()
        self.assertEqual(snapshots[-1][-1]['content'],reply)
        self.assertFalse(any(m.get('tool_calls') for m in agent.messages))

if __name__ == "__main__":
    unittest.main(verbosity=2)
