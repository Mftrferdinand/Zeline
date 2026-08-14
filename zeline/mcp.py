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
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from zeline import __version__

MCP_TOOL_PREFIX = "mcp__"
_RPC_TIMEOUT = 30
_INIT_TIMEOUT = 20
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

    _process: subprocess.Popen | None = field(default=None, repr=False)
    _rpc_id: int = field(default=0, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)
    _initialized: bool = field(default=False, repr=False)

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
            ))
        return cls(servers)

    def schemas(self) -> list[dict[str, Any]]:
        """Semua tool dari semua server (cached). Server error dilewati."""
        if self._tool_cache is not None:
            return self._tool_cache
        collected: list[dict[str, Any]] = []
        for name, server in self.servers.items():
            try:
                collected.extend(server.list_tools())
            except Exception as exc:
                self._errors[name] = f"{exc.__class__.__name__}: {exc}"
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
