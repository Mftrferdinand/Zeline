import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import revenue_engine as R  # noqa: E402


class TokenBucketTest(unittest.TestCase):
    def test_consume_and_deficit_wait(self):
        t = [0.0]
        b = R.TokenBucket(10, capacity=2, clock=lambda: t[0])
        self.assertEqual(b.consume(), 0.0)
        self.assertEqual(b.consume(), 0.0)
        self.assertAlmostEqual(b.consume(), 0.1, places=6)  # deficit 1 token / 10ps

    def test_refill_over_time(self):
        t = [0.0]
        b = R.TokenBucket(10, capacity=2, clock=lambda: t[0])
        b.consume()
        b.consume()
        t[0] = 0.05  # +0.5 token
        self.assertAlmostEqual(b.consume(), 0.05, places=6)

    def test_rejects_bad_rate(self):
        with self.assertRaises(ValueError):
            R.TokenBucket(0)


class BackoffTest(unittest.TestCase):
    def test_exponential_and_cap(self):
        self.assertEqual(R.backoff_delay(0, base=0.5, factor=2, cap=30), 0.5)
        self.assertEqual(R.backoff_delay(1, base=0.5, factor=2, cap=30), 1.0)
        self.assertEqual(R.backoff_delay(2, base=0.5, factor=2, cap=30), 2.0)
        self.assertEqual(R.backoff_delay(20, base=0.5, factor=2, cap=30), 30)

    def test_jitter_within_bounds(self):
        for r in (0.0, 0.5, 1.0):
            d = R.backoff_delay(2, base=0.5, factor=2, cap=30, jitter=0.5, rng=lambda: r)
            self.assertGreaterEqual(d, 0.0)
            self.assertLessEqual(d, 30.0)


class CheckpointTest(unittest.TestCase):
    def test_persist_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.jsonl")
            c = R.Checkpoint(p)
            self.assertFalse(c.is_done("a"))
            c.mark("a")
            c.mark("a")  # idempotent
            c.mark("b")
            c2 = R.Checkpoint(p)  # resume from file
            self.assertTrue(c2.is_done("a"))
            self.assertTrue(c2.is_done("b"))
            self.assertEqual(len(c2), 2)


class DedupeTest(unittest.TestCase):
    def test_scalar(self):
        self.assertEqual(list(R.dedupe([1, 1, 2, 3, 3, 3])), [1, 2, 3])

    def test_keyed(self):
        out = list(R.dedupe([{"k": 1}, {"k": 1}, {"k": 2}], key=lambda x: x["k"]))
        self.assertEqual(out, [{"k": 1}, {"k": 2}])


class BulkRunnerTest(unittest.TestCase):
    def _runner(self, worker, **kw):
        kw.setdefault("max_workers", 1)
        kw.setdefault("sleep", lambda *_: None)
        return R.BulkRunner(worker, **kw)

    def test_all_succeed(self):
        seen = []
        rep = self._runner(lambda t: seen.append(t) or t * 2).run([1, 2, 3])
        self.assertEqual(len(rep.succeeded), 3)
        self.assertEqual(rep.failed, [])
        self.assertEqual(sorted(seen), [1, 2, 3])
        self.assertEqual(rep.total, 3)

    def test_checkpoint_skips_done(self):
        with tempfile.TemporaryDirectory() as d:
            cp = R.Checkpoint(os.path.join(d, "s.jsonl"))
            cp.mark("2")
            rep = self._runner(lambda t: t, checkpoint=cp).run([1, 2, 3])
            self.assertIn("2", rep.skipped)
            self.assertEqual(len(rep.succeeded), 2)

    def test_retry_then_success(self):
        calls = {"n": 0}

        def worker(_):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("transient")
            return "ok"

        rep = self._runner(worker, max_retries=3).run([99])
        self.assertEqual(len(rep.succeeded), 1)
        self.assertEqual(rep.succeeded[0].attempts, 3)

    def test_permanent_failure_records_error(self):
        def worker(_):
            raise RuntimeError("nope")

        rep = self._runner(worker, max_retries=2).run([1])
        self.assertEqual(len(rep.failed), 1)
        self.assertEqual(rep.failed[0].attempts, 3)  # 1 try + 2 retries
        self.assertIn("nope", rep.failed[0].error)


if __name__ == "__main__":
    unittest.main()
