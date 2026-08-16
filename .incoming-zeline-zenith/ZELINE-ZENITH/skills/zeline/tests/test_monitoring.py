"""Tests for monitoring.py: RPCRouter round-robin failover."""
import _bootstrap  # noqa: F401

import asyncio
import unittest

import monitoring as M


class TestRPCRouter(unittest.TestCase):
    def test_requires_at_least_one_url(self):
        with self.assertRaises(ValueError):
            M.RPCRouter([])

    def test_current_starts_at_first(self):
        r = M.RPCRouter(["a", "b", "c"])
        self.assertEqual(r.current, "a")

    def test_rotate_wraps_around(self):
        r = M.RPCRouter(["a", "b"])
        r._rotate()
        self.assertEqual(r.current, "b")
        r._rotate()
        self.assertEqual(r.current, "a")

    def test_failover_rotates_until_success(self):
        r = M.RPCRouter(["a", "b", "c"])
        calls = {"n": 0}

        def fn(_w3):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("boom")
            return "ok"

        # patch w3() so it doesn't touch the (stubbed) Web3 provider
        r.w3 = lambda: None
        out = asyncio.run(r.call_with_failover(fn))
        self.assertEqual(out, "ok")
        self.assertEqual(calls["n"], 3)

    def test_failover_raises_when_all_fail(self):
        r = M.RPCRouter(["a", "b"])
        r.w3 = lambda: None

        def fn(_w3):
            raise RuntimeError("down")

        with self.assertRaises(RuntimeError):
            asyncio.run(r.call_with_failover(fn))


if __name__ == "__main__":
    unittest.main()
