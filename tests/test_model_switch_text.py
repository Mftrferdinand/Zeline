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

    def test_switch_text_has_box_and_route_label(self):
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
        self.assertIn("Model Switched", text)
        self.assertIn("9Router › GoRouter", text)
        self.assertIn("Gr/claude-opus-4-8", text)
        self.assertIn("1M tokens", text)
        self.assertIn("128K tokens", text)
        self.assertIn("Saved to config.yaml", text)
        # No Indonesian leftover
        self.assertNotIn("Konteks", text)
        self.assertNotIn("dijaga", text)
        # No capability flags line (removed per owner request)
        self.assertNotIn("vision", text)
        self.assertNotIn("Supports", text)

    def test_switch_text_degrades_without_capabilities(self):
        with mock.patch.object(telegram, "_fetch_model_capabilities", return_value={}):
            text = telegram._model_switch_text("some/model", {"name": "P", "slug": "p"})
        self.assertIn("Model Switched", text)
        self.assertNotIn("tokens", text)
        self.assertIn("Saved to config.yaml", text)


if __name__ == "__main__":
    unittest.main()
