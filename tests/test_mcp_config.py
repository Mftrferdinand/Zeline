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


class GateTests(unittest.TestCase):
    """Per-server allowed_tools/denied_tools gating."""

    def _registry(self, spec):
        from zeline import mcp

        return mcp.MCPRegistry.from_config({"mcp": {"servers": {"srv": spec}}})

    def _fake_server(self, registry, tools=("alpha", "beta", "gamma")):
        """Register a fake connected server without spawning a process."""
        from zeline import mcp as mcp_module

        server = mcp_module.MCPServer(name="srv", transport="stdio", command="")
        server.list_tools = lambda: [{"name": name, "description": "", "inputSchema": {}} for name in tools]
        registry.servers["srv"] = server
        registry._tool_cache = None
        return server

    def test_allowlist_hides_other_tools(self):
        registry = self._registry({"command": "x", "allowed_tools": ["alpha"]})
        self._fake_server(registry)
        names = {schema["name"] for schema in registry.schemas()}
        self.assertEqual(names, {"alpha"})

    def test_denylist_wins_over_allowlist(self):
        registry = self._registry({"command": "x", "allowed_tools": ["alpha", "beta"], "denied_tools": ["beta"]})
        self._fake_server(registry)
        names = {schema["name"] for schema in registry.schemas()}
        self.assertEqual(names, {"alpha"})

    def test_call_refused_for_denied(self):
        registry = self._registry({"command": "x", "denied_tools": ["beta"]})
        self._fake_server(registry)
        out = registry.call("mcp__srv__beta", {})
        self.assertIn("denied by denied_tools", out)

    def test_call_refused_for_not_allowed(self):
        registry = self._registry({"command": "x", "allowed_tools": ["alpha"]})
        self._fake_server(registry)
        out = registry.call("mcp__srv__gamma", {})
        self.assertIn("not in allowed_tools", out)

    def test_call_allowed_reaches_server(self):
        registry = self._registry({"command": "x", "allowed_tools": ["alpha"]})
        self._fake_server(registry)
        calls = []
        server = registry.servers["srv"]
        server.initialize = lambda: None
        server.call_tool = lambda tool, args: f"ran {tool}"
        self.assertEqual(registry.call("mcp__srv__alpha", {}), "ran alpha")

    def test_invalid_gate_config_is_an_error(self):
        with self.assertRaises(ValueError):
            self._registry({"command": "x", "allowed_tools": "alpha"})


if __name__ == "__main__":
    unittest.main()
