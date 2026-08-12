import pathlib
import sqlite3
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import router_log as R  # noqa: E402


def _mem(margin=0.2):
    return R.RouterLog(conn=sqlite3.connect(":memory:"), tie_margin=margin)


class RouterLogTest(unittest.TestCase):
    def test_clear_winner_not_tie(self):
        with _mem() as rl:
            d = rl.log("deploy nginx ke vps", {"sk2": 10, "sk16": 3})
            self.assertEqual(d.primary, "sk2")
            self.assertFalse(d.was_tie)

    def test_close_scores_is_tie(self):
        with _mem() as rl:
            d = rl.log("bulk scrape api", {"sk30": 9, "sk12": 8})
            self.assertTrue(d.was_tie)
            self.assertEqual(d.runner_up, "sk12")

    def test_tune_report_groups_pairs(self):
        with _mem() as rl:
            rl.log("bulk a", {"sk30": 9, "sk12": 8})
            rl.log("bulk b", {"sk12": 9, "sk30": 8})   # urutan kebalik → pair sama
            rl.log("clear", {"sk2": 10, "sk1": 1})
            pairs = rl.tune_report(min_ties=2)
            self.assertEqual(len(pairs), 1)
            self.assertEqual({pairs[0].a, pairs[0].b}, {"sk12", "sk30"})
            self.assertEqual(pairs[0].ties, 2)

    def test_stats(self):
        with _mem() as rl:
            rl.log("x", {"sk1": 10, "sk2": 1})
            rl.log("y", {"sk1": 9, "sk2": 8})
            st = rl.stats()
            self.assertEqual(st["total"], 2)
            self.assertEqual(st["ties"], 1)
            self.assertEqual(st["tie_rate"], 0.5)

    def test_empty_scores_no_crash(self):
        with _mem() as rl:
            d = rl.log("hmm", {})
            self.assertEqual(d.primary, "")
            self.assertFalse(d.was_tie)


if __name__ == "__main__":
    unittest.main()
