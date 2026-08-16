import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import alpha_radar as A  # noqa: E402


class AlphaRadarTest(unittest.TestCase):
    def test_hot_project_scores_high(self):
        s = A.ProjectSignal("Hot", funded_usd=40_000_000, points_program=True,
                            testnet_live=True, github_commits_30d=90, backed_by_tier1=True,
                            days_since_last_round=300, social_growth_pct=70,
                            governance_active=True)
        r = A.score_project(s)
        self.assertGreaterEqual(r.score, 75)
        self.assertEqual(r.tier, "hot")
        self.assertEqual(r.effort, "high")

    def test_existing_token_capped(self):
        s = A.ProjectSignal("Old", funded_usd=50_000_000, has_token=True,
                            points_program=True, testnet_live=True)
        r = A.score_project(s)
        self.assertLessEqual(r.score, 30)

    def test_cold_project(self):
        s = A.ProjectSignal("Cold")
        self.assertEqual(A.score_project(s).tier, "cold")

    def test_score_bounds(self):
        s = A.ProjectSignal("Max", funded_usd=1e9, points_program=True, testnet_live=True,
                            github_commits_30d=10000, backed_by_tier1=True,
                            governance_active=True, social_growth_pct=999,
                            days_since_last_round=400, testnet_contracts=99)
        self.assertLessEqual(A.score_project(s).score, 100)
        self.assertGreaterEqual(A.score_project(s).score, 0)

    def test_rank_orders_desc(self):
        sigs = [A.ProjectSignal("low"),
                A.ProjectSignal("high", points_program=True, testnet_live=True,
                                backed_by_tier1=True, days_since_last_round=300)]
        ranked = A.rank(sigs)
        self.assertEqual(ranked[0].name, "high")
        self.assertGreaterEqual(ranked[0].score, ranked[1].score)

    def test_rank_top_n(self):
        sigs = [A.ProjectSignal(f"p{i}", points_program=(i % 2 == 0)) for i in range(5)]
        self.assertEqual(len(A.rank(sigs, top=2)), 2)


if __name__ == "__main__":
    unittest.main()
