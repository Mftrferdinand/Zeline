"""A task board that survives the things that actually erase one.

``update_task`` used to validate its arguments and return them as JSON. Nothing
was stored. The Telegram feed rendered a tidy ``📋 Updating tasks: in_progress ·
Run tests`` line for data that was discarded the moment the tool returned, so the
agent's plan lived only inside the message history — the one place guaranteed to be
truncated on long work.

Two losses matter, and they are not the same:

**Compaction.** ``_trim_history`` drops whole user turns to fit the window. On a
multi-step build that is exactly when the plan is needed most, and exactly when it
disappears. The board is on disk, and the compaction digest lists what is still
open, so trimming no longer costs the agent its own plan.

**Process restart.** The gateway is restarted often on a phone. A board held in RAM
dies with it; this one is read back when the session is rebuilt.

``/new`` deliberately does NOT survive: it clears history on purpose, and a fresh
session that starts holding stale tasks is the same pollution problem as memory that
never forgets. Unfinished items are appended to an archive file first, so the work
is recoverable rather than silently dropped.

Storage mirrors ``zeline.memory``: one JSON file per identity, hashed name, 0600.
A Telegram bot with many users must not let one chat's board grow into another's,
and must not put a chat id in a filename.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from zeline import config

STATUSES = ("pending", "in_progress", "completed", "cancelled")
OPEN_STATUSES = ("in_progress", "pending")

# Defensive bounds, same reasoning as memory: a public gateway must not let one
# chat fill the owner's disk. A plan longer than this is not a plan.
MAX_ITEMS = 60
MAX_TASK_CHARS = 300

#: Rendered into the compaction digest and the system prompt. Kept small on
#: purpose — the board is a reminder of what is open, not a second transcript.
MAX_BOARD_CHARS = 1_200


def tasks_dir() -> Path:
    return config.DATA_DIR / "tasks"


def _key(identity: str) -> str:
    return hashlib.sha256((identity or "cli:local").encode("utf-8")).hexdigest()[:32]


def _path(identity: str) -> Path:
    return tasks_dir() / f"{_key(identity)}.json"


def archive_path() -> Path:
    return tasks_dir() / "cleared.md"


#: Filler words dropped before matching two task descriptions. Deliberately tiny
#: and limited to articles/particles in the two languages this agent is driven in:
#: a longer list starts merging genuinely different tasks.
_FILLER = frozenset({"the", "a", "an", "to", "for", "of", "yang", "itu", "nya", "buat"})


def _normalize(text: str) -> str:
    """Matching key for a task description.

    The model rarely repeats a task title byte-for-byte: "Write contract tests"
    becomes "write the contract tests" on the second call. Matching the raw string
    creates a new item on every update, so the board grows instead of progressing —
    which is what a first version of this module actually did.

    Word ORDER is dropped too ("tests for the scheduler" / "scheduler tests"), but
    the remaining word SET must match exactly. A similarity threshold was the
    tempting alternative and is worse: silently merging "Write installer tests" into
    "Write contract tests" loses a task with no way for the operator to notice.
    """
    lowered = (text or "").casefold()
    stripped = re.sub(r"[^a-z0-9\s]+", " ", lowered)
    words = [word for word in stripped.split() if word not in _FILLER]
    return " ".join(sorted(words))


def _read(identity: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(_path(identity).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        task = str(entry.get("task") or "").strip()
        status = str(entry.get("status") or "").strip().lower()
        if not task or status not in STATUSES:
            continue
        items.append(
            {
                "task": task[:MAX_TASK_CHARS],
                "status": status,
                "created_at": float(entry.get("created_at") or 0.0),
                "updated_at": float(entry.get("updated_at") or 0.0),
            }
        )
    return items


def _write(identity: str, items: list[dict[str, Any]]) -> None:
    directory = tasks_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    target = _path(identity)
    # Unique temp name: two turns of the same identity can finish together, and a
    # shared `.tmp` had concurrent writers clobbering each other before the rename
    # (the same bug the scheduler's jobs.json hit).
    temporary = target.with_name(f"{target.stem}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)


def load(identity: str) -> list[dict[str, Any]]:
    """Every item on this identity's board, in creation order."""
    return sorted(_read(identity), key=lambda item: item.get("created_at", 0.0))


