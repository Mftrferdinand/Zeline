"""Tests for text_stats, time_convert, and regex_test."""
from __future__ import annotations

import json
import os
import sys
import unittest

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)


class TextStatsTests(unittest.TestCase):
    def setUp(self):
        from zeline import text_tools

        self.text_tools = text_tools

    def test_counts(self):
        result = json.loads(self.text_tools._text_stats("Hello world\nhello again\n"))
        self.assertEqual(result["words"], 4)
        self.assertEqual(result["lines"], 2)
        self.assertGreater(result["characters"], 0)
        self.assertGreater(result["estimated_tokens"], 0)
    def test_top_terms_exclude_stopwords(self):
        result = json.loads(self.text_tools._text_stats("agent agent agent the the tool"))
        terms = [item["term"] for item in result["top_terms"]]
        self.assertEqual(terms[0], "agent")
        self.assertNotIn("the", terms)

    def test_oversized_text_refused(self):
        self.assertIn("limit", self.text_tools._text_stats("x" * 600_000))


class TimeConvertTests(unittest.TestCase):
    def setUp(self):
        from zeline import text_tools

        self.text_tools = text_tools

    def test_known_timestamp(self):
        out = self.text_tools._time_convert(timestamp=0, timezones="UTC", text_time="2026-10-23 09:30")
        self.assertIn("UTC: 2026-10-23 09:30:00", out)
        self.assertIn("Unix: 1792747800", out)

    def test_unknown_timezone_reported(self):
        out = self.text_tools._time_convert(timestamp=0, timezones="Mars/Olympus", text_time="2026-10-23 09:30")
        self.assertIn("unknown timezone", out)

    def test_bad_text_time(self):
        out = self.text_tools._time_convert(timestamp=0, timezones="UTC", text_time="tomorrow")
        self.assertIn("ERROR time_convert", out)


class RegexTestTests(unittest.TestCase):
    def setUp(self):
        from zeline import text_tools

        self.text_tools = text_tools

    def test_matches_with_positions(self):
        out = self.text_tools._regex_test(r"\d+", "a1 b22 c333")
        self.assertIn("3 match(es)", out)
        self.assertIn("chars", out)

    def test_named_groups(self):
        out = self.text_tools._regex_test(r"(?P<user>\w+)@", "mail: bob@x")
        self.assertIn("user", out)
        self.assertIn("bob", out)

    def test_no_matches(self):
        self.assertIn("NO MATCHES", self.text_tools._regex_test(r"\d+", "abc"))

    def test_invalid_pattern(self):
        self.assertIn("invalid pattern", self.text_tools._regex_test(r"([", "abc"))

    def test_match_cap(self):
        out = self.text_tools._regex_test(r"x", "x" * 200)
        self.assertIn("omitted", out)


if __name__ == "__main__":
    unittest.main()
