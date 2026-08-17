import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import eligibility as E  # noqa: E402


class EligibilityTest(unittest.TestCase):
    def test_strong_wallet_scores_high(self):
        w = E.WalletStats(tx_count=60, age_days=300, volume_usd=20000,
                          unique_contracts=25, distinct_chains=4, active_weeks=20,
                          bridged=True, holds_lp_or_stake=True, last_active_days_ago=2)
        r = E.score_wallet(w)
        self.assertGreaterEqual(r.score, 70)
        self.assertEqual(r.band, "strong")

    def test_weak_wallet_scores_low_with_gaps(self):
        w = E.WalletStats(tx_count=2, age_days=5, volume_usd=50,
                          unique_contracts=1, distinct_chains=1, active_weeks=1)
        r = E.score_wallet(w)
        self.assertLess(r.score, 40)
        self.assertEqual(r.band, "weak")
        self.assertTrue(r.gaps)

    def test_score_capped_at_100(self):
        w = E.WalletStats(tx_count=1000, age_days=2000, volume_usd=1e7,
                          unique_contracts=500, distinct_chains=20, active_weeks=200,
                          bridged=True, holds_lp_or_stake=True)
        self.assertLessEqual(E.score_wallet(w).score, 100)

    def test_dormant_flag(self):
        w = E.WalletStats(tx_count=40, age_days=200, volume_usd=10000,
                          unique_contracts=15, distinct_chains=3, active_weeks=12,
                          last_active_days_ago=90)
        flags = " ".join(E.score_wallet(w).flags)
        self.assertIn("dormant", flags)

    def test_custom_rubric(self):
        rubric = [E.Criterion("tx_count", 10, 1.0, "tx")]
        w = E.WalletStats(tx_count=10)
        r = E.score_wallet(w, rubric=rubric, bonus={})
        self.assertEqual(r.score, 100)


if __name__ == "__main__":
    unittest.main()
