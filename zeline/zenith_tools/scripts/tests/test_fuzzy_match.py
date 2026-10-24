import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import fuzzy_match as F  # noqa: E402


class SimilarityTest(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(F.similarity("whatsapp", "whatsapp"), 1.0)

    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(F.similarity("Hello, World!", "hello world"), 1.0)

    def test_typo_close(self):
        score = F.similarity("whatsapp", "whatsap")
        self.assertGreater(score, 0.85)

    def test_unrelated(self):
        score = F.similarity("banana", "xyzzy")
        self.assertLess(score, 0.3)

    def test_empty(self):
        self.assertEqual(F.similarity("", ""), 1.0)
        self.assertEqual(F.similarity("a", ""), 0.0)


class BestMatchTest(unittest.TestCase):
    CHOICES = ["telegram", "whatsapp", "discord", "slack"]

    def test_finds_right_choice(self):
        choice, score = F.best_match("whatsap", self.CHOICES)
        self.assertEqual(choice, "whatsapp")
        self.assertGreaterEqual(score, 0.6)

    def test_below_threshold_returns_none(self):
        choice, score = F.best_match("zzzzzzz", self.CHOICES)
        self.assertIsNone(choice)
        self.assertEqual(score, 0.0)

    def test_exact_match(self):
        choice, _ = F.best_match("slack", self.CHOICES)
        self.assertEqual(choice, "slack")


class MatchAllTest(unittest.TestCase):
    def test_sorted_desc_and_filtered(self):
        results = F.match_all("telegam", ["telegram", "telemarketing", "slack"])
        self.assertTrue(all(results[i][1] >= results[i + 1][1]
                            for i in range(len(results) - 1)))
        self.assertEqual(results[0][0], "telegram")
        self.assertTrue(all(s >= 0.6 for _, s in results))


class DedupeTest(unittest.TestCase):
    def test_clusters_similar(self):
        items = ["config.yaml", "config.yml", "README.md", "readme.md", "main.py"]
        clusters = F.dedupe(items, threshold=0.8)
        joined = [sorted(c) for c in clusters]
        self.assertIn(["README.md", "readme.md"], joined)
        self.assertIn(["config.yaml", "config.yml"], joined)
        self.assertIn(["main.py"], joined)

    def test_no_cross_cluster(self):
        clusters = F.dedupe(["apple", "zebra"], threshold=0.9)
        self.assertEqual(len(clusters), 2)


if __name__ == "__main__":
    unittest.main()
