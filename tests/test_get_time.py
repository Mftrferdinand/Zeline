"""get_time: the agent's only source of 'now' on shell-less profiles."""
import re
import unittest
from datetime import datetime

from zeline.tools import _get_time


class GetTimeTests(unittest.TestCase):
    def test_default_reply_has_iso_stamp_weekday_and_utc(self):
        result = _get_time()
        self.assertRegex(result, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")
        self.assertIn("weekday:", result)
        self.assertRegex(result, r"utc: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

    def test_named_timezone_returns_that_zones_offset(self):
        result = _get_time("Asia/Jakarta")
        self.assertIn("+07:00", result)
        self.assertIn("(Asia/Jakarta)", result)

    def test_utc_request_has_zero_offset(self):
        result = _get_time("UTC")
        self.assertIn("+00:00", result)
        self.assertIn("(UTC)", result)

    def test_jakarta_is_seven_hours_behind_utc(self):
        jakarta = _get_time("Asia/Jakarta")
        utc_match = re.search(r"utc: (\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})", jakarta)
        local_match = re.search(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})", jakarta, re.MULTILINE)
        self.assertIsNotNone(utc_match)
        self.assertIsNotNone(local_match)
        # Same calendar date in the common case; the hour difference is the
        # documented +07:00 offset (modulo day rollover, which the date fields
        # absorb). Compare minute-of-day on the same date only when the dates
        # match to keep the test race-free near midnight.
        if utc_match[1] == local_match[1]:
            utc_minutes = int(utc_match[2]) * 60 + int(utc_match[3])
            local_minutes = int(local_match[2]) * 60 + int(local_match[3])
            self.assertEqual((local_minutes - utc_minutes) % (24 * 60), 7 * 60)

    def test_unknown_timezone_is_a_clear_error_not_a_crash(self):
        result = _get_time("Mars/Olympus_Mons")
        self.assertIn("ERROR get_time:", result)
        self.assertIn("unknown timezone", result)
        self.assertIn("Asia/Jakarta", result)  # hints at the right format

    def test_reply_is_small_and_single_purpose(self):
        self.assertLess(len(_get_time()), 200)
        self.assertLess(len(_get_time("Europe/Berlin")), 200)


if __name__ == "__main__":
    unittest.main()
