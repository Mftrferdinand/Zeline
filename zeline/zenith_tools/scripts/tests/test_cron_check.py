import pathlib
import sys
import unittest
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import cron_check as C  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_wildcard(self):
        spec = C.parse("* * * * *")
        self.assertEqual(len(spec.minute), 60)
        self.assertEqual(len(spec.hour), 24)

    def test_step(self):
        spec = C.parse("*/15 * * * *")
        self.assertEqual(spec.minute, {0, 15, 30, 45})

    def test_range_with_step(self):
        spec = C.parse("0 9-17/2 * * *")
        self.assertEqual(spec.hour, {9, 11, 13, 15, 17})

    def test_aliases(self):
        self.assertEqual(C.parse("0 0 * * mon").day_of_week, {1})
        self.assertEqual(C.parse("0 0 1 jan *").month, {1})

    def test_dow_7_is_sunday(self):
        self.assertEqual(C.parse("0 0 * * 7").day_of_week, {0})

    def test_wrong_field_count(self):
        with self.assertRaises(C.CronError):
            C.parse("*/5 * *")
        with self.assertRaises(C.CronError):
            C.parse("* * * * * *")

    def test_out_of_range(self):
        with self.assertRaises(C.CronError):
            C.parse("61 * * * *")
        with self.assertRaises(C.CronError):
            C.parse("* 25 * * *")
        with self.assertRaises(C.CronError):
            C.parse("* * * 13 *")

    def test_reversed_range(self):
        with self.assertRaises(C.CronError):
            C.parse("10-5 * * * *")

    def test_garbage(self):
        with self.assertRaises(C.CronError):
            C.parse("hello world foo bar baz")


class MatchTest(unittest.TestCase):
    def test_every_five_minutes(self):
        spec = C.parse("*/5 * * * *")
        self.assertTrue(spec.matches(datetime(2026, 10, 23, 4, 20)))
        self.assertFalse(spec.matches(datetime(2026, 10, 23, 4, 21)))

    def test_daily(self):
        spec = C.parse("30 8 * * *")
        self.assertTrue(spec.matches(datetime(2026, 10, 23, 8, 30)))
        self.assertFalse(spec.matches(datetime(2026, 10, 23, 8, 31)))

    def test_specific_weekday(self):
        # 2026-10-23 is a Friday (weekday 4 -> cron 5)
        spec = C.parse("0 12 * * fri")
        self.assertTrue(spec.matches(datetime(2026, 10, 23, 12, 0)))
        self.assertFalse(spec.matches(datetime(2026, 10, 24, 12, 0)))

    def test_dom_dow_or_semantics(self):
        # Both restricted: fires on the 1st OR on Mondays (POSIX rule).
        spec = C.parse("0 0 1 * mon")
        self.assertTrue(spec.matches(datetime(2026, 11, 2, 0, 0)))   # Monday
        self.assertTrue(spec.matches(datetime(2026, 12, 1, 0, 0)))   # the 1st
        self.assertFalse(spec.matches(datetime(2026, 11, 5, 0, 0)))  # neither


class DescribeValidateTest(unittest.TestCase):
    def test_describe_daily(self):
        self.assertEqual(C.describe("0 0 * * *"), "Every day at 00:00")
        self.assertIn("08:30", C.describe("30 8 * * *"))

    def test_validate_ok(self):
        result = C.validate("*/10 * * * *")
        self.assertTrue(result["ok"])
        self.assertIn("description", result)

    def test_validate_error(self):
        result = C.validate("99 * * * *")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_next_run_progresses(self):
        nxt = C.next_run("*/15 * * * *", after=datetime(2026, 10, 23, 4, 30))
        self.assertEqual(nxt, datetime(2026, 10, 23, 4, 45))


if __name__ == "__main__":
    unittest.main()
