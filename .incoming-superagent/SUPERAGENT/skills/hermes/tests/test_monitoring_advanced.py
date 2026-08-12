"""Tests for monitoring_advanced.py: MempoolFilter matching + danger selectors."""
import _bootstrap  # noqa: F401

import unittest

import monitoring_advanced as MA


class TestMempoolFilter(unittest.TestCase):
    def test_matches_by_to_address_case_insensitive(self):
        f = MA.MempoolFilter(to="0xAbC")
        self.assertTrue(f.matches({"to": "0xabc"}))
        self.assertFalse(f.matches({"to": "0xdef"}))

    def test_matches_by_selector(self):
        f = MA.MempoolFilter(selector="0xa9059cbb")
        self.assertTrue(f.matches({"input": "0xA9059CBB0000"}))
        self.assertFalse(f.matches({"input": "0x12345678"}))

    def test_min_value_decimal_and_hex(self):
        f = MA.MempoolFilter(min_value_wei=1000)
        self.assertTrue(f.matches({"value": 2000}))
        self.assertTrue(f.matches({"value": hex(2000)}))
        self.assertFalse(f.matches({"value": 10}))
        self.assertFalse(f.matches({"value": "0x5"}))

    def test_custom_predicate(self):
        f = MA.MempoolFilter(custom=lambda tx: tx.get("flag") is True)
        self.assertTrue(f.matches({"flag": True}))
        self.assertFalse(f.matches({"flag": False}))

    def test_combined_filters_all_must_pass(self):
        f = MA.MempoolFilter(to="0x1", selector="0xa9059cbb")
        self.assertTrue(f.matches({"to": "0x1", "input": "0xa9059cbbdead"}))
        self.assertFalse(f.matches({"to": "0x1", "input": "0xdeadbeef"}))


class TestDangerSelectors(unittest.TestCase):
    def test_approval_selectors_present(self):
        self.assertIn("0x095ea7b3", MA.DANGER_SELECTORS)        # approve
        self.assertIn("0xa22cb465", MA.DANGER_SELECTORS)        # setApprovalForAll
        self.assertIn("0xd505accf", MA.DANGER_SELECTORS)        # permit


if __name__ == "__main__":
    unittest.main()
