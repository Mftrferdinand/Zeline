import unittest
from unittest import mock

from zeline.gateways import telegram


class ModelSwitchTextTests(unittest.TestCase):
    def test_format_token_count(self):
        self.assertEqual(telegram._format_token_count(1_000_000), "1M")
        self.assertEqual(telegram._format_token_count(128_000), "128K")
        self.assertEqual(telegram._format_token_count(200_000), "200K")
        self.assertIsNone(telegram._format_token_count(None))
        self.assertIsNone(telegram._format_token_count(0))
        self.assertEqual(telegram._format_token_count(512), "512")

    def test_switch_text_is_english_with_capabilities(self):
        caps = {
            "capabilities": {
                "contextWindow": 1_000_000,
                "maxOutput": 128_000,
                "vision": True,
                "tools": True,
                "reasoning": True,
                "search": True,
            }
        }
        with mock.patch.object(telegram, "_fetch_model_capabilities", return_value=caps):
            text = telegram._model_switch_text("Gr/claude-opus-4-8", {"name": "9Router", "slug": "9router"})
        # No Indonesian leftover
        self.assertNotIn("Konteks", text)
        self.assertNotIn("dijaga", text)
        # Has the detail fields
        self.assertIn("Model switched", text)
        self.assertIn("1M tokens", text)
        self.assertIn("128K tokens", text)
        self.assertIn("vision", text)
        self.assertIn("context preserved", text)

    def test_switch_text_degrades_without_capabilities(self):
        with mock.patch.object(telegram, "_fetch_model_capabilities", return_value={}):
            text = telegram._model_switch_text("some/model", {"name": "P", "slug": "p"})
        # Still valid, still English, just no token lines
        self.assertIn("Model switched", text)
        self.assertNotIn("tokens", text)
        self.assertIn("context preserved", text)


if __name__ == "__main__":
    unittest.main()
