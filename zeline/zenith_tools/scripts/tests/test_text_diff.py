import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import text_diff as T  # noqa: E402


class UnifiedDiffTest(unittest.TestCase):
    def test_identical_empty(self):
        self.assertEqual(T.unified_diff("same", "same"), "")

    def test_basic_diff_contains_changes(self):
        d = T.unified_diff("line1\nline2", "line1\nline2x", "a.txt", "b.txt")
        self.assertIn("--- a.txt", d)
        self.assertIn("+++ b.txt", d)
        self.assertIn("-line2", d)
        self.assertIn("+line2x", d)


class DiffStatsTest(unittest.TestCase):
    def test_counts(self):
        stats = T.diff_stats("a\nb\nc", "a\nx\nc\nd")
        self.assertEqual(stats["added"], 2)   # x, d
        self.assertEqual(stats["removed"], 1)  # b
        self.assertEqual(stats["unchanged"], 2)  # a, c

    def test_identical_similarity_one(self):
        stats = T.diff_stats("a\nb", "a\nb")
        self.assertEqual(stats["added"], 0)
        self.assertEqual(stats["removed"], 0)
        self.assertEqual(stats["similarity"], 1.0)

    def test_both_empty(self):
        stats = T.diff_stats("", "")
        self.assertEqual(stats["similarity"], 1.0)

    def test_total_replacement(self):
        stats = T.diff_stats("a", "b")
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["removed"], 1)
        self.assertEqual(stats["similarity"], 0.0)


class SummarizeDiffTest(unittest.TestCase):
    def test_none_when_identical(self):
        self.assertIsNone(T.summarize_diff("x", "x"))

    def test_summary_reports_hunks(self):
        s = T.summarize_diff("a\nb\nc", "a\nX\nc")
        self.assertIsNotNone(s)
        self.assertIn("+1 -1", s)
        self.assertIn("X", s)

    def test_max_hunks_limit(self):
        # Alternating changed/unchanged lines force separate hunks
        # even at context=0.
        a = "\n".join(f"keep{i}\nold{i}" for i in range(50))
        b = "\n".join(f"keep{i}\nnew{i}" for i in range(50))
        s = T.summarize_diff(a, b, max_hunks=2)
        assert s is not None
        self.assertIn("and 48 more hunk", s)


if __name__ == "__main__":
    unittest.main()
