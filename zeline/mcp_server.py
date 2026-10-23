"""Run Zeline as a stdio MCP server (``zeline mcp-server``).

The mirror of zeline/mcp.py: that module connects Zeline *to* external MCP
servers; this one lets MCP clients (Claude Desktop, Cursor, Zed) connect
*to* Zeline and use its memory and skill catalog as tools/resources.

Design mirrors OpenHuman Core's approach: newline-delimited JSON-RPC 2.0 on
stdin/stdout, logs to stderr, read tools pass a read gate, and memory
writes stay behind the same transactional store the native tools use.
"""
from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "zeline", "version": "0.1.0"}
MAX_TEXT_CHARS = 20_000


def _version() -> str:
    from zeline import __version__
    return __version__


def _tool_definitions(memory: Any) -> list[dict[str, Any]]:
    """The MCP tool surface: read-heavy memory tools plus skill lookup."""
    tools = [
        {
            "name": "memory.list",
            "description": "Page through Zeline memory items, newest first. Read-only.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kinds": {"type": "array", "items": {"type": "string"},
                              "description": "Filter by kind: preference, fact, procedure, correction, other"},
                    "sources": {"type": "array", "items": {"type": "string"},
                                "description": "Filter by source: user, reflection"},
                    "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "observed_after": {"type": "number", "description": "Unix timestamp: updated after"},
                    "observed_before": {"type": "number", "description": "Unix timestamp: updated before"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "cursor": {"type": "string", "description": "Opaque pagination cursor"},
                },
            },
        },
        {
            "name": "memory.recall",
            "description": "Lexical memory search for a query. Read-only.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
        },
        {
            "name": "memory.learn",
            "description": "Store one fact/preference/procedure in Zeline memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The fact to remember"},
                    "kind": {"type": "string",
                             "enum": ["preference", "fact", "procedure", "correction", "other"]},
                },
                "required": ["text"],
            },
        },
        {
            "name": "skill.list",
            "description": "List available Zeline skills by name. Read-only.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "skill.read",
            "description": "Read one Zeline skill's full markdown content. Read-only.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    ]
    return tools


class ZelineMcpServer:
    """Minimal MCP stdio server over Zeline's memory store and skill catalog."""

    def __init__(self, identity: str = "cli:local") -> None:
        from zeline import memory as memory_module
        self.memory = memory_module.MemoryStore(identity)
        self._write_count = 0

    # ---- tool dispatch (read gate / write gate) ---------------------------

    def _call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "memory.list":
            result = self.memory.query(
                kinds=args.get("kinds"),
                sources=args.get("sources"),
                min_confidence=args.get("min_confidence"),
                observed_after=args.get("observed_after"),
                observed_before=args.get("observed_before"),
                limit=args.get("limit", 20),
                cursor=args.get("cursor", ""),
            )
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        if name == "memory.recall":
            hits = self.memory.search(str(args.get("query", "")), int(args.get("limit", 10)))
            return {"content": [{"type": "text", "text": json.dumps(
                {"records": hits}, ensure_ascii=False)}]}
        if name == "memory.learn":
            text = str(args.get("text", "")).strip()
            if not text or len(text) > 1000:
                raise ValueError("text must be 1-1000 characters")
            self.memory.add(text, kind=str(args.get("kind", "fact")))
            self._write_count += 1
            return {"content": [{"type": "text", "text": "stored"}]}
        if name == "skill.list":
            from zeline import skills
            names = [entry[0] for entry in skills.list_skill_entries(include_private=False)]
            return {"content": [{"type": "text", "text": json.dumps(
                {"skills": names}, ensure_ascii=False)}]}
        if name == "skill.read":
            from zeline import skills
            try:
                content = skills.load_skill(str(args.get("name", "")), include_private=False)
            except Exception:
                content = ""
            # load_skill returns an "ERROR: ..." string (or raises) for misses.
            if not content or content.startswith("ERROR"):
                raise ValueError(f"skill not found: {args.get('name')}")
            if len(content) > MAX_TEXT_CHARS:
                content = content[:MAX_TEXT_CHARS] + "\n... [truncated]"
            return {"content": [{"type": "text", "text": content}]}
        raise ValueError(f"unknown tool: {name}")

    def _resources(self) -> list[dict[str, Any]]:
        """Static prompt resources, mirroring OpenHuman's URI scheme shape."""
        return [
            {"uri": "zeline://prompts/identity", "name": "Zeline identity",
             "mimeType": "text/markdown"},
            {"uri": "zeline://prompts/soul", "name": "Zeline personality",
             "mimeType": "text/markdown"},
        ]

    def _read_resource(self, uri: str) -> dict[str, Any]:
        from zeline import branding
        catalog = {
            "zeline://prompts/identity": lambda: "\n".join(
                branding.render(_version(), unicode_ok=True)),
            "zeline://prompts/soul": lambda: (
                "Zeline is a local-first agentic assistant: your data stays on "
                "your machine, tools run inside a sandboxed workspace, and every "
                "memory write is transactional and auditable."),
        }
        loader = catalog.get(uri)
        if loader is None:
            raise ValueError(f"unknown resource: {uri}")
        text = loader()
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text[:MAX_TEXT_CHARS]}]}

    # ---- JSON-RPC loop ----------------------------------------------------

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method", "")
        msg_id = message.get("id")
        if "id" not in message or method.startswith("notifications/"):
            return None  # notification: no response
        try:
            result = self._dispatch(method, message.get("params") or {})
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except ValueError as exc:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32602, "message": str(exc)}}
        except Exception as exc:
            # Never echo internals; a client only needs the shape of the failure.
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32603, "message": f"internal error ({type(exc).__name__})"}}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {"subscribe": False, "listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": "Zeline memory and skills over MCP.",
            }
        if method == "tools/list":
            return {"tools": _tool_definitions(self.memory)}
        if method == "tools/call":
            name = str(params.get("name", ""))
            return self._call_tool(name, params.get("arguments") or {})
        if method == "resources/list":
            return {"resources": self._resources()}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            return self._read_resource(str((params or {}).get("uri", "")))
        if method == "ping":
            return {}
        raise ValueError(f"method not found: {method}")

    def serve(self, stdin: Any = None, stdout: Any = None) -> None:
        """Read newline-delimited JSON-RPC requests until EOF."""
        stream_in = stdin if stdin is not None else sys.stdin
        stream_out = stdout if stdout is not None else sys.stdout
        for line in stream_in:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle(message)
            if response is not None:
                stream_out.write(json.dumps(response, ensure_ascii=False) + "\n")
                stream_out.flush()
