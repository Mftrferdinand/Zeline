"""Offload oversized tool output to disk instead of truncating it away.

Zeline used to cut every large tool result at a fixed character limit and
append ``... [output truncated]``. The discarded tail was gone for good: the
model could not ask for it, and neither could the user. A 200 KB log, a long
``git diff``, or a big API response all became 12 KB plus an apology.

This module keeps the context window just as small while making the loss
recoverable. Oversized text is written to a file under ``DATA_DIR`` and the
tool result is replaced by a pointer plus a head/tail preview, so the model can
decide whether the detail matters and page through it with
``read_file(path, offset=..., limit=...)``.

Design notes
------------
* The filename is ``sha256(text)[:16]``, so identical output is stored once and
  re-offloading is idempotent. No tool-call id is needed, which keeps this
  usable from any call site.
* A write failure must never produce a dangling pointer, so the caller falls
  back to plain truncation. Losing the tail is bad; lying about where it went
  is worse.
* Old entries are pruned by age and by total directory size, because a phone
  is not a data lake.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from zeline import config

#: Tool text longer than this (characters) is offloaded rather than truncated.
DEFAULT_LIMIT = 12_000

#: Lines shown from the start and the end of an offloaded payload.
PREVIEW_LINES = 5

#: Hard cap per previewed line, so one enormous line cannot flood the context.
PREVIEW_LINE_CHARS = 1_000

#: Retention for offloaded payloads.
MAX_AGE_SECONDS = 7 * 24 * 3600
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def root() -> Path:
    """Directory holding offloaded tool payloads."""
    return config.DATA_DIR / "large_tool_results"


def _ensure_root() -> Path:
    target = root()
    target.mkdir(parents=True, exist_ok=True)
    return target


def is_offload_path(path: Path) -> bool:
    """True when ``path`` points inside the offload store.

    ``read_file`` is workspace-confined; this lets it serve offload pointers
    that legitimately live outside the workspace without widening the sandbox
    to arbitrary paths.
    """
    try:
        path.resolve(strict=False).relative_to(root().resolve(strict=False))
    except (ValueError, OSError):
        return False
    return True


def prune() -> int:
    """Delete stale or excess payloads. Returns how many files were removed."""
    store = root()
    if not store.is_dir():
        return 0
    try:
        entries = sorted(
            (path for path in store.glob("*.txt") if path.is_file()),
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
        total += stat.st_size
        too_old = now - stat.st_mtime > MAX_AGE_SECONDS
        too_big = total > MAX_TOTAL_BYTES
        if too_old or too_big:
            try:
                path.unlink()
                removed += 1
                total -= stat.st_size
            except OSError:
                continue
    return removed


def _preview(text: str) -> str:
    """Head+tail excerpt of ``text`` with line numbers, bounded in size."""
    lines = text.splitlines()

    def render(index: int, line: str) -> str:
        body = line[:PREVIEW_LINE_CHARS]
        if len(line) > PREVIEW_LINE_CHARS:
            body += " ... [line truncated]"
        return f"{index:>6}| {body}"

    if len(lines) <= PREVIEW_LINES * 2:
        return "\n".join(render(i, line) for i, line in enumerate(lines, 1))
    head = [render(i, line) for i, line in enumerate(lines[:PREVIEW_LINES], 1)]
    tail_start = len(lines) - PREVIEW_LINES + 1
    tail = [render(i, line) for i, line in enumerate(lines[-PREVIEW_LINES:], tail_start)]
    hidden = len(lines) - PREVIEW_LINES * 2
    return "\n".join([*head, f"... [{hidden} lines omitted] ...", *tail])


def store(text: str) -> Path | None:
    """Write ``text`` to the offload store, returning its path or ``None``."""
    try:
        store_dir = _ensure_root()
    except OSError:
        return None
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    target = store_dir / f"{digest}.txt"
    try:
        if not target.exists():
            target.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return target


def maybe_offload(text: str, limit: int = DEFAULT_LIMIT) -> str:
    """Return ``text`` if small, else a pointer plus preview.

    Falls back to plain truncation when the payload cannot be written, so the
    result never references a file that does not exist.
    """
    if len(text) <= limit:
        return text
    target = store(text)
    if target is None:
        return text[:limit] + "\n... [output truncated; offload to disk failed]"
    prune()
    line_count = text.count("\n") + 1
    return (
        f"Output too large for context ({len(text):,} chars, {line_count:,} lines), "
        f"so the full text was saved to:\n{target}\n\n"
        "Read the parts you need with "
        f'read_file(path="{target}", offset=1, limit=200) — offset is a 1-based '
        "line number. Do not re-run the command to see the rest.\n\n"
        f"Preview:\n{_preview(text)}"
    )
