"""Language Server Protocol client: ask a real language server about the code.

Why this exists: the agent currently reads code as text. It can grep for a name,
but grep cannot tell a definition from a mention in a comment, cannot follow a
symbol through an import, and cannot say whether an edit type-checks. A language
server can answer all three, because it has actually parsed the project.

Four decisions, each with a tempting wrong answer:

**No new dependency, and no bundled servers.** The server is the operator's own
binary, discovered from PATH per language. LSP is JSON-RPC over stdio with
``Content-Length`` headers, which is small enough to implement directly. Shipping
a client that downloads language servers would put a package manager inside
Zeline for a feature many users never touch.

**Diagnostics are pulled, not awaited blindly.** A server publishes diagnostics
whenever it feels like it, and a client that waits for a notification hangs
forever on a clean file — the correct answer for clean code is *no message at
all*. So this drains notifications until the server goes quiet and reports what
arrived, including the empty case, rather than blocking on a message that may
never come.

**One tool, several actions.** Same reasoning as ``browser`` after #188 made
schema size a measured cost.

**A server that fails is reported, never fatal.** A missing binary, a crash mid
request, or a timeout returns text the model can act on. The alternative — an
exception escaping into the turn — would make an optional convenience able to
kill a task.

Servers are started on first use per language and reused, because initialization
is the expensive part (a server indexes the project) and an agent asks several
questions in a row.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from zeline import config

ALLOWED_PROFILES = frozenset({"workspace", "full"})

# Language server candidates per language, in preference order. Whatever the
# operator has installed wins; nothing is ever downloaded.
SERVERS: dict[str, tuple[tuple[str, ...], ...]] = {
    "python": (
        ("basedpyright-langserver", "--stdio"),
        ("pyright-langserver", "--stdio"),
        ("jedi-language-server",),
        ("pylsp",),
        ("ruff", "server"),
    ),
    "typescript": (("typescript-language-server", "--stdio"),),
    "javascript": (("typescript-language-server", "--stdio"),),
    "go": (("gopls",),),
    "rust": (("rust-analyzer",),),
    "c": (("clangd",),),
    "cpp": (("clangd",),),
}

EXTENSIONS: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
}

LANGUAGE_IDS = {"cpp": "cpp", "c": "c", "typescript": "typescript", "javascript": "javascript"}

REQUEST_TIMEOUT = 20.0
INIT_TIMEOUT = 30.0
# How long to wait for diagnostics before concluding the file is clean. A server
# has to parse the project first, so this cannot be tiny; but a clean file will
# never produce anything, so it cannot be a blocking wait either.
DIAGNOSTIC_SETTLE = 3.0

SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}


class LspError(RuntimeError):
    """Something the model should be told plainly."""


def enabled() -> bool:
    return bool(getattr(config, "LSP", True))


def language_for(path: str | Path) -> str | None:
    return EXTENSIONS.get(Path(path).suffix.lower())


def find_server(language: str) -> tuple[str, ...] | None:
    configured = getattr(config, "LSP_SERVERS", {}) or {}
    override = configured.get(language)
    if override:
        parts = tuple(str(override).split()) if isinstance(override, str) else tuple(override)
        if parts and (shutil.which(parts[0]) or Path(parts[0]).is_file()):
            return parts
        return None
    for candidate in SERVERS.get(language, ()):
        found = shutil.which(candidate[0])
        if found:
            return (found, *candidate[1:])
    return None


def available() -> dict[str, tuple[str, ...] | None]:
    return {language: find_server(language) for language in sorted(SERVERS)}


def _uri(path: Path) -> str:
    return path.resolve(strict=False).as_uri()


@dataclass
class Diagnostic:
    line: int
    character: int
    severity: str
    message: str
    source: str = ""

    def render(self, path: Path) -> str:
        origin = f" [{self.source}]" if self.source else ""
        return f"{path.name}:{self.line + 1}:{self.character + 1}: {self.severity}{origin}: {self.message}"


@dataclass
class LanguageServer:
    """One language server process, spoken to over stdio JSON-RPC."""

    language: str
    argv: tuple[str, ...]
    root: Path
    _process: Any = None
    _next_id: int = 0
    _lock: Any = field(default_factory=threading.Lock)
    # Framed messages arrive on a reader thread. Reading inline is not an option:
    # stdout.readline() blocks with no timeout, so a server that says nothing --
    # which is exactly what a clean file produces -- would hang the agent
    # forever. The thread turns "no message" into an empty queue instead.
    _inbox: Any = field(default_factory=queue.Queue)
    _reader: Any = None
    # Notifications seen while waiting for a request reply, kept for drain().
    _notifications: list[dict[str, Any]] = field(default_factory=list)
    _open_files: set[str] = field(default_factory=set)
    _capabilities: dict[str, Any] = field(default_factory=dict)

    # -- process -----------------------------------------------------------
    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                list(self.argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(self.root),
            )
        except OSError as exc:
            raise LspError(f"could not start {self.argv[0]}: {exc.__class__.__name__}") from exc
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        try:
            self._initialize()
        except LspError:
            self.stop()
            raise

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        with contextlib.suppress(Exception):
            process.stdin.close()
        with contextlib.suppress(Exception):
            process.terminate()
            process.wait(timeout=5)
        if process.poll() is None:
            with contextlib.suppress(Exception):
                process.kill()
        self._open_files.clear()

    # -- framing -----------------------------------------------------------
    def _write(self, message: dict[str, Any]) -> None:
        if not self.running:
            raise LspError(f"{self.argv[0]} is not running")
        body = json.dumps(message).encode("utf-8")
        try:
            self._process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise LspError(f"{self.argv[0]} closed its input") from exc

    def _pump(self) -> None:
        """Read framed messages off stdout forever, onto the inbox queue.

        Runs on its own thread so a silent server can never block a request.
        """
        stream = self._process.stdout if self._process else None
        if stream is None:
            return
        while True:
            headers: dict[str, str] = {}
            while True:
                line = stream.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    break
                key, _, value = text.partition(":")
                headers[key.strip().lower()] = value.strip()
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                continue
            if length <= 0:
                continue
            payload = b""
            while len(payload) < length:
                chunk = stream.read(length - len(payload))
                if not chunk:
                    return
                payload += chunk
            try:
                self._inbox.put(json.loads(payload.decode("utf-8")))
            except json.JSONDecodeError:
                continue

    def _read(self, timeout: float) -> dict[str, Any] | None:
        """Next message, or None if the server said nothing in time."""
        try:
            return self._inbox.get(timeout=max(0.05, timeout))
        except queue.Empty:
            return None

    def _request(self, method: str, params: dict[str, Any] | None = None, timeout: float = REQUEST_TIMEOUT) -> Any:
        with self._lock:
            self._next_id += 1
            message_id = self._next_id
            self._write({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}})
            deadline = time.time() + timeout
            while time.time() < deadline:
                message = self._read(max(0.2, deadline - time.time()))
                if message is None:
                    break
                if message.get("id") == message_id and ("result" in message or "error" in message):
                    if "error" in message:
                        raise LspError(str(message["error"].get("message", "LSP error")))
                    return message.get("result")
                if "method" in message:
                    if "id" in message:
                        # A server-to-client request must be answered or some
                        # servers stall waiting; a null result is a valid answer.
                        self._write({"jsonrpc": "2.0", "id": message["id"], "result": None})
                    else:
                        # Not ours: put it back for drain() to find.
                        self._notifications.append(message)
            raise LspError(f"{method} timed out after {timeout:.0f}s")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def drain(self, settle: float = DIAGNOSTIC_SETTLE) -> list[dict[str, Any]]:
        """Collect notifications until the server goes quiet.

        Diagnostics are pushed, not returned from a request, and a clean file
        produces none at all -- so waiting for one would hang forever on exactly
        the input we most want to confirm.
        """
        with self._lock:
            collected = list(self._notifications)
            self._notifications.clear()
            deadline = time.time() + settle
            while time.time() < deadline:
                message = self._read(0.35)
                if message is None:
                    continue
                if "method" in message:
                    if "id" in message:
                        self._write({"jsonrpc": "2.0", "id": message["id"], "result": None})
                    else:
                        collected.append(message)
                        # Something arrived, so allow a little longer for more.
                        deadline = max(deadline, time.time() + 0.6)
        return collected

    # -- lifecycle ---------------------------------------------------------
    def _initialize(self) -> None:
        result = self._request("initialize", {
            "processId": os.getpid(),
            "clientInfo": {"name": "Zeline", "version": "0.2.5"},
            "rootUri": _uri(self.root),
            "workspaceFolders": [{"uri": _uri(self.root), "name": self.root.name}],
            "capabilities": {
                "textDocument": {
                    "synchronization": {"didSave": True, "dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": False},
                    "hover": {"contentFormat": ["plaintext", "markdown"]},
                    "definition": {"linkSupport": True},
                    "references": {},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                },
                "workspace": {"workspaceFolders": True, "symbol": {}},
            },
        }, timeout=INIT_TIMEOUT)
        self._capabilities = (result or {}).get("capabilities", {}) if isinstance(result, dict) else {}
        self._notify("initialized", {})

    # Capability name in the server's initialize reply, per feature. Servers
    # differ enormously -- ruff's server does diagnostics only, clangd does
    # nearly everything -- so a feature is checked before it is requested.
    CAPABILITY_KEYS: ClassVar[dict[str, str]] = {
        "definition": "definitionProvider",
        "references": "referencesProvider",
        "hover": "hoverProvider",
        "symbols": "documentSymbolProvider",
    }

    def supports(self, feature: str) -> bool:
        key = self.CAPABILITY_KEYS.get(feature)
        if key is None:
            return True
        return bool(self._capabilities.get(key))

    def require(self, feature: str) -> None:
        if not self.supports(feature):
            name = Path(self.argv[0]).name
            available_here = sorted(
                item for item, key in self.CAPABILITY_KEYS.items()
                if self._capabilities.get(key)
            )
            raise LspError(
                f"{name} does not provide '{feature}' for {self.language}. "
                f"It supports: {', '.join(available_here) or 'diagnostics only'}. "
                "Install a fuller server (for Python: basedpyright, pyright, or "
                "jedi-language-server) to use this action."
            )

    def open_file(self, path: Path) -> None:
        uri = _uri(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise LspError(f"cannot read {path.name}: {exc.__class__.__name__}") from exc
        if uri in self._open_files:
            # Re-send as a full-content change so the server sees edits made
            # since it was opened; otherwise it answers about stale text.
            self._notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": int(time.time())},
                "contentChanges": [{"text": text}],
            })
            return
        self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": LANGUAGE_IDS.get(self.language, self.language),
                "version": 1,
                "text": text,
            },
        })
        self._open_files.add(uri)


class LspRegistry:
    """One language server per language, for one agent."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.servers: dict[str, LanguageServer] = {}

    def server_for(self, path: Path) -> LanguageServer:
        language = language_for(path)
        if language is None:
            raise LspError(
                f"{path.suffix or 'that file type'} has no configured language server. "
                f"Supported: {', '.join(sorted(SERVERS))}."
            )
        existing = self.servers.get(language)
        if existing is not None and existing.running:
            return existing
        if existing is not None:
            # A crashed server is replaced, not reused, so one crash does not
            # break every later request.
            existing.stop()
        argv = find_server(language)
        if argv is None:
            candidates = ", ".join(item[0] for item in SERVERS.get(language, ()))
            raise LspError(
                f"no {language} language server found. Install one of: {candidates} "
                "(or set tools.lsp_servers in the config)."
            )
        server = LanguageServer(language=language, argv=argv, root=self.root)
        server.start()
        self.servers[language] = server
        return server

    def shutdown(self) -> None:
        for server in self.servers.values():
            server.stop()
        self.servers.clear()

    # -- features ----------------------------------------------------------
    def diagnostics(self, path: Path) -> str:
        server = self.server_for(path)
        server.open_file(path)
        uri = _uri(path)
        found: list[Diagnostic] = []
        for message in server.drain():
            if message.get("method") != "textDocument/publishDiagnostics":
                continue
            params = message.get("params") or {}
            if params.get("uri") != uri:
                continue
            found = [
                Diagnostic(
                    line=int((item.get("range", {}).get("start", {}) or {}).get("line", 0)),
                    character=int((item.get("range", {}).get("start", {}) or {}).get("character", 0)),
                    severity=SEVERITY.get(int(item.get("severity") or 3), "info"),
                    message=" ".join(str(item.get("message", "")).split()),
                    source=str(item.get("source", "")),
                )
                for item in params.get("diagnostics", [])
            ]
        if not found:
            # Stated positively: an empty result is the answer, not a failure.
            return f"No diagnostics reported for {path.name} by {server.argv[0]}."
        found.sort(key=lambda item: (item.line, item.character))
        head = f"{len(found)} diagnostic(s) in {path.name} from {server.argv[0]}:"
        return head + "\n" + "\n".join(item.render(path) for item in found[:60])

    def definition(self, path: Path, line: int, character: int) -> str:
        server = self.server_for(path)
        server.require("definition")
        server.open_file(path)
        result = server._request("textDocument/definition", {
            "textDocument": {"uri": _uri(path)},
            "position": {"line": max(0, line - 1), "character": max(0, character)},
        })
        locations = _as_locations(result)
        if not locations:
            return f"No definition found at {path.name}:{line}:{character}."
        return "Definition:\n" + "\n".join(locations[:10])

    def references(self, path: Path, line: int, character: int) -> str:
        server = self.server_for(path)
        server.require("references")
        server.open_file(path)
        result = server._request("textDocument/references", {
            "textDocument": {"uri": _uri(path)},
            "position": {"line": max(0, line - 1), "character": max(0, character)},
            "context": {"includeDeclaration": True},
        })
        locations = _as_locations(result)
        if not locations:
            return f"No references found at {path.name}:{line}:{character}."
        shown = locations[:40]
        text = f"{len(locations)} reference(s):\n" + "\n".join(shown)
        if len(locations) > len(shown):
            text += f"\n... [{len(locations) - len(shown)} more]"
        return text

    def hover(self, path: Path, line: int, character: int) -> str:
        server = self.server_for(path)
        server.require("hover")
        server.open_file(path)
        result = server._request("textDocument/hover", {
            "textDocument": {"uri": _uri(path)},
            "position": {"line": max(0, line - 1), "character": max(0, character)},
        })
        text = _hover_text(result)
        if not text:
            return f"No hover information at {path.name}:{line}:{character}."
        return text[:4000]

    def symbols(self, path: Path) -> str:
        server = self.server_for(path)
        server.require("symbols")
        server.open_file(path)
        result = server._request("textDocument/documentSymbol", {
            "textDocument": {"uri": _uri(path)},
        })
        lines = _flatten_symbols(result or [])
        if not lines:
            return f"No symbols reported for {path.name}."
        return f"{len(lines)} symbol(s) in {path.name}:\n" + "\n".join(lines[:80])


