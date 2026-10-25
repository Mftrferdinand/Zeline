"""Deterministic registry ownership and late-publication regression checks."""
from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from zeline import mcp


class ControlledServer:
    def __init__(self, name, *, fail=False):
        self.name = name
        self.fail = fail
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.lock = threading.Lock()

    def list_tools(self):
        with self.lock:
            self.calls += 1
        self.entered.set()
        if not self.release.wait(3):
            raise TimeoutError("test did not release server")
        if self.fail:
            raise ConnectionError("late failure")
        return [{"type": "function", "function": {"name": f"mcp__{self.name}__ping"}}]

    def close(self):
        pass


class SingleFlightTests(unittest.TestCase):
    def setUp(self):
        self.servers = []
        self.callers = []
        self.deadline = patch.object(mcp, "_DISCOVERY_DEADLINE", 0.05)
        self.deadline.start()

    def tearDown(self):
        for server in self.servers:
            server.release.set()
        for caller in self.callers:
            caller.join(1)
        for server in self.servers:
            self.join_worker(server)
        self.deadline.stop()

    def server(self, name, *, fail=False):
        server = ControlledServer(name, fail=fail)
        self.servers.append(server)
        return server

    def join_worker(self, server):
        # Match only this test's unique server, never all discovery threads.
        for worker in threading.enumerate():
            if worker.name == f"zeline-mcp-discovery-{server.name}":
                worker.join(1)
                self.assertFalse(worker.is_alive())

    def test_concurrent_initial_callers_probe_each_server_only_once(self):
        server = self.server("singleflight-race")
        pool = mcp.MCPRegistry([server])
        results = []
        errors = []

        def lookup():
            try:
                results.append(pool.schemas())
            except Exception as exc:
                errors.append(exc)

        first = threading.Thread(target=lookup)
        second = threading.Thread(target=lookup)
        self.callers.extend([first, second])
        first.start()
        self.assertTrue(server.entered.wait(1))
        second.start()
        first.join(1)
        second.join(1)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [[], []])
        self.assertEqual(server.calls, 1, "Concurrent callers must share one discovery")
        server.release.set()
        self.join_worker(server)
        self.assertEqual(len(pool.schemas()), 1)
        self.assertEqual(server.calls, 1)

    def test_close_prevents_late_publication_and_new_probes(self):
        server = self.server("singleflight-close")
        pool = mcp.MCPRegistry([server])
        self.assertEqual(pool.schemas(), [])
        self.assertTrue(server.entered.is_set())
        pool.close()
        server.release.set()
        self.join_worker(server)
        self.assertEqual(pool.schemas(), [], "Closed registries discard late results")
        self.assertEqual(server.calls, 1)

    def test_staggered_successes_preserve_old_snapshots(self):
        first = self.server("singleflight-first")
        second = self.server("singleflight-second")
        pool = mcp.MCPRegistry([first, second])
        empty = pool.schemas()
        self.assertEqual(empty, [])
        first.release.set()
        self.join_worker(first)
        partial = pool.schemas()
        self.assertEqual(len(partial), 1)
        second.release.set()
        self.join_worker(second)
        self.assertEqual(len(pool.schemas()), 2)
        self.assertEqual(empty, [])
        self.assertEqual(len(partial), 1)
        self.assertEqual(pool.errors, {})

    def test_late_failure_replaces_pending_timeout(self):
        server = self.server("singleflight-failure", fail=True)
        pool = mcp.MCPRegistry([server])
        self.assertEqual(pool.schemas(), [])
        self.assertIn("deadline", pool.errors[server.name])
        server.release.set()
        self.join_worker(server)
        self.assertEqual(pool.schemas(), [])
        self.assertEqual(pool.errors[server.name], "ConnectionError: late failure")
        self.assertEqual(server.calls, 1)

    def test_close_before_discovery_never_launches_server(self):
        server = self.server("singleflight-never-started")
        pool = mcp.MCPRegistry([server])
        pool.close()
        self.assertEqual(pool.schemas(), [])
        self.assertEqual(server.calls, 0)


if __name__ == "__main__":
    unittest.main()
