import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import scam_sentinel as S  # noqa: E402


class ScamSentinelTest(unittest.TestCase):
    def test_levenshtein(self):
        self.assertEqual(S.levenshtein("abc", "abc"), 0)
        self.assertEqual(S.levenshtein("zkprotox", "zkprot0x"), 1)
        self.assertEqual(S.levenshtein("", "abc"), 3)

    def test_root_strips_scheme_and_tld(self):
        self.assertEqual(S._root("https://www.zkprotox.xyz/claim"), "zkprotox")

    def test_typosquat_with_seed_phrase_is_scam(self):
        v = S.analyze("zkprot0x.xyz", "zkprotox.xyz",
                      S.PageSignals(asks_seed_phrase=True, ssl_age_days=3, claim_button=True))
        self.assertEqual(v.verdict, "likely-scam")
        self.assertEqual(v.typosquat_distance, 1)
        self.assertGreaterEqual(v.risk_score, 60)

    def test_official_domain_safe(self):
        v = S.analyze("zkprotox.xyz", "zkprotox.xyz", S.PageSignals(ssl_age_days=400))
        self.assertEqual(v.verdict, "likely-safe")
        self.assertEqual(v.typosquat_distance, 0)

    def test_substring_impersonation(self):
        v = S.analyze("zkprotox-airdrop.com", "zkprotox.xyz", S.PageSignals(ssl_age_days=400))
        self.assertGreaterEqual(v.risk_score, 25)

    def test_warning_post_only_for_risky(self):
        safe = S.analyze("zkprotox.xyz", "zkprotox.xyz")
        self.assertEqual(S.warning_post(safe, "ZkProtoX"), "")
        scam = S.analyze("zkprot0x.xyz", "zkprotox.xyz",
                         S.PageSignals(asks_seed_phrase=True, ssl_age_days=2))
        post = S.warning_post(scam, "ZkProtoX")
        self.assertIn("ZkProtoX", post)
        self.assertIn("seed phrase", post.lower())

    def test_score_bounds(self):
        v = S.analyze("evil.com", "real.com",
                      S.PageSignals(asks_seed_phrase=True, has_drainer_signature=True,
                                    external_redirect=True, ssl_age_days=1, claim_button=True))
        self.assertLessEqual(v.risk_score, 100)


if __name__ == "__main__":
    unittest.main()
