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
        pool = mcp.MCPRegistry.__new__(mcp.MCPRegistry)
        pool.servers = servers
        pool._errors = {}
        pool._tool_cache = None
        return pool

    def test_one_hanging_server_does_not_block_the_good_one(self):
        pool = self._pool({"hanging": _Hanging(), "good": _Good()})
        started = time.monotonic()
        tools = pool.schemas()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, mcp._DISCOVERY_DEADLINE + 2, elapsed)
        self.assertEqual(len(tools), 1)
        self.assertIn("hanging", pool._errors)

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


if __name__ == "__main__":
    unittest.main()
