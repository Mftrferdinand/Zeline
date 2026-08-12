import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import dryrun as D  # noqa: E402


class DryRunTest(unittest.TestCase):
    def tearDown(self):
        D.set_dry_run(False)
        D.clear_plan()

    def test_context_manager_scope(self):
        self.assertFalse(D.is_dry_run())
        with D.dry_run():
            self.assertTrue(D.is_dry_run())
        self.assertFalse(D.is_dry_run())

    def test_plan_collected_in_scope(self):
        with D.dry_run():
            D.plan("swap", chain=1, detail="100 USDC→ETH", est_usd=100)
            D.plan("mint", chain=1, detail="zora x3")
            actions = D.get_plan()
            self.assertEqual(len(actions), 2)
            self.assertEqual(actions[0].action, "swap")
            self.assertEqual(actions[0].est_usd, 100)

    def test_render_plan_totals(self):
        with D.dry_run():
            D.plan("swap", est_usd=100)
            D.plan("bridge", est_usd=50)
            txt = D.render_plan()
            self.assertIn("150", txt)
            self.assertIn("TIDAK ada", txt)

    def test_engine_pattern_blocks_broadcast(self):
        broadcasts = []

        def fake_swap():
            if D.is_dry_run():
                D.plan("swap", detail="dry")
                return "planned"
            broadcasts.append("real")
            return "broadcast"

        with D.dry_run():
            self.assertEqual(fake_swap(), "planned")
        self.assertEqual(fake_swap(), "broadcast")
        self.assertEqual(broadcasts, ["real"])


if __name__ == "__main__":
    unittest.main()
