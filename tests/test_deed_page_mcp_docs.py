"""Exercise the deed.page guide's config through Zeline's HTTP MCP client."""
import json
from json import dumps
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import requests

from zeline.mcp import MCPRegistry, PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[1]
SECTION = "### Publishing pages with deed.page"


def _documented_config() -> dict:
    guide = (ROOT / "docs" / "extending.md").read_text(encoding="utf-8")
    section = guide.split(SECTION, 1)[1].split("\n---", 1)[0].split("\n### ", 1)[0]
    example = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert example is not None, "deed.page setup must include a JSON config example"
    return json.loads(example.group(1))


class DeedPageMCPDocsTests(unittest.TestCase):
    def test_documented_config_is_a_single_http_server(self):
        servers = _documented_config()["mcp"]["servers"]
        self.assertEqual(set(servers), {"deedpage"})
        self.assertEqual(servers["deedpage"]["transport"], "http")
        self.assertEqual(servers["deedpage"]["url"], "https://deed.page/mcp")

    def test_documented_config_publishes_and_deletes_offline(self):
        config = _documented_config()
        # The guide's optional owner header, added the way a reader would.
        config["mcp"]["servers"]["deedpage"]["headers"]["Authorization"] = "Bearer test-owner-token"
        registry = MCPRegistry.from_config(config)
        publish_schema = {
            "type": "object",
            "properties": {
                "files": {"type": "object"},
                "slug": {"type": "string"},
                "ttl_seconds": {"type": "integer"},
            },
            "required": ["files"],
        }
        delete_schema = {
            "type": "object",
            "properties": {"slug": {"type": "string"}},
            "required": ["slug"],
        }
        methods = []

        def deed_page_http(url, *, headers, json: dict, timeout):
            # Only the HTTP boundary is replaced; JSON-RPC, SSE parsing,
            # namespacing and dispatch run the real client.
            self.assertEqual(url, "https://deed.page/mcp")
            self.assertEqual(headers["User-Agent"], "Zeline (deed.page MCP)")
            self.assertEqual(headers["Authorization"], "Bearer test-owner-token")
            self.assertGreater(timeout, 0)
            method = json["method"]
            methods.append(method)
            response = requests.Response()
            if method == "notifications/initialized":
                response.status_code = 202
                return response
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}}
            elif method == "tools/list":
                result = {"tools": [
                    {"name": "publish_site", "inputSchema": publish_schema},
                    {"name": "delete_site", "inputSchema": delete_schema},
                ]}
            else:
                params = json["params"]
                if params["name"] == "publish_site":
                    self.assertEqual(params["arguments"], {
                        "files": {"index.html": "<h1>Report</h1>"},
                        "ttl_seconds": 3600,
                    })
                    text = dumps({"ok": True, "slug": "calm-report-1a2b",
                                  "url": "https://calm-report-1a2b.deed.page/"})
                else:
                    self.assertEqual(params, {"name": "delete_site",
                                              "arguments": {"slug": "calm-report-1a2b"}})
                    text = dumps({"ok": True, "deleted": "calm-report-1a2b"})
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            response.status_code = 200
            payload = {"jsonrpc": "2.0", "id": json["id"], "result": result}
            response._content = ("event: message\ndata: " + dumps(payload) + "\n\n").encode()
            return response

        try:
            with patch("zeline.mcp.requests.post", side_effect=deed_page_http):
                names = {s["function"]["name"] for s in registry.schemas()}
                self.assertEqual(registry.errors, {})
                self.assertEqual(names, {"mcp__deedpage__publish_site", "mcp__deedpage__delete_site"})
                published = json.loads(registry.call("mcp__deedpage__publish_site", {
                    "files": {"index.html": "<h1>Report</h1>"},
                    "ttl_seconds": 3600,
                }))
                self.assertEqual(published["url"], "https://calm-report-1a2b.deed.page/")
                deleted = json.loads(registry.call("mcp__deedpage__delete_site", {"slug": "calm-report-1a2b"}))
                self.assertEqual(deleted["deleted"], "calm-report-1a2b")
            self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/list",
                                       "tools/call", "tools/call"])
        finally:
            registry.close()


if __name__ == "__main__":
    unittest.main()