def open_items(identity: str) -> list[dict[str, Any]]:
    """Tasks still to do: in_progress first, then pending."""
    items = load(identity)
    return [item for status in OPEN_STATUSES for item in items if item["status"] == status]


def update(identity: str, task: str, status: str) -> tuple[list[dict[str, Any]], str]:
    """Upsert one task. Returns (board, note) where note explains any surprise."""
    clean_task = " ".join((task or "").split())[:MAX_TASK_CHARS]
    clean_status = (status or "").strip().lower()
    if not clean_task:
        raise ValueError("empty description.")
    if clean_status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}.")

    items = load(identity)
    key = _normalize(clean_task)
    now = time.time()
    note = ""
    for item in items:
        if _normalize(item["task"]) == key:
            item["status"] = clean_status
            item["updated_at"] = now
            # Keep the newer wording: the model's latest phrasing is what it will
            # use next time, so storing the old title guarantees a mismatch later.
            item["task"] = clean_task
            break
    else:
        if len(items) >= MAX_ITEMS:
            # Drop the oldest finished item rather than refusing the update: a
            # rejected write would leave the board frozen and silently wrong.
            finished = [
                item for item in items if item["status"] in ("completed", "cancelled")
            ]
            if finished:
                items.remove(min(finished, key=lambda item: item.get("updated_at", 0.0)))
                note = f"board was full ({MAX_ITEMS}); dropped the oldest finished item."
            else:
                raise ValueError(
                    f"the board already holds {MAX_ITEMS} unfinished tasks — "
                    "complete or cancel some before adding more."
                )
        items.append(
            {"task": clean_task, "status": clean_status, "created_at": now, "updated_at": now}
        )
    _write(identity, items)
    return load(identity), note


def clear(identity: str, *, archive: bool = True) -> int:
    """Empty the board. Returns how many items were removed.

    Unfinished work is appended to ``tasks/cleared.md`` first: ``/new`` is a
    deliberate reset, but losing a half-done plan with no trace is not what the
    operator asked for.
    """
    items = load(identity)
    if not items:
        _path(identity).unlink(missing_ok=True)
        return 0
    if archive:
        pending = [item for item in items if item["status"] in OPEN_STATUSES]
        if pending:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            body = "\n".join(f"- [{item['status']}] {item['task']}" for item in pending)
            try:
                tasks_dir().mkdir(parents=True, exist_ok=True)
                with archive_path().open("a", encoding="utf-8") as handle:
                    handle.write(f"\n## Cleared {stamp}\n\n{body}\n")
                os.chmod(archive_path(), 0o600)
            except OSError:
                pass
    _path(identity).unlink(missing_ok=True)
    return len(items)


_SYMBOLS = {
    "completed": "[x]",
    "in_progress": "[>]",
    "pending": "[ ]",
    "cancelled": "[-]",
}


def render(items: list[dict[str, Any]]) -> str:
    """The board as the model should read it back."""
    if not items:
        return "(no tasks)"
    lines = [f"{_SYMBOLS.get(item['status'], '[ ]')} {item['task']}" for item in items]
    done = sum(1 for item in items if item["status"] == "completed")
    text = "\n".join(lines)
    if len(text) > MAX_BOARD_CHARS:
        text = text[:MAX_BOARD_CHARS] + "\n… [board truncated]"
    return f"{text}\n({done}/{len(items)} completed)"


def prompt_block(identity: str) -> str:
    """Open tasks, for injection at session build. Empty string when there are none.

    This is what makes the board survive a gateway restart: a new session for the
    same identity starts knowing what was left unfinished, instead of the operator
    having to re-explain a plan the agent had already written down.
    """
    items = open_items(identity)
    if not items:
        return ""
    lines = "\n".join(f"{_SYMBOLS[item['status']]} {item['task']}" for item in items)
    if len(lines) > MAX_BOARD_CHARS:
        lines = lines[:MAX_BOARD_CHARS] + "\n… [truncated]"
    return (
        "\n\n## Unfinished tasks carried over\n"
        "These were recorded with update_task in an earlier turn of this same "
        "conversation and are NOT yet done. Do not start them unprompted; if the "
        "user says to continue, this is what they mean.\n"
        f"{lines}"
    )
