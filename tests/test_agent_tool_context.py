"""Late workers cannot adopt a later turn's reset cancellation state."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


class AgentToolContextTests(unittest.TestCase):
    def test_cancelled_worker_remains_cancelled_after_the_next_turn(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {
            "ZELINE_HOME": home, "ZELINE_API_KEY": "fixture-key",
            "ZELINE_BASE_URL": "http://provider.test/v1", "ZELINE_MODEL": "fixture-model",
        }):
            for name in list(sys.modules):
                if name == "zeline" or name.startswith("zeline."):
                    sys.modules.pop(name, None)
            module = importlib.import_module("zeline.agent")
            agent = module.Zeline(identity="fixture-owner", tool_profile="full", workspace=home)
            stopped = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            seen = []

            def reader(path, **_kwargs):
                if path != "slow":
                    return "fast result"
                stopped.set()
                try:
                    release.wait(2)
                    seen.append(agent.executor._cancelled())
                    return "late result"
                finally:
                    finished.set()

            agent.executor._handlers["read_file"] = reader
            calls = [{"id": f"call-{index}", "type": "function",
                      "function": {"name": "read_file", "arguments": json.dumps({"path": path})}}
                     for index, path in enumerate(("slow", "fast"))]
            responses = [{"content": "", "tool_calls": calls}, {"content": "second turn complete"}]
            try:
                with mock.patch.object(agent, "_call_llm", side_effect=responses):
                    started = time.monotonic()
                    result = agent.send("first", should_stop=stopped.is_set)
                    elapsed = time.monotonic() - started
                    self.assertEqual(result, module.CANCELLED_REPLY)
                    self.assertLess(elapsed, .8, "cancel must not join a blocked worker")
                    self.assertEqual(agent.send("second"), "second turn complete")
            finally:
                release.set()
                self.assertTrue(finished.wait(3), "disposable worker failed to release")
                # The handler returns before trace/audit cleanup finishes. Wait
                # for the worker-owned slots before deleting its private home.
                runtime = agent._parallel_runtime
                acquired = 0
                try:
                    for _ in range(runtime.limit):
                        self.assertTrue(runtime._slots.acquire(timeout=3))
                        acquired += 1
                finally:
                    for _ in range(acquired):
                        runtime._slots.release()
            self.assertEqual(seen, [True], "the old worker must keep its original cancellation context")
