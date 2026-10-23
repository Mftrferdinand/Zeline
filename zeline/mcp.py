"""MCP (Model Context Protocol) client untuk Zeline.

Menyambungkan Zeline ke MCP server eksternal (stdio atau HTTP streamable),
menemukan tool mereka lewat JSON-RPC, lalu mengekspos tool-tool itu sebagai
tool Zeline biasa. Sekali sebuah server terdaftar, semua tool-nya bisa dipanggil
model tanpa harus ditulis satu per satu.

Desain:
- Transport ``stdio``: spawn perintah server, bicara JSON-RPC via stdin/stdout
  dengan framing baris (satu objek JSON per baris — newline-delimited).
- Transport ``http``: POST JSON-RPC ke satu endpoint; balasan bisa JSON biasa
  atau SSE (``data: {...}``) — dua-duanya diparse.
- Tool MCP diberi prefix ``mcp__<server>__<tool>`` supaya tidak bentrok dengan
  tool asli Zeline dan gateway bisa tahu asalnya.

Keamanan: server stdio menjalankan perintah lokal, jadi hanya boleh diaktifkan
oleh operator lewat config (bukan oleh orang yang chat bot). Registrasi server
disimpan di ``~/.zeline/config.json`` bagian ``mcp.servers``.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from zeline import __version__

MCP_TOOL_PREFIX = "mcp__"
_RPC_TIMEOUT = 12
_INIT_TIMEOUT = 5
# Total wall-clock budget for listing tools across ALL MCP servers.
_DISCOVERY_DEADLINE = 6.0
PROTOCOL_VERSION = "2024-11-05"


def make_tool_name(server: str, tool: str) -> str:
    """Nama tool Zeline untuk sebuah tool MCP: mcp__<server>__<tool>."""
    return f"{MCP_TOOL_PREFIX}{server}__{tool}"


def split_command(command: str) -> list[str]:
    """Pecah string command jadi argv, aman untuk path Windows.

    ``shlex.split`` default POSIX: backslash dianggap escape char, sehingga
    ``C:\\Python\\python.exe`` jadi ``C:Pythonpython.exe`` dan spawn gagal
    dengan ``FileNotFoundError [WinError 2]``. Di Windows dipakai
    ``posix=False`` (backslash literal), lalu tanda kutip sisa dibersihkan
    karena mode non-POSIX mempertahankannya.
    """
    if os.name != "nt":
        return shlex.split(command)
    parts = shlex.split(command, posix=False)
    return [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part for part in parts]


def parse_tool_name(name: str) -> tuple[str, str] | None:
    """Kebalikan make_tool_name; None kalau bukan tool MCP."""
    if not name.startswith(MCP_TOOL_PREFIX):
        return None
    rest = name[len(MCP_TOOL_PREFIX):]
    server, _, tool = rest.partition("__")
    if not server or not tool:
        return None
    return server, tool


def _decode_jsonrpc_payload(text: str) -> dict[str, Any] | None:
    """Ambil objek JSON-RPC dari body HTTP: JSON biasa atau SSE (data: ...)."""
    text = text.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # SSE: ambil baris data: terakhir yang berisi objek dengan "result"/"error".
    last: dict[str, Any] | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                try:
                    obj = json.loads(chunk)
                    if isinstance(obj, dict):
                        last = obj
                except json.JSONDecodeError:
                    continue
    return last


def _content_to_text(result: dict[str, Any]) -> str:
    """Ubah hasil tools/call MCP jadi teks yang bisa dibaca model."""
    if not isinstance(result, dict):
        return str(result)
    content = result.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(str(block.get("text", "")))
            elif btype == "resource":
                res = block.get("resource", {})
                parts.append(str(res.get("text") or res.get("uri") or json.dumps(res, ensure_ascii=False)))
            else:
                parts.append(json.dumps(block, ensure_ascii=False))
        text = "\n".join(p for p in parts if p).strip()
    else:
        text = json.dumps(result, ensure_ascii=False)
    if result.get("isError"):
        return f"[MCP tool error] {text}"
    return text or "(tool MCP tidak mengembalikan konten)"


@dataclass
class MCPServer:
    """Satu koneksi ke sebuah MCP server (stdio atau http)."""

    name: str
    transport: str  # "stdio" | "http"
    command: str = ""  # untuk stdio
    url: str = ""  # untuk http
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    # Per-server tool filtering: only tools matching an `allow` glob (and not
    # matching a `deny` glob) are exposed to the model. Both are fnmatch
    # patterns against the raw tool name. deny wins over allow.
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    # Sent as a header on every HTTP request so remote servers that key
    # behavior on caller identity (per-user limits, audit trails, multi-tenant
    # routing) can tell turns apart. Format: {"Header-Name": "value"}.
    identity: dict[str, str] = field(default_factory=dict)

    _process: subprocess.Popen | None = field(default=None, repr=False)
    _rpc_id: int = field(default=0, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)
    _initialized: bool = field(default=False, repr=False)

    def tool_visible(self, tool_name: str) -> bool:
        """Apply this server's allow/deny filter to a raw tool name."""
        import fnmatch
        for pattern in self.deny:
            if fnmatch.fnmatchcase(tool_name, pattern):
                return False
        if not self.allow:
            return True
        return any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in self.allow)

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    # ---- stdio transport ----
    def _ensure_process(self) -> None:
        if self._process and self._process.poll() is None:
            return
        if not self.command:
            raise RuntimeError(f"MCP server '{self.name}' stdio tanpa command.")
        env = {**os.environ, **self.env}
        self._process = subprocess.Popen(
            split_command(self.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            # JSON-RPC is UTF-8. Without this, Windows text mode uses the locale
            # code page (cp1252) and any non-ASCII payload raises/garbles.
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._initialized = False

    def _stdio_rpc(self, method: str, params: dict[str, Any] | None, *, notify: bool = False, timeout: int = _RPC_TIMEOUT) -> dict[str, Any] | None:
        self._ensure_process()
        assert self._process and self._process.stdin and self._process.stdout
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            message["id"] = self._next_id()
        line = json.dumps(message, ensure_ascii=False) + "\n"
        self._process.stdin.write(line)
        self._process.stdin.flush()
        if notify:
            return None
        deadline = time.monotonic() + timeout
        target_id = message["id"]
        while time.monotonic() < deadline:
            raw = self._process.stdout.readline()
            if not raw:
                if self._process.poll() is not None:
                    raise RuntimeError(f"MCP server '{self.name}' berhenti tak terduga.")
                continue
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            # Lewati notifikasi/log server; tunggu id yang cocok.
            if obj.get("id") == target_id:
                if obj.get("error"):
                    raise RuntimeError(f"MCP error: {obj['error']}")
                return obj.get("result", {})
        raise TimeoutError(f"MCP server '{self.name}' tidak menjawab {method} dalam {timeout}s.")

    # ---- http transport ----
    def _http_rpc(self, method: str, params: dict[str, Any] | None, *, notify: bool = False, timeout: int = _RPC_TIMEOUT) -> dict[str, Any] | None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            message["id"] = self._next_id()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
            **self.identity,
        }
        response = requests.post(self.url, headers=headers, json=message, timeout=timeout)
        if not response.ok:
            raise RuntimeError(f"MCP HTTP {response.status_code} dari '{self.name}'.")
        if notify:
            return None
        payload = _decode_jsonrpc_payload(response.text)
        if payload is None:
            return {}
        if payload.get("error"):
            raise RuntimeError(f"MCP error: {payload['error']}")
        return payload.get("result", {})

    def _rpc(self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False, timeout: int = _RPC_TIMEOUT) -> dict[str, Any] | None:
        with self._lock:
            if self.transport == "http":
                return self._http_rpc(method, params, notify=notify, timeout=timeout)
            return self._stdio_rpc(method, params, notify=notify, timeout=timeout)

    def initialize(self) -> None:
        """Handshake MCP: initialize lalu notifications/initialized."""
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "zeline", "version": __version__},
            },
            timeout=_INIT_TIMEOUT,
        )
        try:
            self._rpc("notifications/initialized", {}, notify=True)
        except Exception:
            pass  # sebagian server tidak butuh notifikasi ini
        self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        """Ambil daftar tool server sebagai schema OpenAI-compatible (ber-prefix)."""
        self.initialize()
        result = self._rpc("tools/list", {}) or {}
        tools = result.get("tools", []) if isinstance(result, dict) else []
        schemas: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            if not self.tool_visible(str(tool["name"])):
                continue
            schemas.append({
                "type": "function",
                "function": {
                    "name": make_tool_name(self.name, str(tool["name"])),
                    "description": str(tool.get("description", ""))[:1024],
                    "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
        return schemas

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> str:
        """Panggil satu tool MCP dan kembalikan teks hasilnya."""
        self.initialize()
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments}) or {}
        return _content_to_text(result)

    # ---- resources & prompts ----
    def list_resources(self) -> list[dict[str, Any]]:
        """Resources the server exposes: [{uri, name, description, mimeType}]."""
        self.initialize()
        try:
            result = self._rpc("resources/list", {}) or {}
        except RuntimeError:
            # Servers without the resources capability return a JSON-RPC error;
            # an empty list is the correct "nothing offered" answer.
            return []
        items = result.get("resources", []) if isinstance(result, dict) else []
        return [r for r in items if isinstance(r, dict) and r.get("uri")]

    def read_resource(self, uri: str) -> str:
        """Read one resource; returns its text content (or a JSON dump)."""
        self.initialize()
        result = self._rpc("resources/read", {"uri": uri}) or {}
        contents = result.get("contents", []) if isinstance(result, dict) else []
        parts: list[str] = []
        for item in contents:
            if not isinstance(item, dict):
                continue
            if item.get("text") is not None:
                parts.append(str(item["text"]))
            elif item.get("blob") is not None:
                parts.append(f"[binary resource: {item.get('mimeType', 'unknown')}, "
                             f"{len(str(item['blob']))} base64 chars]")
        return "\n".join(parts) if parts else "(resource kosong atau tidak ditemukan)"

    def list_prompts(self) -> list[dict[str, Any]]:
        """Prompt templates the server offers: [{name, description, arguments}]."""
        self.initialize()
        try:
            result = self._rpc("prompts/list", {}) or {}
        except RuntimeError:
            return []
        items = result.get("prompts", []) if isinstance(result, dict) else []
        return [p for p in items if isinstance(p, dict) and p.get("name")]

    def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> str:
        """Render a server prompt template; returns the message texts joined."""
        self.initialize()
        params: dict[str, Any] = {"name": name}
        if arguments:
            params["arguments"] = arguments
        result = self._rpc("prompts/get", params) or {}
        messages = result.get("messages", []) if isinstance(result, dict) else []
        parts: list[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "user"))
            content = msg.get("content", {})
            text = content.get("text", "") if isinstance(content, dict) else str(content)
            parts.append(f"[{role}] {text}")
        return "\n".join(parts) if parts else "(prompt kosong)"

    def close(self) -> None:
        process = self._process
        if process is not None:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except Exception:
                    try:
                        process.kill()
                        process.wait(timeout=1)
                    except Exception:
                        pass
            # Popen does not close these file objects merely because the child
            # exited. Leaving them for GC causes ResourceWarning and leaks OS
            # handles on long-running multi-session gateways.
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is None:
                    continue
                try:
                    stream.close()
                except Exception:
                    pass
        self._process = None
        self._initialized = False


