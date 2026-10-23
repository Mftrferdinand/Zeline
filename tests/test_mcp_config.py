"""Tests for MCP config ${VAR} expansion."""
from __future__ import annotations

import os
import sys
import unittest

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

ENV = {"GITHUB_TOKEN": "tok-123", "BASE_URL": "https://mcp.example.com"}


class ExpandTests(unittest.TestCase):
    def setUp(self):
        from zeline import mcp_config

        self.mcp_config = mcp_config

    def test_expands_from_environment(self):
        self.assertEqual(
            self.mcp_config.expand("Bearer ${GITHUB_TOKEN}", ENV),
            "Bearer tok-123")

    def test_missing_variable_is_a_named_error(self):
        with self.assertRaises(self.mcp_config.ConfigReferenceError) as ctx:
            self.mcp_config.expand("Bearer ${MISSING_TOKEN}", ENV)
        self.assertIn("MISSING_TOKEN", str(ctx.exception))

    def test_empty_variable_is_an_error(self):
        with self.assertRaises(self.mcp_config.ConfigReferenceError):
            self.mcp_config.expand("${EMPTY}", {"EMPTY": ""})

    def test_no_references_untouched(self):
        self.assertEqual(self.mcp_config.expand("plain value", ENV), "plain value")

    def test_expand_spec_fields(self):
        spec = {
            "url": "${BASE_URL}/mcp",
            "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
            "env": {"API_KEY": "${GITHUB_TOKEN}"},
            "enabled": True,
        }
        out = self.mcp_config.expand_spec(spec, ENV)
        self.assertEqual(out["url"], "https://mcp.example.com/mcp")
        self.assertEqual(out["headers"]["Authorization"], "Bearer tok-123")
        self.assertEqual(out["env"]["API_KEY"], "tok-123")
        self.assertEqual(out["enabled"], True)
        # Original spec untouched.
        self.assertEqual(spec["url"], "${BASE_URL}/mcp")

    def test_registry_from_config_expands(self):
        from zeline import mcp

        saved = os.environ.pop("BASE_URL", None)
        try:
            os.environ["BASE_URL"] = ENV["BASE_URL"]
            registry = mcp.MCPRegistry.from_config({
                "mcp": {"servers": {
                    "remote": {"url": "${BASE_URL}/mcp", "transport": "http", "enabled": True},
                }}})
            self.assertEqual(registry.servers["remote"].url, "https://mcp.example.com/mcp")
        finally:
            if saved is None:
                os.environ.pop("BASE_URL", None)
            else:
                os.environ["BASE_URL"] = saved

    def test_registry_names_the_server_and_variable_on_error(self):
        from zeline import mcp

        with self.assertRaises(ValueError) as ctx:
            mcp.MCPRegistry.from_config({
                "mcp": {"servers": {
                    "remote": {"url": "${NOPE_VAR}", "transport": "http", "enabled": True},
                }}})
        self.assertIn("remote", str(ctx.exception))
        self.assertIn("NOPE_VAR", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
