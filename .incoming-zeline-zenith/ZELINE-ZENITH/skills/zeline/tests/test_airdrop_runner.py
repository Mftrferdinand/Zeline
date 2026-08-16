"""Tests for airdrop_runner.py: jitter bounds, dedupe state, TaskSpec defaults."""
import _bootstrap  # noqa: F401

import tempfile
import unittest
from pathlib import Path

import airdrop_runner as A


class TestTaskSpecDefaults(unittest.TestCase):
    def test_params_defaults_to_independent_dict(self):
        async def _noop():
            return None
        a = A.TaskSpec(name="t1", func=_noop)
        b = A.TaskSpec(name="t2", func=_noop)
        self.assertEqual(a.params, {})
        a.params["k"] = 1
        self.assertEqual(b.params, {})  # would leak with the mutable-default bug


class TestRunState(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "runs.db"
        self._states = []

    def _state(self):
        st = A.RunState(db_path=self.db)
        self._states.append(st)
        return st

    def tearDown(self):
        for st in self._states:        # tutup koneksi sebelum hapus tmp dir
            st.close()
        self._tmp.cleanup()

    def test_records_and_dedupes_success(self):
        st = self._state()
        self.assertFalse(st.already_done("run1", "w1", "swap"))
        st.record("run1", "w1", "swap", "success", tx_hash="0x1")
        self.assertTrue(st.already_done("run1", "w1", "swap"))

    def test_error_status_is_not_done(self):
        st = self._state()
        st.record("run1", "w1", "swap", "error", error="boom")
        self.assertFalse(st.already_done("run1", "w1", "swap"))


class TestJitter(unittest.TestCase):
    def _scheduler(self, pct):
        sched = A.WalletScheduler.__new__(A.WalletScheduler)
        sched.jitter = pct
        return sched

    def test_none_base_returns_none(self):
        self.assertIsNone(self._scheduler(15)._jitter(None))

    def test_within_bounds(self):
        sched = self._scheduler(15)
        base = 100.0
        for _ in range(500):
            v = sched._jitter(base)
            self.assertGreaterEqual(v, base * 0.85 - 1e-6)
            self.assertLessEqual(v, base * 1.15 + 1e-6)

    def test_zero_jitter_returns_base(self):
        self.assertEqual(self._scheduler(0)._jitter(50.0), 50.0)


if __name__ == "__main__":
    unittest.main()
