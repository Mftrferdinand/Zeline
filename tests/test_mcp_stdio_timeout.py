"""A silent stdio MCP server must not hold an RPC beyond its read deadline."""
from __future__ import annotations

import shlex
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from zeline.mcp import MCPServer


class MCPStdioTimeoutTests(unittest.TestCase):
    def test_silent_server_respects_rpc_deadline_and_is_cleaned_up(self):
        class TrackedServer(MCPServer):
            spawned_process = None

            def _ensure_process(self):
                super()._ensure_process()
                self.spawned_process = self._process

        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "silent_mcp.py"
            script.write_text(
                "import sys, time\n"
                "sys.stdin.readline()\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            server = TrackedServer(
                name="silent", transport="stdio",
                command=f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}",
            )
            outcome = []

            def invoke():
                try:
                    server._rpc("tools/list", {}, timeout=0.1)
                except Exception as exc:  # noqa: BLE001 - capture result across thread
                    outcome.append(exc)

            worker = threading.Thread(target=invoke, daemon=True)
            started = time.monotonic()
            worker.start()
            worker.join(timeout=1.0)
            finished_within_budget = not worker.is_alive()
            elapsed = time.monotonic() - started
            process = server.spawned_process
            try:
                self.assertTrue(finished_within_budget, "silent stdio read ignored RPC deadline")
                self.assertEqual(len(outcome), 1)
                self.assertIsInstance(outcome[0], TimeoutError)
                self.assertLess(elapsed, 1.0)
                self.assertIsNone(server._process, "timed-out server should be closed")
                self.assertIsNotNone(process)
                self.assertIsNotNone(process.poll())
            finally:
                server.close()
                worker.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()