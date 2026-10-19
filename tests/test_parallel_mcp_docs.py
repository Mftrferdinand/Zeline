"""Exercise the Parallel guide's actual config through Zeline's HTTP MCP client."""
import json
from json import dumps
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import requests

from zeline.mcp import MCPRegistry, PROTOCOL_VERSION


ROOT = Path(__file__).resolve().parents[1]


class ParallelMCPDocsTests(unittest.TestCase):
    def test_documented_config_discovers_and_calls_tools_offline(self):
        _exercise_parallel_documented_config()


def _exercise_parallel_documented_config():
    guide = (ROOT / "docs" / "extending.md").read_text(encoding="utf-8")
    section = guide.split("### Parallel web search and page fetching", 1)[1].split("\n---", 1)[0]
    example = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert example is not None, "Parallel setup must include a JSON config example"
    registry = MCPRegistry.from_config(json.loads(example.group(1)))
    search_schema = {
        "type": "object",
        "properties": {
            "objective": {"type": "string"},
            "search_queries": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["objective", "search_queries"],
    }
    fetch_schema = {
        "type": "object",
        "properties": {"urls": {"type": "array", "items": {"type": "string"}}},
        "required": ["urls"],
    }
    methods = []

    def parallel_http(url, *, headers, json: dict, timeout):
        # Only the HTTP boundary is replaced: discovery, JSON-RPC, SSE parsing,
        # namespacing, dispatch and text conversion all run the real client.
        assert url == "https://search.parallel.ai/mcp"
        assert headers == {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Zeline (Parallel Search MCP)",
        }
        assert timeout > 0
        assert json["jsonrpc"] == "2.0"
        method = json["method"]
        methods.append(method)
        if method == "initialize":
            assert json["params"]["protocolVersion"] == PROTOCOL_VERSION
            assert json["params"]["clientInfo"]["name"] == "zeline"
            result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}}
        elif method == "notifications/initialized":
            assert "id" not in json
            response = requests.Response()
            response.status_code = 202
            return response
        elif method == "tools/list":
            result = {"tools": [
                {"name": "web_search", "inputSchema": search_schema},
                {"name": "web_fetch", "inputSchema": fetch_schema},
            ]}
        else:
            assert method == "tools/call"
            params = json["params"]
            if params["name"] == "web_search":
                assert params["arguments"] == {
                    "objective": "Find the official Python website",
                    "search_queries": ["site:python.org Python official website"],
                }
                text = "Python official website: https://www.python.org/"
            else:
                assert params == {"name": "web_fetch", "arguments": {"urls": ["https://www.python.org/"]}}
                text = "Welcome to Python.org"
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        response = requests.Response()
        response.status_code = 200
        # Parallel's streamable HTTP returns SSE, not just plain JSON.
        payload = {"jsonrpc": "2.0", "id": json["id"], "result": result}
        response._content = ("event: message\ndata: " + dumps(payload) + "\n\n").encode()
        return response

    try:
        with patch("zeline.mcp.requests.post", side_effect=parallel_http):
            schemas = {s["function"]["name"]: s["function"]["parameters"] for s in registry.schemas()}
            assert registry.errors == {}
            assert schemas == {
                "mcp__parallel__web_search": search_schema,
                "mcp__parallel__web_fetch": fetch_schema,
            }
            assert registry.call("mcp__parallel__web_search", {
                "objective": "Find the official Python website",
                "search_queries": ["site:python.org Python official website"],
            }) == "Python official website: https://www.python.org/"
            assert registry.call("mcp__parallel__web_fetch", {
                "urls": ["https://www.python.org/"],
            }) == "Welcome to Python.org"
            assert registry.schemas()  # Cached discovery must not repeat the handshake.
        assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call", "tools/call"]
    finally:
        registry.close()
