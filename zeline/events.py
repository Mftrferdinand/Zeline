"""Append-only provenance log for mutating tool calls.

Zeline could already lose the record of a state change: when a turn dies AFTER
the model wrote a file, saved a skill, or stored a memory, the mutation happened
but the turn transcript can be dropped (session save runs only after a SUCCESSFUL
turn). Nothing then explains *what changed, from which session, and whether it
succeeded* — so drift is unauditable and reflection is impossible to roll back.

This module is the audit trail. Design decisions, stated plainly:

- **Only mutating tools are logged.** Read-only calls (read_file, web_search,
  list_memory, runtime_info…) would drown the signal; the log is for side
  effects, not activity tracing.
- **Recording must never break a turn.** Every write is best-effort, exactly
  like usage_stats: a locked or unwritable DB loses one audit row, which is
  strictly better than losing the user's answer.
- **Written at tool time, not turn end.** The event is committed the moment the
  tool runs, so a turn that raises afterwards still leaves the side effect on
  record.
- **Identity is hashed** (SHA-256, like memory/sessions) so chat IDs never land
  in the table, and rows are isolated per identity.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from zeline import config

_LOCK = threading.Lock()

#: Tools whose call changes durable state and therefore belongs in the audit
#: trail. Everything not listed here (read_file, search_files, web_search,
#: web_fetch, deep_research, list_memory, runtime_info, load_skill, recall_history,
#: code_intel, process_control, ask_user, browser reads…) is deliberately NOT
#: logged: the log is for side effects, not for tracing every read.
MUTATING_TOOLS = frozenset({
    "write_file",
    "edit_file",
    "patch_file",
    "download_file",
    "undo_file",
    "add_memory",
    "remove_memory",
    "manage_skill",
    "update_task",
    "generate_image",
    "run_shell",
    "execute_code",
    "http_request",
    "schedule_task",
})

#: Detail text is a short, human-readable hint (what path / which action), never
#: the full tool output — the log is an index, not a second transcript.
_MAX_DETAIL = 200


def _db_path() -> Path:
    return config.DATA_DIR / "events.db"


def _key(identity: str) -> str:
    return hashlib.sha256((identity or "cli:local").encode("utf-8")).hexdigest()[:32]


def is_mutating(tool: str) -> bool:
    return tool in MUTATING_TOOLS


def detail_for(tool: str, args: dict[str, Any]) -> str:
    """A short, safe descriptor of a mutating call for the audit row.

    Prefers the argument that says *what was touched* (path, skill name, task),
    and never echoes file contents, shell command bodies, or memory text in full
    so the log cannot become a place secrets accumulate.
    """
    if not isinstance(args, dict):
        return ""
    if tool in {"write_file", "edit_file", "patch_file", "download_file", "undo_file"}:
        action = str(args.get("action", "")).strip()
        path = str(args.get("path", "")).strip()
        return f"{action} {path}".strip() if action else path
    if tool == "manage_skill":
        return f"{str(args.get('action', '')).strip()} {str(args.get('name', '')).strip()}".strip()
    if tool == "update_task":
        return f"{str(args.get('status', '')).strip()} {str(args.get('task', '')).strip()}".strip()
    if tool == "schedule_task":
        return str(args.get("action", "")).strip()
    if tool == "add_memory":
        # First few words only: enough to recognise, not the whole fact.
        return " ".join(str(args.get("fact", "")).split()[:8])
    if tool == "remove_memory":
        return " ".join(str(args.get("substring", "")).split()[:8])
    if tool == "generate_image":
        return str(args.get("path", "")).strip()
    if tool in {"run_shell", "execute_code"}:
        # The verb only. A command body can carry secrets and is itself the kind
        # of thing an audit reader should open the transcript for, not the log.
        return tool
    if tool == "http_request":
        return f"{str(args.get('method', '')).strip()} {str(args.get('url', '')).strip()}".strip()
    return ""


class EventLog:
    """SQLite-backed append-only audit trail. Every op degrades, never raises."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _db_path()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            # If setup fails, close before propagating so a failed connect can
            # never leak an open handle (ResourceWarning / Windows file lock).
            conn.close()
            raise
        return conn

    def _ensure_schema(self) -> None:
        try:
            with _LOCK, closing(self._connect()) as conn, conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS events ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  key TEXT NOT NULL,"
                    "  tool TEXT NOT NULL,"
                    "  status TEXT NOT NULL,"
                    "  detail TEXT,"
                    "  ts REAL NOT NULL"
                    ")"
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_events_key ON events(key, ts)")
        except sqlite3.Error:
            return
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def record(
        self,
        identity: str,
        tool: str,
        status: str,
        detail: str = "",
        ts: float | None = None,
    ) -> bool:
        """Append one audit row. Returns True when stored, False on any failure."""
        moment = time.time() if ts is None else ts
        try:
            with _LOCK, closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO events (key, tool, status, detail, ts) VALUES (?, ?, ?, ?, ?)",
                    (_key(identity), str(tool), str(status), str(detail or "")[:_MAX_DETAIL], moment),
                )
            return True
        except (sqlite3.Error, OSError, ValueError, TypeError):
            # Losing an audit row must never cost the user their answer.
            return False

    def recent(self, identity: str, limit: int = 20) -> list[dict[str, Any]]:
        """Most recent audit rows for an identity, newest first."""
        try:
            with _LOCK, closing(self._connect()) as conn, conn:
                rows = conn.execute(
                    "SELECT tool, status, detail, ts FROM events WHERE key = ? "
                    "ORDER BY ts DESC, id DESC LIMIT ?",
                    (_key(identity), max(1, int(limit))),
                ).fetchall()
        except sqlite3.Error:
            return []
        return [
            {"tool": str(r[0]), "status": str(r[1]), "detail": str(r[2] or ""), "ts": float(r[3] or 0.0)}
            for r in rows
        ]

    def counts(self, identity: str) -> dict[str, int]:
        """How many times each tool mutated state for this identity."""
        try:
            with _LOCK, closing(self._connect()) as conn, conn:
                rows = conn.execute(
                    "SELECT tool, COUNT(*) FROM events WHERE key = ? GROUP BY tool",
                    (_key(identity),),
                ).fetchall()
        except sqlite3.Error:
            return {}
        return {str(tool): int(count or 0) for tool, count in rows}


def log_tool_call(identity: str, tool: str, args: dict[str, Any], result: str) -> None:
    """Record a mutating tool call as an audit event. Best-effort, silent.

    Called from the tool executor right after a tool runs. Read-only tools are
    skipped so the log stays a side-effect index, not an activity trace. The
    status is derived from the result string's ERROR convention used across the
    tool layer.
    """
    if not is_mutating(tool):
        return
    status = "error" if str(result).startswith("ERROR") else "ok"
    try:
        EventLog().record(identity, tool, status, detail=detail_for(tool, args))
    except Exception:
        # Absolutely never propagate: the audit trail is best-effort.
        pass
