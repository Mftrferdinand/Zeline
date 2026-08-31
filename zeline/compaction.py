"""Keep a record of conversation turns that fall out of the context window.

``ZelineAgent._trim_history`` bounds the message list by dropping whole user
turns from the front. That keeps the tool-call protocol valid, but everything in
those turns used to vanish: the agent would forget a file it had just written
and the user would have to repeat a decision they had already made.

deepagents solves this by calling an LLM to summarize the evicted messages.
That is the wrong trade on a phone — every trim would add a request, latency and
another failure mode to a path that runs before *every* turn. Instead this
module does two cheaper things that cover most of the value:

1. Append the evicted turns verbatim to an on-disk transcript, so nothing is
   actually lost and the agent can ``read_file`` the archive when it needs
   detail.
2. Build a deterministic extractive digest — what the user asked, which files
   were touched, which tools ran, and where the archive lives — and inject it
   as a single message at the front of the retained history.

No model call, no tokens, no network. The digest is bounded so it cannot itself
become the thing that overflows the window.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from zeline import config

#: Marker on the injected digest so repeated compaction never digests a digest.
DIGEST_MARKER = "[compacted-context]"

#: Bounds for the digest itself.
MAX_ASKS = 6
MAX_ASK_CHARS = 220
MAX_ARTIFACTS = 12
MAX_DIGEST_CHARS = 3_000

#: Tool calls whose arguments name a file the agent created or changed.
_ARTIFACT_TOOLS = {
    "write_file": "path",
    "edit_file": "path",
    "patch_file": "path",
    "download_file": "path",
    "generate_image": "path",
    "manage_skill": "name",
}


#: Retention for archived transcripts. A phone is not a data lake, and the
#: archive is a convenience for the *current* work, not a permanent log.
MAX_AGE_SECONDS = 14 * 24 * 3600
MAX_TOTAL_BYTES = 32 * 1024 * 1024


def archive_root() -> Path:
    """Directory holding evicted-conversation transcripts."""
    return config.DATA_DIR / "conversation_history"


def prune() -> int:
    """Delete stale or excess transcripts. Returns how many files were removed.

    Mirrors ``offload.prune``: newest first, drop anything past the age limit,
    then keep dropping until the directory fits the size budget.
    """
    store = archive_root()
    if not store.is_dir():
        return 0
    try:
        entries = sorted(
            (path for path in store.glob("*.md") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return 0
    removed = 0
    now = time.time()
    total = 0
    for path in entries:
        try:
            stat = path.stat()
        except OSError:
            continue
        too_old = now - stat.st_mtime > MAX_AGE_SECONDS
        total += stat.st_size
        if too_old or total > MAX_TOTAL_BYTES:
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
            total -= stat.st_size
    return removed


def is_digest(message: dict[str, Any]) -> bool:
    """True when ``message`` is a digest this module injected earlier."""
    return DIGEST_MARKER in str(message.get("content", ""))


def is_archive_path(path: Path) -> bool:
    """True when ``path`` points inside the transcript archive.

    Archives live under ``DATA_DIR`` by design, so ``read_file`` resolves them
    explicitly instead of widening the workspace sandbox to arbitrary paths.
    """
    try:
        path.resolve(strict=False).relative_to(archive_root().resolve(strict=False))
    except (ValueError, OSError):
        return False
    return True


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls")
    return list(calls) if isinstance(calls, list) else []


def _call_name_and_args(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict):
        return "", {}
    name = str(function.get("name") or "")
    raw = function.get("arguments")
    if isinstance(raw, dict):
        return name, raw
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return name, {}
    return name, parsed if isinstance(parsed, dict) else {}


def render(messages: list[dict[str, Any]]) -> str:
    """Readable plain-text transcript of ``messages`` for the archive."""
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "?"))
        content = str(message.get("content", "") or "")
        calls = _tool_calls(message)
        if calls:
            rendered = ", ".join(
                f"{name}({json.dumps(args, ensure_ascii=False)[:300]})"
                for name, args in (_call_name_and_args(call) for call in calls)
            )
            content = f"{content}\n[tool_calls] {rendered}".strip()
        lines.append(f"### {role}\n{content}".strip())
    return "\n\n".join(lines)


def archive(messages: list[dict[str, Any]], identity: str) -> Path | None:
    """Append ``messages`` to this identity's transcript, returning its path."""
    if not messages:
        return None
    safe_identity = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in (identity or "cli")
    )[:60] or "cli"
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    target = archive_root() / f"{safe_identity}-{stamp}.md"
    section = (
        f"\n\n## Evicted at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
        f"{render(messages)}\n"
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(section)
    except OSError:
        return None
    prune()
    return target


def digest(messages: list[dict[str, Any]], archive_path: Path | None) -> str:
    """Extractive summary of evicted ``messages``. Deterministic, no LLM."""
    asks: list[str] = []
    artifacts: list[str] = []
    tool_counts: Counter[str] = Counter()
    for message in messages:
        role = str(message.get("role", ""))
        content = str(message.get("content", "") or "").strip()
        if role == "user" and content and not is_digest(message):
            collapsed = " ".join(content.split())
            if len(collapsed) > MAX_ASK_CHARS:
                collapsed = collapsed[:MAX_ASK_CHARS] + "…"
            asks.append(collapsed)
        for call in _tool_calls(message):
            name, args = _call_name_and_args(call)
            if not name:
                continue
            tool_counts[name] += 1
            key = _ARTIFACT_TOOLS.get(name)
            if key:
                value = str(args.get(key) or "").strip()
                if value and value not in artifacts:
                    artifacts.append(value)

    parts = [
        f"{DIGEST_MARKER} Earlier turns were removed from this context to stay "
        "inside the window. This is what happened in them."
    ]
    if asks:
        shown = asks[-MAX_ASKS:]
        dropped = len(asks) - len(shown)
        heading = "## Earlier requests"
        if dropped > 0:
            heading += f" (latest {len(shown)} of {len(asks)})"
        parts.append(heading + "\n" + "\n".join(f"- {ask}" for ask in shown))
    if artifacts:
        shown_artifacts = artifacts[:MAX_ARTIFACTS]
        heading = "## Files written or changed"
        if len(artifacts) > len(shown_artifacts):
            heading += f" (first {len(shown_artifacts)} of {len(artifacts)})"
        parts.append(heading + "\n" + "\n".join(f"- {item}" for item in shown_artifacts))
    if tool_counts:
        summary = ", ".join(
            f"{name}×{count}" for name, count in tool_counts.most_common(8)
        )
        parts.append(f"## Tools used\n{summary}")
    if archive_path is not None:
        parts.append(
            "## Full transcript\n"
            f'read_file(path="{archive_path}", offset=1, limit=200) has the '
            "complete text of the removed turns. Read it before asking the user "
            "to repeat something."
        )
    text = "\n\n".join(parts)
    if len(text) > MAX_DIGEST_CHARS:
        text = text[:MAX_DIGEST_CHARS] + "\n… [digest truncated]"
    return text


def compact(
    dropped: list[dict[str, Any]],
    identity: str,
) -> dict[str, Any] | None:
    """Archive ``dropped`` and return the digest message to inject, if any.

    Returns ``None`` when there is nothing worth recording, so the caller keeps
    its previous plain-drop behavior.
    """
    meaningful = [
        message
        for message in dropped
        if not is_digest(message)
        and (str(message.get("content", "") or "").strip() or _tool_calls(message))
    ]
    if not meaningful:
        return None
    path = archive(meaningful, identity)
    return {"role": "user", "content": digest(meaningful, path)}
