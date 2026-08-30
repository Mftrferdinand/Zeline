"""Export, import, and fork a conversation session.

OpenCode ships session export/import/fork (`--fork`, JSON dump, import from a
file). The uses are practical: hand a debugging transcript to someone else, move
a conversation between machines, or branch an exploration without destroying the
original.

Zeline stores history per identity in SQLite with the identity hashed, so this
module works in terms of the identity string the operator supplies.

Three properties are non-negotiable, because an imported file becomes the
model's own conversation history:

- **Imported `system` messages are dropped.** The runtime builds the system
  prompt from config, SOUL, memory, and project rules. Honouring a `system`
  message out of a file would let anyone who hands you a transcript rewrite the
  agent's instructions — a prompt-injection channel with system authority.
- **Tool-call protocol is repaired, not trusted.** A history whose tail is an
  `assistant(tool_calls)` without its `tool` results makes the provider reject
  the next message. Imports are truncated to a clean boundary.
- **Exports are written 0600 and the operator is told what is inside.** A
  transcript can contain anything that was discussed, including secrets the
  operator pasted. We do not silently scrub (that would corrupt a debugging
  artifact) — we state it plainly.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from zeline import __version__

# Bumped only on an incompatible layout change; the importer accepts anything
# it can structurally validate, so this is provenance rather than a gate.
FORMAT_VERSION = 1

# Defensive ceiling for an imported file. A transcript this large is either a
# mistake or an attempt to blow up the context window.
MAX_IMPORT_BYTES = 20_000_000
MAX_IMPORT_MESSAGES = 5000

# Roles allowed to survive an import. `system` is deliberately absent.
IMPORTABLE_ROLES = frozenset({"user", "assistant", "tool"})


def _clean_message(raw: Any) -> dict[str, Any] | None:
    """Keep only the fields the agent loop actually reads."""
    if not isinstance(raw, dict):
        return None
    role = str(raw.get("role", "")).strip()
    if role not in IMPORTABLE_ROLES:
        return None
    message: dict[str, Any] = {"role": role}
    content = raw.get("content")
    if content is None:
        message["content"] = ""
    elif isinstance(content, (str, list, dict)):
        message["content"] = content
    else:
        message["content"] = str(content)
    # Preserve the tool-call wiring so a restored transcript stays valid.
    for field in ("tool_calls", "tool_call_id", "name"):
        if field in raw and raw[field] is not None:
            message[field] = raw[field]
    return message


def _repair_tail(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a trailing assistant(tool_calls) that has no tool results.

    The provider rejects the NEXT message when a tool-call turn is incomplete,
    so an imported session must not start life in that state.
    """
    repaired = list(messages)
    while repaired:
        last = repaired[-1]
        if last.get("role") == "tool":
            # A tool result with no preceding assistant(tool_calls) is orphaned.
            has_parent = any(
                item.get("role") == "assistant" and item.get("tool_calls")
                for item in repaired[:-1]
            )
            if has_parent:
                break
            repaired.pop()
            continue
        if last.get("role") == "assistant" and last.get("tool_calls"):
            repaired.pop()
            continue
        break
    return repaired


def build_export(
    identity: str,
    messages: list[dict[str, Any]],
    title: str | None,
    archive: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the export payload. The identity is recorded for provenance."""
    return {
        "zeline_session": FORMAT_VERSION,
        "exported_by": f"zeline {__version__}",
        "exported_at": time.time(),
        "identity": identity,
        "title": title or "",
        "message_count": len(messages),
        "messages": messages,
        "archive": archive or [],
    }


def write_export(path: str | Path, payload: dict[str, Any]) -> Path:
    """Write an export to disk with private permissions."""
    target = Path(path).expanduser()
    if target.is_dir():
        target = target / "zeline-session.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def read_export(path: str | Path) -> tuple[dict[str, Any] | None, str]:
    """Load and structurally validate an export file. Returns (payload, error)."""
    source = Path(path).expanduser()
    try:
        size = source.stat().st_size
    except OSError as exc:
        return None, f"cannot read {source}: {exc.__class__.__name__}"
    if size > MAX_IMPORT_BYTES:
        return None, f"file too large ({size} bytes; limit {MAX_IMPORT_BYTES})"
    try:
        payload = json.loads(source.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"not readable JSON: {exc.__class__.__name__}"
    if not isinstance(payload, dict):
        return None, "expected a JSON object at the top level"
    if "messages" not in payload or not isinstance(payload["messages"], list):
        return None, "missing a 'messages' array — not a Zeline session export"
    return payload, ""


def sanitize_messages(raw_messages: list[Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Convert exported messages into a safe, protocol-valid history.

    Returns the cleaned list plus counters describing what was dropped, so the
    caller can tell the operator instead of silently mutating their data.
    """
    stats = {"system_dropped": 0, "invalid_dropped": 0, "truncated": 0}
    cleaned: list[dict[str, Any]] = []
    for raw in raw_messages[:MAX_IMPORT_MESSAGES]:
        if isinstance(raw, dict) and str(raw.get("role", "")).strip() == "system":
            # The runtime owns the system prompt; an imported one would carry
            # system authority from an untrusted file.
            stats["system_dropped"] += 1
            continue
        message = _clean_message(raw)
        if message is None:
            stats["invalid_dropped"] += 1
            continue
        cleaned.append(message)
    if len(raw_messages) > MAX_IMPORT_MESSAGES:
        stats["truncated"] = len(raw_messages) - MAX_IMPORT_MESSAGES
    before = len(cleaned)
    cleaned = _repair_tail(cleaned)
    stats["invalid_dropped"] += before - len(cleaned)
    return cleaned, stats
