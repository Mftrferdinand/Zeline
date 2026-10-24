import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import json_repair as J  # noqa: E402


class ValidJsonTest(unittest.TestCase):
    def test_valid_passthrough(self):
        self.assertEqual(J.loads('{"a": 1}'), {"a": 1})
        self.assertEqual(J.loads("[1, 2, 3]"), [1, 2, 3])


class ExtractTest(unittest.TestCase):
    def test_fenced_block(self):
        text = 'Here you go:\n```json\n{"a": 1}\n```\nDone!'
        self.assertEqual(J.loads(text), {"a": 1})

    def test_fenced_without_language(self):
        text = '```\n{"a": 1}\n```'
        self.assertEqual(J.loads(text), {"a": 1})

    def test_prose_wrapped(self):
        text = 'The result is {"a": {"b": [1, "x] y"]}} as requested.'
        self.assertEqual(J.loads(text), {"a": {"b": [1, "x] y"]}})

    def test_leading_array(self):
        self.assertEqual(J.loads("Output: [1, 2]"), [1, 2])


class RepairTest(unittest.TestCase):
    def test_single_quotes(self):
        self.assertEqual(J.loads("{'a': 'b'}"), {"a": "b"})

    def test_trailing_comma(self):
        self.assertEqual(J.loads('{"a": 1,}'), {"a": 1})
        self.assertEqual(J.loads('[1, 2,]'), [1, 2])

    def test_bare_keys(self):
        self.assertEqual(J.loads('{a: 1, b_two: "x"}'), {"a": 1, "b_two": "x"})

    def test_comments(self):
        self.assertEqual(J.loads('{\n  // hi\n  "a": 1 /* yo */\n}'), {"a": 1})

    def test_combined_mess(self):
        messy = "```json\n{'a': 1, 'b': [2, 3,],} // trailing\n```"
        self.assertEqual(J.loads(messy), {"a": 1, "b": [2, 3]})

    def test_truncated_output(self):
        self.assertEqual(J.loads('{"a": 1, "b": [2, "x'), {"a": 1, "b": [2, "x"]})

    def test_unrepairable_raises(self):
        with self.assertRaises(ValueError):
            J.loads("this is not json at all %%")

    def test_repair_detailed_records_repairs(self):
        result = J.repair_detailed("{'a': 1,}")
        self.assertTrue(result["ok"])
        self.assertIn("single_quotes", result["repairs"])


class RepairReportTest(unittest.TestCase):
    def test_ok_report(self):
        result = J.repair('{"a": 1}')
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], {"a": 1})

    def test_fail_report(self):
        result = J.repair("nope")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
