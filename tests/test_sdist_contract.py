"""Source releases must retain the documented offline bootstrap surfaces."""
from pathlib import Path
import unittest


class SourceDistributionContractTests(unittest.TestCase):
    def test_manifest_keeps_installers_and_release_documentation(self):
        root = Path(__file__).resolve().parents[1]
        rules = {line.strip() for line in (root / "MANIFEST.in").read_text("utf-8").splitlines()}
        for rule in (
            "include install.sh install.ps1 CHANGELOG.md",
            "include .github/RELEASE_NOTES.md",
            "recursive-include docs *.md",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, rules)
