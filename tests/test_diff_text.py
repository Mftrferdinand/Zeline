"""Tests for diff_text (unified diff of files/inline texts)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)


class DiffTextTests(unittest.TestCase):
    def setUp(self):
        from zeline import diff_tools

        self.diff_tools = diff_tools
        self.workspace = Path(tempfile.mkdtemp())
        (self.workspace / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
        (self.workspace / "b.txt").write_text("one\ntwo\nthree!\n", encoding="utf-8")

    def test_file_diff_shows_change(self):
        out = self.diff_tools._diff_text(path_a="a.txt", path_b="b.txt", workspace=self.workspace)
        self.assertIn("--- a.txt", out)
        self.assertIn("+++ b.txt", out)
        self.assertIn("-three", out)
        self.assertIn("+three!", out)

    def test_identical_files(self):
        out = self.diff_tools._diff_text(path_a="a.txt", path_b="a.txt", workspace=self.workspace)
        self.assertIn("IDENTICAL", out)

    def test_inline_texts(self):
        out = self.diff_tools._diff_text(text_a="x\ny\n", text_b="x\nz\n", workspace=self.workspace)
        self.assertIn("-y", out)
        self.assertIn("+z", out)

    def test_missing_file(self):
        out = self.diff_tools._diff_text(path_a="nope.txt", text_b="hi", workspace=self.workspace)
        self.assertIn("ERROR diff_text", out)

    def test_path_escape_refused(self):
        out = self.diff_tools._diff_text(path_a="../secret.txt", text_b="hi", workspace=self.workspace)
        self.assertIn("ERROR diff_text", out)


if __name__ == "__main__":
    unittest.main()
