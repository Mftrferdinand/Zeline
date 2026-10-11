"""Tests for ACP streaming (session/update notifications during session/prompt)."""

from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from zeline import acp as acp_mod
from zeline.acp import ACPServer


class FakeZeline:
    """Mock agent whose send() fires callbacks like a real streaming turn."""

    def __init__(self, identity="", workspace=""):
        self.identity = identity
        self.workspace = workspace
        self.messages: list = []
        self.script = None  # set by test: list of ("delta"|"tool"|"result"|"narr", ...)
        self.cancel_fn = None  # set by test: called on ("cancel",) script step

    def send(self, text, on_tool=None, on_tool_result=None, on_narration=None,
             on_stream_delta=None, should_stop=None, **kwargs):
        if should_stop and should_stop():
            return ""  # cooperative early exit, like the real agent loop
        for step in self.script or []:
            kind = step[0]
            if kind == "delta" and on_stream_delta:
                on_stream_delta(step[1])
            elif kind == "tool" and on_tool:
                on_tool(step[1], step[2])
            elif kind == "result" and on_tool_result:
                on_tool_result(step[1], step[2], step[3])
            elif kind == "narr" and on_narration:
                on_narration(step[1])
            elif kind == "cancel" and self.cancel_fn:
                self.cancel_fn()  # simulate client session/cancel mid-turn
            if should_stop and should_stop():
                return ""
        return "final answer"


def _make_server(script, cancel_fn=None):
    out = io.StringIO()
    server = ACPServer(out=out)

    def fake_zeline_factory(identity="", workspace=""):
        inst = FakeZeline(identity=identity, workspace=workspace)
        inst.script = script
        inst.cancel_fn = cancel_fn
        return inst

    patcher = mock.patch("zeline.agent.Zeline", fake_zeline_factory)
    patcher.start()
    server._test_patchers = (patcher,)
    return server, out


def _frames(out):
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def _new_session(server):
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "session/new",
                          "params": {"cwd": "/tmp"}})
    return resp["result"]["sessionId"]


