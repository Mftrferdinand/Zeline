import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import hook_lab as H  # noqa: E402


class HookLabTest(unittest.TestCase):
    def test_strong_hook_beats_weak(self):
        strong = H.score_hook("3 rahasia airdrop yang bikin cuan sekarang")
        weak = H.score_hook("airdrop info")
        self.assertGreater(strong.score, weak.score)

    def test_signals_detected(self):
        s = H.score_hook("5 cara cuan sekarang juga?")
        self.assertIn("angka", s.signals)
        self.assertIn("pertanyaan", s.signals)

    def test_score_bounds(self):
        s = H.score_hook("3 rahasia gila wajib gratis sekarang terbukti instan cuan?")
        self.assertLessEqual(s.score, 100)
        self.assertGreaterEqual(s.score, 0)

    def test_all_caps_penalized(self):
        normal = H.score_hook("cara cuan airdrop sekarang juga")
        caps = H.score_hook("CARA CUAN AIRDROP SEKARANG JUGA")
        self.assertLess(caps.score, normal.score)
        self.assertIn("caps-berlebih", caps.signals)

    def test_too_long_penalized(self):
        s = H.score_hook(" ".join(["kata"] * 25))
        self.assertIn("kepanjangan", s.signals)

    def test_generate_hooks(self):
        hooks = H.generate_hooks("Airdrop Base", n_points=3)
        self.assertTrue(hooks)
        self.assertTrue(any("3" in h for h in hooks))

    def test_rank_sorted_desc(self):
        ranked = H.rank_hooks(H.generate_hooks("Airdrop Base", 3))
        scores = [h.score for h in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
