"""Tests for governor.py (SpendGovernor / circuit-breaker). Pure stdlib logic."""
import _bootstrap  # noqa: F401

import os
import tempfile
import unittest
from pathlib import Path

import governor as G


_OPEN_GOVS = []


def _gov(tmp, **limit_kw):
    limits = G.GovernorLimits(**limit_kw)
    db = Path(tmp) / "gov.db"
    gov = G.SpendGovernor(limits=limits, db_path=db, session_id="t-sess")
    _OPEN_GOVS.append(gov)          # ditutup di tearDownModule (no ResourceWarning)
    return gov


def tearDownModule():
    for g in _OPEN_GOVS:
        g.close()
    _OPEN_GOVS.clear()


class TestGovernorLimitsFromEnv(unittest.TestCase):
    def test_defaults_when_unset(self):
        for k in ("HERMES_MAX_TX_USD", "HERMES_DAILY_CAP_USD", "HERMES_SESSION_CAP_USD",
                  "HERMES_MAX_SLIPPAGE_PCT", "HERMES_MAX_GAS_MULTIPLE",
                  "HERMES_MAX_TX_PER_MIN", "HERMES_REQUIRE_SIM"):
            os.environ.pop(k, None)
        lim = G.GovernorLimits.from_env()
        self.assertIsNone(lim.max_tx_usd)
        self.assertEqual(lim.max_slippage_pct, 5.0)
        self.assertEqual(lim.max_gas_multiple, 4.0)
        self.assertEqual(lim.max_tx_per_min, 12)
        self.assertTrue(lim.require_simulation)

    def test_reads_env(self):
        os.environ["HERMES_MAX_TX_USD"] = "250"
        os.environ["HERMES_REQUIRE_SIM"] = "0"
        try:
            lim = G.GovernorLimits.from_env()
            self.assertEqual(lim.max_tx_usd, 250.0)
            self.assertFalse(lim.require_simulation)
        finally:
            os.environ.pop("HERMES_MAX_TX_USD", None)
            os.environ.pop("HERMES_REQUIRE_SIM", None)


class TestAuthorize(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        # tutup koneksi DB sebelum hapus tmp dir (cegah ResourceWarning + lock)
        for g in _OPEN_GOVS:
            g.close()
        _OPEN_GOVS.clear()
        self._tmp.cleanup()

    def test_allow_clean_intent(self):
        gov = _gov(self.tmp, max_tx_usd=500, require_simulation=True)
        intent = G.TxIntent(wallet="0xabc", chain_id=1, action="swap",
                            usd_value=100.0, slippage_pct=1.0, simulated_ok=True)
        d = gov.authorize(intent)
        self.assertEqual(d.verdict, "allow")
        self.assertTrue(d.allowed)

    def test_block_when_not_simulated(self):
        gov = _gov(self.tmp, require_simulation=True)
        intent = G.TxIntent(wallet="0xabc", chain_id=1, action="swap", simulated_ok=None)
        d = gov.authorize(intent)
        self.assertEqual(d.verdict, "block")
        self.assertFalse(d.allowed)
        self.assertTrue(any("simulasi" in r for r in d.reasons))

    def test_block_over_per_tx_cap(self):
        gov = _gov(self.tmp, max_tx_usd=100, require_simulation=False)
        intent = G.TxIntent(wallet="0xabc", chain_id=1, action="swap", usd_value=1000.0)
        d = gov.authorize(intent)
        self.assertEqual(d.verdict, "block")

    def test_block_high_slippage(self):
        gov = _gov(self.tmp, max_slippage_pct=5.0, require_simulation=False)
        intent = G.TxIntent(wallet="0xabc", chain_id=1, action="swap", slippage_pct=42.0)
        self.assertEqual(gov.authorize(intent).verdict, "block")

    def test_daily_cap_accumulates(self):
        gov = _gov(self.tmp, daily_cap_usd=150, require_simulation=False)
        i1 = G.TxIntent(wallet="0xw1", chain_id=1, action="swap", usd_value=100.0)
        self.assertEqual(gov.authorize(i1).verdict, "allow")
        gov.record(i1, "0xhash1")
        i2 = G.TxIntent(wallet="0xw1", chain_id=1, action="swap", usd_value=100.0)
        self.assertEqual(gov.authorize(i2).verdict, "block")  # 100+100 > 150

    def test_daily_cap_is_per_wallet(self):
        gov = _gov(self.tmp, daily_cap_usd=150, require_simulation=False)
        i1 = G.TxIntent(wallet="0xw1", chain_id=1, action="swap", usd_value=100.0)
        gov.record(i1, "0xh")
        # different wallet should still be allowed
        i2 = G.TxIntent(wallet="0xw2", chain_id=1, action="swap", usd_value=100.0)
        self.assertEqual(gov.authorize(i2).verdict, "allow")

    def test_rate_limit_trips_killswitch(self):
        gov = _gov(self.tmp, max_tx_per_min=3, require_simulation=False)
        for n in range(3):
            i = G.TxIntent(wallet="0xw", chain_id=1, action="swap", usd_value=1.0)
            gov.record(i, f"0x{n}")
        i = G.TxIntent(wallet="0xw", chain_id=1, action="swap", usd_value=1.0)
        d = gov.authorize(i)
        self.assertEqual(d.verdict, "halt")
        # once tripped, everything halts until manual reset
        self.assertEqual(gov.authorize(i).verdict, "halt")
        gov.reset_killswitch()

    def test_killswitch_blocks_then_resets(self):
        gov = _gov(self.tmp, require_simulation=False)
        gov.trip("manual test")
        i = G.TxIntent(wallet="0xw", chain_id=1, action="swap", usd_value=1.0)
        self.assertEqual(gov.authorize(i).verdict, "halt")
        gov.reset_killswitch()
        self.assertEqual(gov.authorize(i).verdict, "allow")


class TestDecision(unittest.TestCase):
    def test_summary_and_allowed(self):
        d = G.Decision("allow")
        self.assertTrue(d.allowed)
        self.assertIn("ALLOW", d.summary())
        d2 = G.Decision("block", ["too big"])
        self.assertFalse(d2.allowed)
        self.assertIn("too big", d2.summary())


if __name__ == "__main__":
    unittest.main()