class MCPRegistry:
    """Kumpulan MCP server aktif untuk satu proses agent.

    Tool di-cache setelah discovery pertama supaya schema tidak berubah di
    tengah percakapan (prompt caching aman). Kegagalan satu server tidak
    menjatuhkan yang lain — server bermasalah cuma dilewati.
    """

    def __init__(self, servers: list[MCPServer] | None = None):
        self.servers: dict[str, MCPServer] = {s.name: s for s in (servers or [])}
        self._tool_cache: list[dict[str, Any]] | None = None
        self._errors: dict[str, str] = {}

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "MCPRegistry":
        raw = (cfg.get("mcp") or {}).get("servers") or {}
        servers: list[MCPServer] = []
        for name, spec in raw.items():
            if not isinstance(spec, dict) or not spec.get("enabled", True):
                continue
            transport = str(spec.get("transport") or ("http" if spec.get("url") else "stdio"))
            servers.append(MCPServer(
                name=str(name),
                transport=transport,
                command=str(spec.get("command", "")),
                url=str(spec.get("url", "")),
                headers={str(k): str(v) for k, v in (spec.get("headers") or {}).items()},
                env={str(k): str(v) for k, v in (spec.get("env") or {}).items()},
                allow=[str(p) for p in (spec.get("allow") or [])],
                deny=[str(p) for p in (spec.get("deny") or [])],
                identity={str(k): str(v) for k, v in (spec.get("identity_header") or {}).items()},
            ))
        return cls(servers)

    def schemas(self) -> list[dict[str, Any]]:
        """Tools from every server (cached). A slow or dead server is skipped.

        Discovery used to run sequentially, so one unreachable server added its
        whole timeout to every first turn. Now each server is probed in its own
        thread under one shared deadline: total wait is bounded by the deadline,
        not by the number of broken servers.
        """
        if self._tool_cache is not None:
            return self._tool_cache
        collected: list[dict[str, Any]] = []
        if not self.servers:
            self._tool_cache = collected
            return collected

        results: dict[str, list[dict[str, Any]]] = {}

        def probe(name: str, server: Any) -> None:
            try:
                results[name] = list(server.list_tools())
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                self._errors[name] = f"{exc.__class__.__name__}: {exc}"

        threads = [
            threading.Thread(target=probe, args=(name, server), daemon=True)
            for name, server in self.servers.items()
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + _DISCOVERY_DEADLINE
        for name, thread in zip(self.servers, threads):
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                self._errors[name] = "TimeoutError: discovery deadline exceeded"
        for name in self.servers:
            collected.extend(results.get(name, []))
        self._tool_cache = collected
        return collected

    def has_tool(self, name: str) -> bool:
        parsed = parse_tool_name(name)
        return bool(parsed and parsed[0] in self.servers)

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        parsed = parse_tool_name(name)
        if not parsed:
            return f"ERROR: '{name}' bukan tool MCP."
        server_name, tool = parsed
        server = self.servers.get(server_name)
        if not server:
            return f"ERROR: MCP server '{server_name}' tidak terdaftar."
        try:
            return server.call_tool(tool, arguments)
        except Exception as exc:
            return f"ERROR MCP {server_name}.{tool}: {exc.__class__.__name__}: {exc}"

    def close(self) -> None:
        for server in self.servers.values():
            server.close()

    @property
    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def server(self, name: str) -> MCPServer | None:
        return self.servers.get(name)


def import_claude_mcp_servers(claude_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert a Claude Code / Cursor ``mcpServers`` block to Zeline config.

    Claude and Cursor store servers under ``mcpServers`` as
    ``{name: {command, args, env}}`` (stdio) or
    ``{name: {url, headers?}}`` (HTTP). Zeline's ``mcp.servers`` block
    uses the same keys, so the mapping is mostly renaming ``args`` into
    a single ``command`` string for stdio servers.
    """
    raw = claude_config.get("mcpServers") or {}
    out: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("url"):
            entry: dict[str, Any] = {"transport": "http", "url": str(spec["url"])}
            if spec.get("headers"):
                entry["headers"] = {str(k): str(v) for k, v in spec["headers"].items()}
        elif spec.get("command"):
            args = [str(a) for a in (spec.get("args") or [])]
            entry = {"transport": "stdio",
                     "command": " ".join(_shell_quote_join(str(spec["command"]), args))}
            if spec.get("env"):
                entry["env"] = {str(k): str(v) for k, v in spec["env"].items()}
        else:
            continue  # neither stdio nor http: skip with no error
        out[str(name)] = entry
    return out


def _shell_quote_join(command: str, args: list[str]) -> list[str]:
    """Join command + args into one command string, quoting where needed."""
    import shlex
    quoted = [command if not re.search(r"[\s\"']", command) else shlex.quote(command)]
    for arg in args:
        quoted.append(shlex.quote(arg) if re.search(r"[\s\"']", arg) else arg)
    return quoted
