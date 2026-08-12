import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import eval as EV  # noqa: E402


class RegressionSuiteTest(unittest.TestCase):
    def test_baseline_then_stable_passes(self):
        with tempfile.TemporaryDirectory() as d:
            store = pathlib.Path(d) / "golden.json"
            s = EV.RegressionSuite("sq", store).add("two", 2).add("three", 3)
            s.record_baseline(lambda x: x * x)
            # reload fresh suite from disk
            s2 = EV.RegressionSuite("sq", store).add("two", 2).add("three", 3)
            res = s2.run(lambda x: x * x)
            self.assertTrue(res.ok)
            self.assertEqual(len(res.passed), 2)

    def test_detects_regression(self):
        with tempfile.TemporaryDirectory() as d:
            store = pathlib.Path(d) / "golden.json"
            s = EV.RegressionSuite("sq", store).add("two", 2)
            s.record_baseline(lambda x: x * x)
            res = s.run(lambda x: x * x + 1)   # output berubah → regress
            self.assertFalse(res.ok)
            self.assertEqual(len(res.regressed), 1)
            self.assertIn("FAIL", res.summary())

    def test_new_case_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            store = pathlib.Path(d) / "golden.json"
            s = EV.RegressionSuite("sq", store).add("two", 2)
            s.record_baseline(lambda x: x * x)
            s.add("four", 4)                   # case baru belum di golden
            res = s.run(lambda x: x * x)
            self.assertIn("four", res.new_cases)

    def test_error_captured(self):
        with tempfile.TemporaryDirectory() as d:
            store = pathlib.Path(d) / "golden.json"
            s = EV.RegressionSuite("boom", store).add("x", 0)
            s.record_baseline(lambda x: 1)

            def explode(x):
                raise ValueError("kaboom")

            res = s.run(explode)
            self.assertFalse(res.ok)
            self.assertEqual(len(res.errored), 1)

    def test_normalize_strips_volatile(self):
        with tempfile.TemporaryDirectory() as d:
            store = pathlib.Path(d) / "golden.json"
            norm = lambda out: {k: v for k, v in out.items() if k != "ts"}
            s = EV.RegressionSuite("n", store, normalize=norm).add("c", 1)
            s.record_baseline(lambda x: {"val": x, "ts": 111})
            res = s.run(lambda x: {"val": x, "ts": 999})  # ts beda, tapi di-strip
            self.assertTrue(res.ok)


if __name__ == "__main__":
    unittest.main()
