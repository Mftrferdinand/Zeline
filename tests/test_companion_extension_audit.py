"""Regression coverage for complete companion filename extraction."""
import unittest

from test_bundled_skill_references import COMPANION


class CompanionExtensionAuditTests(unittest.TestCase):
    def test_companion_extensions_are_not_truncated(self):
        for reference in (
            "templates/Widget.jsx", "scripts/helper.tsx", "scripts/helper.ts",
            "templates/config.json", "scripts/main.js",
        ):
            with self.subTest(reference=reference):
                self.assertEqual(COMPANION.findall(reference), [reference])

    def test_unsupported_extension_is_not_partially_matched(self):
        self.assertEqual(COMPANION.findall("templates/Widget.jsbackup"), [])
