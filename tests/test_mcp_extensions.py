"""Tests for MCP tool filtering, resources/prompts, identity header, Claude import."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from zeline import mcp  # noqa: E402


class _FakeServer(mcp.MCPServer):
    """MCPServer with the transport replaced by a canned RPC responder."""

    def __init__(self, **kwargs):
        super().__init__(name="fake", transport="stdio", **kwargs)
        self.responses: dict[str, dict] = {}

    def _rpc(self, method, params=None, *, notify=False, timeout=12):
        if method == "initialize":
            self._initialized = True
            return {}
        resp = self.responses.get(method)
        if resp is None:
            raise RuntimeError(f"no canned response for {method}")
        return resp


class ToolFilterTest(unittest.TestCase):
    def test_no_filter_shows_everything(self):
        server = _FakeServer()
        self.assertTrue(server.tool_visible("anything"))
        self.assertTrue(server.tool_visible("read_file"))

    def test_deny_wins(self):
        server = _FakeServer(allow=["*"], deny=["delete_*"])
        self.assertFalse(server.tool_visible("delete_repo"))
        self.assertTrue(server.tool_visible("create_issue"))

    def test_allow_is_fnmatch(self):
        server = _FakeServer(allow=["read_*", "list_*"])
        self.assertTrue(server.tool_visible("read_file"))
        self.assertTrue(server.tool_visible("list_tools"))
        self.assertFalse(server.tool_visible("write_file"))

    def test_list_tools_applies_filter(self):
        server = _FakeServer(deny=["dangerous"])
        server.responses["tools/list"] = {"tools": [
            {"name": "safe", "description": "ok"},
            {"name": "dangerous", "description": "nope"},
        ]}
        names = [t["function"]["name"] for t in server.list_tools()]
        self.assertEqual(names, ["mcp__fake__safe"])


class ResourcesPromptsTest(unittest.TestCase):
    def test_list_and_read_resource(self):
        server = _FakeServer()
        server.responses["resources/list"] = {"resources": [
            {"uri": "file:///docs/a.md", "name": "a", "mimeType": "text/markdown"},
            {"bad": True},
        ]}
        resources = server.list_resources()
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["uri"], "file:///docs/a.md")

        server.responses["resources/read"] = {"contents": [
            {"uri": "file:///docs/a.md", "text": "hello resource"},
        ]}
        self.assertEqual(server.read_resource("file:///docs/a.md"), "hello resource")

    def test_binary_resource_is_described_not_dumped(self):
        server = _FakeServer()
        server.responses["resources/read"] = {"contents": [
            {"uri": "file:///img.png", "blob": "AAAA" * 10, "mimeType": "image/png"},
        ]}
        text = server.read_resource("file:///img.png")
        self.assertIn("binary resource", text)
        self.assertIn("image/png", text)

    def test_server_without_capability_returns_empty(self):
        server = _FakeServer()
        # _rpc raises RuntimeError on error payloads; list_resources swallows it.
        def raise_method(method, params=None, *, notify=False, timeout=12):
            if method == "initialize":
                return {}
            raise RuntimeError("method not found")
        server._rpc = raise_method  # type: ignore[method-assign]
        self.assertEqual(server.list_resources(), [])
        self.assertEqual(server.list_prompts(), [])

    def test_get_prompt(self):
        server = _FakeServer()
        server.responses["prompts/get"] = {"messages": [
            {"role": "user", "content": {"type": "text", "text": "Review this code"}},
        ]}
        self.assertEqual(server.get_prompt("review"), "[user] Review this code")


class IdentityHeaderTest(unittest.TestCase):
    def test_identity_sent_on_http(self):
        sent: dict = {}

        class _Resp:
            ok = True
            text = '{"jsonrpc":"2.0","id":1,"result":{}}'

        def fake_post(url, headers=None, json=None, timeout=None):
            sent["headers"] = headers
            return _Resp()

        import zeline.mcp as m
        original = m.requests.post
        m.requests.post = fake_post
        try:
            server = m.MCPServer(name="remote", transport="http",
                                 url="https://example.com/mcp",
                                 identity={"X-User-Id": "user-123"})
            server._initialized = True
            server._http_rpc("tools/list", {})
        finally:
            m.requests.post = original
        self.assertEqual(sent["headers"].get("X-User-Id"), "user-123")


class FromConfigTest(unittest.TestCase):
    def test_parses_new_fields(self):
        registry = mcp.MCPRegistry.from_config({"mcp": {"servers": {
            "gh": {"transport": "http", "url": "https://x/mcp",
                   "allow": ["issue_*"], "deny": ["issue_delete"],
                   "identity_header": {"X-User-Id": "u1"}},
            "off": {"command": "x", "enabled": False},
        }}})
        gh = registry.server("gh")
        assert gh is not None
        self.assertEqual(gh.allow, ["issue_*"])
        self.assertEqual(gh.deny, ["issue_delete"])
        self.assertEqual(gh.identity, {"X-User-Id": "u1"})
        self.assertIsNone(registry.server("off"))


class ClaudeImportTest(unittest.TestCase):
    def test_stdio_with_args(self):
        out = mcp.import_claude_mcp_servers({"mcpServers": {
            "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                   "env": {"HOME": "/x"}},
        }})
        self.assertEqual(out["fs"]["transport"], "stdio")
        self.assertEqual(out["fs"]["command"], "npx -y @modelcontextprotocol/server-filesystem /tmp")
        self.assertEqual(out["fs"]["env"], {"HOME": "/x"})

    def test_http_with_headers(self):
        out = mcp.import_claude_mcp_servers({"mcpServers": {
            "remote": {"url": "https://mcp.example.com", "headers": {"Authorization": "Bearer t"}},
        }})
        self.assertEqual(out["remote"], {"transport": "http", "url": "https://mcp.example.com",
                                         "headers": {"Authorization": "Bearer t"}})

    def test_args_with_spaces_are_quoted(self):
        out = mcp.import_claude_mcp_servers({"mcpServers": {
            "x": {"command": "/usr/local/my server/run", "args": ["--path", "my folder"]},
        }})
        self.assertEqual(out["x"]["command"], "'/usr/local/my server/run' --path 'my folder'")

    def test_invalid_entries_skipped(self):
        out = mcp.import_claude_mcp_servers({"mcpServers": {
            "bad": {"foo": 1},
            "notadict": "hello",
        }})
        self.assertEqual(out, {})

    def test_empty_block(self):
        self.assertEqual(mcp.import_claude_mcp_servers({}), {})


if __name__ == "__main__":
    unittest.main()
