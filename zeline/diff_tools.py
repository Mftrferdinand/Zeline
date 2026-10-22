"""Unified diff between two workspace files or two inline texts.

Before this, comparing a generated file against its source of truth needed
``run_shell diff`` — owner-only — or the model eyeballing two read_file pages.
``diff_text`` gives every profile a bounded, unified diff so a change is
evidence, not a paraphrase.

Scope: read-only, workspace-confident, bounded (inputs over 2 MB refused,
output offloaded like every other tool result).
"""
from __future__ import annotations

import difflib
from pathlib import Path

MAX_INPUT_BYTES = 2_000_000
CONTEXT_LINES = 3


def _read(path: str, workspace: Path) -> str:
    from zeline.tools import _resolve_workspace_path

    target = _resolve_workspace_path(path, workspace)
    if not target.is_file():
        raise ValueError(f"not a file or not found: {target}")
    if target.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"file exceeds the {MAX_INPUT_BYTES // 1_000_000} MB limit")
    return target.read_text(encoding="utf-8")


def _diff_text(
    path_a: str = "",
    path_b: str = "",
    workspace: Path | None = None,
    text_a: str = "",
    text_b: str = "",
    label_a: str = "a",
    label_b: str = "b",
) -> str:
    """Unified diff of two files and/or two inline texts."""
    from zeline.offload import maybe_offload

    if workspace is None:
        from zeline import config

        workspace = Path(config.WORKSPACE)
    try:
        left = _read(path_a, workspace) if path_a else text_a
        right = _read(path_b, workspace) if path_b else text_b
    except (ValueError, OSError, UnicodeError) as exc:
        return f"ERROR diff_text: {exc}"
    if path_a and not text_a:
        label_a = path_a
    if path_b and not text_b:
        label_b = path_b
    if not left and not right:
        return "ERROR diff_text: give two paths or two texts."
    diff = list(difflib.unified_diff(
        left.splitlines(keepends=True),
        right.splitlines(keepends=True),
        fromfile=str(label_a),
        tofile=str(label_b),
        n=CONTEXT_LINES,
    ))
    if not diff:
        return "IDENTICAL: no differences."
    rendered = "".join(diff)
    return maybe_offload(rendered, 12_000)
