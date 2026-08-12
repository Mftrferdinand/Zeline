import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import rugcheck as RC  # noqa: E402


class RugcheckTest(unittest.TestCase):
    def test_honeypot_is_danger(self):
        v = RC.check(RC.SignalSet(is_honeypot=True))
        self.assertEqual(v.verdict, "DANGER")
        self.assertTrue(v.critical)

    def test_unlocked_lp_is_danger(self):
        v = RC.check(RC.SignalSet(lp_locked=False))
        self.assertEqual(v.verdict, "DANGER")

    def test_clean_project_is_safe(self):
        v = RC.check(RC.SignalSet(
            contract_verified=True, is_honeypot=False, buy_tax_pct=2, sell_tax_pct=3,
            lp_locked=True, lp_lock_days=365, owner_renounced=True,
            top10_holders_pct=20, age_days=120, liquidity_usd=500000,
            owner_can_mint=False, owner_can_pause=False, proxy_upgradeable=False))
        self.assertEqual(v.verdict, "SAFE")
        self.assertEqual(v.critical, [])

    def test_warning_signals_accumulate_caution(self):
        v = RC.check(RC.SignalSet(
            contract_verified=False, is_honeypot=False, lp_locked=True, lp_lock_days=365,
            owner_renounced=True, sell_tax_pct=15, top10_holders_pct=60,
            liquidity_usd=200000, age_days=90))
        self.assertEqual(v.verdict, "CAUTION")
        self.assertTrue(v.warnings)

    def test_unknowns_listed(self):
        v = RC.check(RC.SignalSet())
        self.assertTrue(v.unknowns)

    def test_mint_without_renounce_is_critical(self):
        v = RC.check(RC.SignalSet(owner_can_mint=True, owner_renounced=False,
                                  lp_locked=True, is_honeypot=False))
        self.assertEqual(v.verdict, "DANGER")


if __name__ == "__main__":
    unittest.main()
