"""Tests for the hash_check tool."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)


class HashCheckTests(unittest.TestCase):
    def setUp(self):
        from zeline import hash_tools

        self.hash_tools = hash_tools
        self.workspace = Path(tempfile.mkdtemp())
        self.file = self.workspace / "artifact.bin"
        self.file.write_bytes(b"zeline hash check payload")
        self.sha256 = hashlib.sha256(b"zeline hash check payload").hexdigest()

    def test_digest_output(self):
        result = self.hash_tools._hash_check("artifact.bin", self.workspace)
        self.assertIn(self.sha256, result)
        self.assertTrue(result.startswith("sha256:"))

    def test_match(self):
        result = self.hash_tools._hash_check(
            "artifact.bin", self.workspace, expected=self.sha256.upper())
        self.assertIn("MATCH", result)

    def test_mismatch(self):
        result = self.hash_tools._hash_check(
            "artifact.bin", self.workspace, expected="0" * 64)
        self.assertIn("MISMATCH", result)
        self.assertIn(self.sha256, result)

    def test_invalid_expected_digest(self):
        result = self.hash_tools._hash_check("artifact.bin", self.workspace, expected="zz")
        self.assertIn("ERROR hash_check", result)

    def test_unknown_algorithm(self):
        result = self.hash_tools._hash_check("artifact.bin", self.workspace, algorithm="crc32")
        self.assertIn("ERROR hash_check", result)

    def test_path_escape_refused(self):
        result = self.hash_tools._hash_check("../outside.txt", self.workspace)
        self.assertIn("ERROR hash_check", result)

    def test_missing_file(self):
        result = self.hash_tools._hash_check("nope.bin", self.workspace)
        self.assertIn("ERROR hash_check", result)

    def test_registered_in_safe_profiles(self):
        from zeline import tools

        executor = tools.ToolExecutor("test:hash", "safe")
        schema_names = {schema.get("function", {}).get("name") for schema in executor.all_schemas}
        self.assertIn("hash_check", schema_names)


if __name__ == "__main__":
    unittest.main()