SYMBOL_KINDS = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class", 6: "method",
    7: "property", 8: "field", 9: "constructor", 10: "enum", 11: "interface",
    12: "function", 13: "variable", 14: "constant", 15: "string", 16: "number",
    17: "boolean", 18: "array", 19: "object", 20: "key", 21: "null",
    22: "enum-member", 23: "struct", 24: "event", 25: "operator", 26: "type-parameter",
}


def _flatten_symbols(nodes: Any, depth: int = 0) -> list[str]:
    lines: list[str] = []
    if not isinstance(nodes, list):
        return lines
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name", "?"))
        kind = SYMBOL_KINDS.get(int(node.get("kind") or 0), "symbol")
        # DocumentSymbol nests; SymbolInformation is flat with a location.
        span = node.get("range") or (node.get("location") or {}).get("range") or {}
        line = int((span.get("start") or {}).get("line", 0)) + 1
        lines.append(f"{'  ' * depth}{kind} {name} (line {line})")
        lines.extend(_flatten_symbols(node.get("children") or [], depth + 1))
    return lines


def _as_locations(result: Any) -> list[str]:
    items = result if isinstance(result, list) else ([result] if isinstance(result, dict) else [])
    out: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = str(item.get("uri") or item.get("targetUri") or "")
        span = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange") or {}
        start = span.get("start") or {}
        name = uri.rsplit("/", 1)[-1] or uri
        out.append(f"  {name}:{int(start.get('line', 0)) + 1}:{int(start.get('character', 0)) + 1}")
    return out


def _hover_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    contents = result.get("contents")
    parts: list[str] = []
    if isinstance(contents, str):
        parts.append(contents)
    elif isinstance(contents, dict):
        parts.append(str(contents.get("value", "")))
    elif isinstance(contents, list):
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("value", "")))
    return "\n".join(part.strip() for part in parts if part.strip())
