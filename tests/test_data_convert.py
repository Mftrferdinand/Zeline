"""Tests for data_convert (JSON/CSV/YAML/TOML conversion and validation)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)


class DataConvertTests(unittest.TestCase):
    def setUp(self):
        from zeline import data_tools

        self.data_tools = data_tools
        self.workspace = Path(tempfile.mkdtemp())

    def write(self, name: str, content: str) -> str:
        (self.workspace / name).write_text(content, encoding="utf-8")
        return name

    def test_json_to_yaml(self):
        self.write("in.json", '{"name": "zeline", "tags": ["a", "b"], "nested": {"on": true}}')
        out = self.data_tools._data_convert("in.json", self.workspace, to_format="yaml")
        self.assertIn("name: zeline", out)
        self.assertIn("- a", out)
        self.assertIn("on: true", out)

    def test_csv_to_json(self):
        self.write("rows.csv", "id,name\n1,alpha\n2,beta\n")
        out = self.data_tools._data_convert("rows.csv", self.workspace, to_format="json")
        data = json.loads(out)
        self.assertEqual(data, [{"id": "1", "name": "alpha"}, {"id": "2", "name": "beta"}])

    def test_json_to_csv_and_back(self):
        self.write("in.json", '[{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]')
        out = self.data_tools._data_convert("in.json", self.workspace, to_format="csv")
        self.write("back.csv", out)
        roundtrip = self.data_tools._data_convert("back.csv", self.workspace, to_format="json")
        self.assertEqual(json.loads(roundtrip), [{"id": "1", "name": "alpha"}, {"id": "2", "name": "beta"}])

    def test_toml_roundtrip(self):
        self.write("cfg.toml", 'name = "zeline"\n[server]\nhost = "0.0.0.0"\nport = 8080\n')
        out = self.data_tools._data_convert("cfg.toml", self.workspace, to_format="json")
        data = json.loads(out)
        self.assertEqual(data["server"]["port"], 8080)

    def test_validate_ok_and_broken(self):
        self.write("good.json", '{"ok": true}')
        self.assertIn("VALID JSON", self.data_tools._data_convert("good.json", self.workspace, validate_only=True))
        self.write("bad.json", '{"ok": tru}')
        bad = self.data_tools._data_convert("bad.json", self.workspace, validate_only=True)
        self.assertIn("INVALID JSON", bad)
        self.assertIn("line 1", bad)

    def test_yaml_broken_reports_line(self):
        self.write("bad.yaml", "a: 1\n  b: 2\n")
        out = self.data_tools._data_convert("bad.yaml", self.workspace, validate_only=True)
        self.assertIn("INVALID YAML", out)

    def test_output_path_writes_workspace_file(self):
        self.write("in.json", '{"a": 1}')
        out = self.data_tools._data_convert(
            "in.json", self.workspace, to_format="yaml", output_path="out.yaml")
        self.assertIn("OK, converted", out)
        self.assertIn("a: 1", (self.workspace / "out.yaml").read_text())

    def test_path_escape_refused(self):
        self.assertIn("ERROR", self.data_tools._data_convert("../x.json", self.workspace))

    def test_unknown_target_format(self):
        self.write("in.json", "{}")
        self.assertIn("ERROR data_convert",
                      self.data_tools._data_convert("in.json", self.workspace, to_format="xml"))


if __name__ == "__main__":
    unittest.main()