class StreamingTests(unittest.TestCase):
    def tearDown(self):
        s = getattr(self, "_server", None)
        if s is not None:
            for p in getattr(s, "_test_patchers", ()):
                p.stop()

    def test_delta_chunks_streamed_as_agent_message_chunk(self):
        script = [("delta", "Hel"), ("delta", "lo"), ("delta", " world")]
        self._server, out = _make_server(script)
        sid = _new_session(self._server)
        resp = self._server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
             "params": {"sessionId": sid,
                        "prompt": [{"type": "text", "text": "hi"}]}})
        self.assertEqual(resp["result"], {"stopReason": "end_turn"})
        frames = _frames(out)
        chunks = [f for f in frames
                  if f.get("method") == "session/update"
                  and f["params"]["update"]["sessionUpdate"] == "agent_message_chunk"]
        # Deltas di-batch (hemat flush): 3 delta kecil -> 1 frame gabungan.
        self.assertEqual(len(chunks), 1)
        self.assertEqual("".join(c["params"]["update"]["content"]["text"]
                                 for c in chunks), "Hello world")
        for c in chunks:
            self.assertEqual(c["params"]["sessionId"], sid)
            self.assertNotIn("id", c)  # notifications carry no id

    def test_delta_batching_preserves_order_with_tool_calls(self):
        # Urutan harus terjaga: teks yang datang sebelum tool_call
        # di-flush dulu, jadi IDE tidak melihat tool_call mendahului teks.
        script = [("delta", "thinking..."), ("tool", "read_file", {"path": "a.txt"}),
                  ("delta", "done")]
        self._server, out = _make_server(script)
        sid = _new_session(self._server)
        self._server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
             "params": {"sessionId": sid,
                        "prompt": [{"type": "text", "text": "hi"}]}})
        frames = _frames(out)
        updates = [f["params"]["update"] for f in frames
                   if f.get("method") == "session/update"]
        kinds = [u["sessionUpdate"] for u in updates]
        self.assertIn("agent_message_chunk", kinds)
        self.assertIn("tool_call", kinds)
        # chunk teks pertama harus muncul SEBELUM tool_call
        first_chunk = kinds.index("agent_message_chunk")
        tool_call = kinds.index("tool_call")
        self.assertLess(first_chunk, tool_call)
        texts = [u["content"]["text"] for u in updates
                 if u["sessionUpdate"] == "agent_message_chunk"]
        self.assertEqual("".join(texts), "thinking...done")

    def test_tool_call_and_result_paired(self):
        script = [("tool", "read_file", {"path": "a.txt"}),
                  ("result", "read_file", {"path": "a.txt"}, "file contents here"),
                  ("tool", "run_shell", {"cmd": "ls"}),
                  ("result", "run_shell", {"cmd": "ls"}, "ok")]
        self._server, out = _make_server(script)
        sid = _new_session(self._server)
        self._server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
             "params": {"sessionId": sid,
                        "prompt": [{"type": "text", "text": "do things"}]}})
        frames = _frames(out)
        calls = [f["params"]["update"] for f in frames
                 if f.get("method") == "session/update"
                 and f["params"]["update"]["sessionUpdate"] == "tool_call"]
        updates = [f["params"]["update"] for f in frames
                   if f.get("method") == "session/update"
                   and f["params"]["update"]["sessionUpdate"] == "tool_call_update"]
        self.assertEqual([c["title"] for c in calls], ["read_file", "run_shell"])
        # FIFO pairing: result ids match call ids in order
        self.assertEqual([u["toolCallId"] for u in updates],
                         [c["toolCallId"] for c in calls])
        self.assertTrue(all(u["status"] == "completed" for u in updates))
        self.assertIn("file contents here", updates[0]["content"][0]["text"])

    def test_narration_streams_as_thought_chunk(self):
        script = [("narr", "Checking the workspace first…"),
                  ("delta", "done")]
        self._server, out = _make_server(script)
        sid = _new_session(self._server)
        self._server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
             "params": {"sessionId": sid,
                        "prompt": [{"type": "text", "text": "hi"}]}})
        frames = _frames(out)
        thoughts = [f for f in frames
                    if f.get("method") == "session/update"
                    and f["params"]["update"]["sessionUpdate"] == "agent_thought_chunk"]
        self.assertEqual(len(thoughts), 1)
        self.assertIn("workspace", thoughts[0]["params"]["update"]["content"]["text"])

    def test_no_streaming_agent_still_works(self):
        # Agent that never calls on_stream_delta: no chunk frames, but the
        # turn completes exactly like the old blocking runner.
        self._server, out = _make_server([])
        sid = _new_session(self._server)
        resp = self._server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
             "params": {"sessionId": sid,
                        "prompt": [{"type": "text", "text": "hi"}]}})
        self.assertEqual(resp["result"], {"stopReason": "end_turn"})
        frames = _frames(out)
        self.assertEqual(frames, [])  # nothing emitted, nothing broken
        # History recorded as before
        session = self._server.sessions[sid]
        self.assertEqual(session.history[-1],
                         {"role": "assistant", "content": "final answer"})

    def test_cancel_still_reports_cancelled(self):
        # Simulate a client session/cancel arriving mid-turn: the cancel
        # flag is set during the turn (not before — _session_prompt clears
        # a stale flag at turn start per the H-2 fix).
        holder = {}
        self._server, out = _make_server(
            [("delta", "partial"), ("cancel",)],
            cancel_fn=lambda: holder["session"]._cancel.set())
        sid = _new_session(self._server)
        holder["session"] = self._server.sessions[sid]
        resp = self._server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
             "params": {"sessionId": sid,
                        "prompt": [{"type": "text", "text": "hi"}]}})
        self.assertEqual(resp["result"], {"stopReason": "cancelled"})

    def test_broken_pipe_does_not_crash(self):
        class DeadPipe:
            def write(self, *a, **k):
                raise BrokenPipeError("closed")
            def flush(self):
                pass

        server = ACPServer(out=DeadPipe())
        self._server = server

        def fake_factory(identity="", workspace=""):
            inst = FakeZeline(identity=identity, workspace=workspace)
            inst.script = [("delta", "x")]
            return inst

        with mock.patch("zeline.agent.Zeline", fake_factory):
            sid = _new_session(server)
            # Must not raise despite every notification failing
            resp = server.handle(
                {"jsonrpc": "2.0", "id": 2, "method": "session/prompt",
                 "params": {"sessionId": sid,
                            "prompt": [{"type": "text", "text": "hi"}]}})
        self.assertIsNotNone(resp)
        # Dead client => turn auto-cancelled
        self.assertTrue(server.sessions[sid]._cancel.is_set())

    def test_initialize_reports_new_version(self):
        server = ACPServer(out=io.StringIO())
        self._server = server
        resp = server.handle({"jsonrpc": "2.0", "id": 1,
                              "method": "initialize", "params": {}})
        self.assertEqual(resp["result"]["agentInfo"]["name"], "Zeline")

    def test_malformed_frames_still_handled(self):
        # Existing hardening preserved: non-dict, bad version, bad params.
        server = ACPServer(out=io.StringIO())
        self._server = server
        r = server.handle("not a dict")
        self.assertEqual(r["error"]["code"], -32600)
        r = server.handle({"jsonrpc": "1.0", "id": 1, "method": "initialize"})
        self.assertEqual(r["error"]["code"], -32600)
        r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "session/new",
                           "params": "nope"})
        self.assertEqual(r["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
