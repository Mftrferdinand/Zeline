"""A dead MCP server must not stall chat: discovery is bounded and parallel."""
from __future__ import annotations

import time
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from zeline import mcp  # noqa: E402


class _Hanging:
    name = "hanging"

    def list_tools(self):
        time.sleep(30)          # far beyond the deadline
        return []


class _Dead:
    name = "dead"

    def list_tools(self):
        raise ConnectionError("connection refused")


class _Good:
    name = "good"

    def list_tools(self):
        return [{"type": "function", "function": {"name": "mcp__good__ping"}}]


class DiscoveryDeadlineTests(unittest.TestCase):
    def _pool(self, servers):
        return mcp.MCPRegistry(list(servers.values()))

    def test_one_hanging_server_does_not_block_the_good_one(self):
        pool = self._pool({"hanging": _Hanging(), "good": _Good()})
        started = time.monotonic()
        tools = pool.schemas()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, mcp._DISCOVERY_DEADLINE + 2, elapsed)
        self.assertEqual(len(tools), 1)
        self.assertIn("hanging", pool._errors)

    def test_late_server_becomes_available_without_a_second_probe(self):
        import threading
        from unittest.mock import patch

        release = threading.Event()
        finished = threading.Event()
        calls = []

        class Late:
            name = "late"

            def list_tools(self):
                calls.append("probe")
                release.wait(2)
                finished.set()
                return [{"type": "function", "function": {"name": "mcp__late__ping"}}]

        pool = mcp.MCPRegistry([Late(), _Good()])
        try:
            with patch.object(mcp, "_DISCOVERY_DEADLINE", 0.02):
                self.assertEqual(len(pool.schemas()), 1)
                self.assertIn("late", pool.errors)
                release.set()
                self.assertTrue(finished.wait(1))
                # Synchronize the discovery worker, not an arbitrary sleep.
                for thread in pool._discovery_threads.values():
                    thread.join(1)
                    self.assertFalse(thread.is_alive())
                names = {s["function"]["name"] for s in pool.schemas()}
                self.assertEqual(names, {"mcp__good__ping", "mcp__late__ping"})
                self.assertNotIn("late", pool.errors)
                self.assertEqual(pool.schemas(), pool.schemas())
                self.assertEqual(calls, ["probe"])
        finally:
            release.set()

    def test_dead_server_is_recorded_not_raised(self):
        pool = self._pool({"dead": _Dead(), "good": _Good()})
        tools = pool.schemas()
        self.assertEqual(len(tools), 1)
        self.assertIn("dead", pool._errors)

    def test_cached_result_skips_the_network_entirely(self):
        pool = self._pool({"good": _Good()})
        pool.schemas()
        pool.servers = {"hanging": _Hanging()}   # would block if re-probed
        started = time.monotonic()
        pool.schemas()
        self.assertLess(time.monotonic() - started, 0.5)

    def test_timed_out_stdio_probe_retires_server_process(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "silent_mcp.py"
            script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            server = mcp.MCPServer(
                name="silent",
                transport="stdio",
                command=f'"{sys.executable}" "{script}"',
            )
            started = time.monotonic()
            try:
                with self.assertRaises(TimeoutError):
                    server._rpc("initialize", {}, timeout=0.1)
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertIsNone(server._process)
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
