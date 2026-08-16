import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import exit_planner as X  # noqa: E402


class ExitPlannerTest(unittest.TestCase):
    def test_tranches_sum_to_100(self):
        for profile in ("conservative", "balanced", "degen"):
            p = X.build_plan(1000, profile=profile)
            self.assertAlmostEqual(sum(t.pct for t in p.tranches), 100.0)

    def test_hold_reduces_sell_tokens(self):
        p = X.build_plan(1000, hold_pct=20)
        self.assertEqual(p.sell_tokens, 800)

    def test_thin_liquidity_downgrades_degen(self):
        p = X.build_plan(1000, profile="degen", liquidity_thin=True)
        # degen di pool tipis → dipaksa balanced; cek note likuiditas ada
        self.assertTrue(any("LIKUIDITAS TIPIS" in n for n in p.notes))

    def test_vesting_note(self):
        p = X.build_plan(1000, vesting=True)
        self.assertTrue(any("VESTING" in n for n in p.notes))

    def test_always_routes_via_governor(self):
        p = X.build_plan(1000)
        self.assertTrue(any("Spend Governor" in n for n in p.notes))

    def test_invalid_profile_defaults_balanced(self):
        p = X.build_plan(1000, profile="nonsense")
        self.assertEqual(len(p.tranches), 3)


if __name__ == "__main__":
    unittest.main()
