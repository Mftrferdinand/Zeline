import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import farm_roi as F  # noqa: E402


class FarmRoiTest(unittest.TestCase):
    def test_profitable_keep(self):
        p = F.FarmPosition("Good", gas_spent_usd=100, est_airdrop_usd=2000,
                           confidence=0.6, last_activity_days=2)
        v = F.evaluate(p)
        self.assertEqual(v.action, "keep")
        self.assertGreater(v.net_usd, 0)
        self.assertGreater(v.roi, 1)

    def test_loss_drop(self):
        p = F.FarmPosition("Bad", gas_spent_usd=80, est_airdrop_usd=50,
                           confidence=0.1, last_activity_days=60)
        v = F.evaluate(p)
        self.assertEqual(v.action, "drop")
        self.assertLess(v.net_usd, 0)

    def test_idle_wallet_note(self):
        p = F.FarmPosition("Idle", gas_spent_usd=10, est_airdrop_usd=5,
                           confidence=0.1, last_activity_days=45)
        notes = " ".join(F.evaluate(p).notes)
        self.assertIn("nganggur", notes)

    def test_zero_cost_infinite_roi(self):
        p = F.FarmPosition("Free", gas_spent_usd=0, est_airdrop_usd=100, confidence=1.0)
        v = F.evaluate(p)
        self.assertEqual(v.roi, float("inf"))
        self.assertEqual(v.action, "keep")

    def test_portfolio_aggregates_and_sorts(self):
        port = [
            F.FarmPosition("A", gas_spent_usd=10, est_airdrop_usd=1000, confidence=0.5),
            F.FarmPosition("B", gas_spent_usd=200, est_airdrop_usd=10, confidence=0.1),
        ]
        s = F.analyze(port)
        self.assertEqual(s.verdicts[0].project, "A")  # higher net first
        self.assertAlmostEqual(s.total_cost_usd, 210.0)
        self.assertIn("keep", s.by_action)


if __name__ == "__main__":
    unittest.main()
