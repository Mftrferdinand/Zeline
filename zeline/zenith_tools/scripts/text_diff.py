#!/usr/bin/env python3
"""
tools/text_diff.py — Unified Diff / Text Comparison  (v1.0)

Ringkas perubahan dua teks: unified diff, statistik (+/- per baris), dan
ringkasan per-hunk. dipakai agent buat ngecek hasil edit file sebelum
commit, atau ngebandingin dua versi config — tanpa harus manggil `diff`
subprocess (yang gak selalu ada, apalagi di Windows/Termux).

Zero dependency (difflib, json). Pure functions, gampang di-tes.

API:
- unified_diff(a, b, label_a, label_b, context) -> str
- diff_stats(a, b) -> dict (added, removed, unchanged, similarity)
- summarize_diff(a, b, max_hunks) -> list[str] ringkasan per hunk
"""
from __future__ import annotations

import difflib
import json
import re
from typing import Optional

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def _lines(text: str) -> list[str]:
    return text.splitlines()


def unified_diff(a: str, b: str,
                 label_a: str = "a", label_b: str = "b",
                 context: int = 3) -> str:
    """Unified diff string; '' when the texts are identical."""
    if a == b:
        return ""
    return "\n".join(difflib.unified_diff(
        _lines(a), _lines(b),
        fromfile=label_a, tofile=label_b, lineterm="", n=context,
    ))


def diff_stats(a: str, b: str) -> dict:
    """Line-level change statistics between two texts."""
    la, lb = _lines(a), _lines(b)
    sm = difflib.SequenceMatcher(a=la, b=lb, autojunk=False)
    added = removed = unchanged = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            unchanged += i2 - i1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "insert":
            added += j2 - j1
        elif tag == "replace":
            removed += i2 - i1
            added += j2 - j1
    total = len(la) + len(lb)
    similarity = (2 * unchanged / total) if total else 1.0
    return {
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
        "similarity": round(similarity, 4),
    }


def summarize_diff(a: str, b: str, max_hunks: int = 10,
                   max_lines_per_hunk: int = 3) -> Optional[str]:
    """Human-readable summary: one line per changed hunk, None if identical."""
    diff = unified_diff(a, b, context=0)
    if not diff:
        return None
    hunks: list[dict] = []
    current: Optional[dict] = None
    for line in diff.splitlines():
        if _HUNK_RE.match(line):
            current = {"add": 0, "del": 0, "sample": []}
            hunks.append(current)
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            current["add"] += 1
            if len(current["sample"]) < max_lines_per_hunk:
                current["sample"].append(line[1:].strip()[:120])
        elif current is not None and line.startswith("-") and not line.startswith("---"):
            current["del"] += 1
    if not hunks:
        return None
    parts = []
    for i, h in enumerate(hunks[:max_hunks], 1):
        part = f"hunk {i}: +{h['add']} -{h['del']}"
        if h["sample"]:
            part += f" | e.g. {h['sample'][0]}"
        parts.append(part)
    if len(hunks) > max_hunks:
        parts.append(f"... and {len(hunks) - max_hunks} more hunk(s)")
    return "\n".join(parts)


def main() -> int:
    """CLI: python3 -m zeline.zenith_tools.scripts.text_diff file_a file_b"""
    import sys
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    try:
        a = Path_read(sys.argv[1])
        b = Path_read(sys.argv[2])
    except OSError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(json.dumps(diff_stats(a, b), indent=2))
    summary = summarize_diff(a, b)
    if summary:
        print("\n" + summary)
    return 0


def Path_read(path: str) -> str:
    from pathlib import Path
    return Path(path).read_text(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
