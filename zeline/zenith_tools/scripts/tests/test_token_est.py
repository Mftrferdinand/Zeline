import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import token_est as T  # noqa: E402


class EstimateTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(T.estimate(""), 0)

    def test_english_prose(self):
        # ~4 chars/token: 400 chars of prose -> roughly 100 tokens.
        text = ("The quick brown fox jumps over the lazy dog. " * 8)  # ~368 chars
        est = T.estimate(text)
        self.assertTrue(60 <= est <= 130, est)

    def test_cjk_denser(self):
        cjk = "这是一个用于测试的中文句子" * 10
        latin = "a" * len(cjk)
        self.assertGreater(T.estimate(cjk), T.estimate(latin) * 1.5)

    def test_monotonic(self):
        a = "hello world " * 10
        self.assertEqual(T.estimate(a), T.estimate(a))
        self.assertGreater(T.estimate(a * 3), T.estimate(a))

    def test_nonempty_minimum(self):
        self.assertEqual(T.estimate("x"), 1)


class MessagesTest(unittest.TestCase):
    def test_overhead_and_content(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi!"},
        ]
        est = T.estimate_messages(msgs)
        self.assertEqual(est, 2 * T.MESSAGE_OVERHEAD +
                         T.estimate("You are helpful.") + T.estimate("Hi!"))

    def test_non_string_content(self):
        msgs = [{"role": "user", "content": {"file": "x.py", "lines": 10}}]
        self.assertGreater(T.estimate_messages(msgs), T.MESSAGE_OVERHEAD)

    def test_empty_messages(self):
        self.assertEqual(T.estimate_messages([]), 0)


class BreakdownTest(unittest.TestCase):
    def test_breakdown_shape(self):
        b = T.breakdown("hello world 12345")
        self.assertIn("estimated_tokens", b)
        self.assertIn("chars", b)
        self.assertIn("by_class_tokens", b)
        self.assertEqual(b["chars"], 17)

    def test_breakdown_total_consistent(self):
        b = T.breakdown("The cat sat on the mat near the window sill today.")
        self.assertEqual(b["estimated_tokens"], T.estimate(
            "The cat sat on the mat near the window sill today."))


if __name__ == "__main__":
    unittest.main()
