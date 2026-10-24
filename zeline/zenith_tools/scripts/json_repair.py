#!/usr/bin/env python3
"""
tools/json_repair.py — Lenient JSON Parser for LLM Output  (v1.0)

Model sering keluarin JSON yang "hampir" bener: dibungkus ```json fence,
pakai single quote, trailing comma, key tanpa quote, atau ada prose sebelum/
sesudahnya. json.loads() langsung nolak — dan agent gak bisa lanjut.

Tool ini nutup gap itu: coba json.loads() dulu (fast path), kalau gagal
bersihin sumbernya bertahap sampai valid. Zero dependency, pure functions.

API:
- loads(text) -> Any  (raises ValueError kalau benar2 gak bisa diselamatkan)
- extract_json(text) -> str potongan JSON kandidat dari teks campuran
- repair(text) -> dict {"ok": bool, "value": Any, "repairs": list[str]}
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_BRACE_RE = re.compile(r"[{\[]")


def extract_json(text: str) -> str:
    """Pull the most plausible JSON fragment out of mixed prose.

    Order: fenced block > first balanced object/array > whole text.
    """
    fence = _FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        return stripped
    # Find the first { or [ and take the balanced region from it.
    match = _BRACE_RE.search(stripped)
    if match:
        open_ch = match.group(0)
        close_ch = "}" if open_ch == "{" else "]"
        depth = 0
        in_str = False
        escape = False
        for i in range(match.start(), len(stripped)):
            ch = stripped[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return stripped[match.start():i + 1]
        # Unbalanced: take from the first bracket to the end.
        return stripped[match.start():]
    return stripped


def _strip_comments(s: str) -> str:
    s = re.sub(r"//[^\n\r]*", "", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    return s


def _quote_single_quotes(s: str) -> str:
    """Replace '…' string delimiters with \"…\" (naive but effective for
    model output, which rarely contains tricky nesting in single quotes)."""
    out = []
    in_double = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == '"':
            in_double = not in_double
            out.append(ch)
            i += 1
        elif ch == "'" and not in_double:
            # Find the closing quote and re-quote the content.
            end = s.find("'", i + 1)
            if end == -1:
                out.append(ch)
                i += 1
                continue
            content = s[i + 1:end].replace('"', '\\"')
            out.append(f'"{content}"')
            i = end + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _fix_trailing_commas(s: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", s)


def _quote_bare_keys(s: str) -> str:
    return re.sub(
        r'([{,]\s*)([A-Za-z_][A-Za-z0-9_\-]*)(\s*:)',
        r'\1"\2"\3',
        s,
    )


def _close_brackets(s: str) -> str:
    """Append the missing closing brackets for truncated output."""
    stack = []
    in_str = False
    escape = False
    for ch in s:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
    if in_str:
        s += '"'
    return s + "".join(reversed(stack))


def _attempt_repairs(s: str) -> list[tuple[str, str]]:
    """Ordered (label, transformed) repair candidates."""
    candidates: list[tuple[str, str]] = []
    base = s.strip()
    if base != s:
        candidates.append(("strip", base))
    if "```" in base:
        candidates.append(("fence", extract_json(base)))
    if "//" in base or "/*" in base:
        candidates.append(("comments", _strip_comments(base)))
    if "'" in base:
        candidates.append(("single_quotes", _quote_single_quotes(base)))
    if re.search(r",\s*[}\]]", base):
        candidates.append(("trailing_commas", _fix_trailing_commas(base)))
    if re.search(r"[{,]\s*[A-Za-z_][A-Za-z0-9_\-]*\s*:", base):
        candidates.append(("bare_keys", _quote_bare_keys(base)))
    # Combined: the usual model output needs several at once.
    combined = _fix_trailing_commas(_quote_bare_keys(
        _quote_single_quotes(_strip_comments(base))))
    candidates.append(("combined", combined))
    return candidates


def loads(text: str) -> Any:
    """Parse JSON, applying progressive repairs. Raises ValueError on total loss."""
    s = text.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    candidate = extract_json(s)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    last_error: Exception | None = None
    for _label, transformed in _attempt_repairs(candidate):
        try:
            return json.loads(transformed)
        except json.JSONDecodeError as exc:
            last_error = exc
    # Last resort: close truncated brackets on the combined form.
    try:
        return json.loads(_close_brackets(candidate))
    except json.JSONDecodeError as exc:
        last_error = exc
    try:
        return json.loads(_close_brackets(candidate) and
                          _fix_trailing_commas(_close_brackets(candidate)))
    except json.JSONDecodeError as exc:
        raise ValueError(f"json_repair: unrepairable JSON: {exc}") from last_error


def repair(text: str) -> dict:
    """Structured result for agent consumption."""
    try:
        return {"ok": True, "value": loads(text), "repairs": []}
    except ValueError as exc:
        # Re-derive which repairs helped for the success path.
        return {"ok": False, "value": None, "error": str(exc)}


def repair_detailed(text: str) -> dict:
    """Like repair() but records which repairs were applied."""
    repairs: list[str] = []
    s = text.strip()
    try:
        return {"ok": True, "value": json.loads(s), "repairs": repairs}
    except json.JSONDecodeError:
        pass
    candidate = extract_json(s)
    if candidate != s:
        repairs.append("extract")
    for label, transformed in _attempt_repairs(candidate):
        try:
            if label not in repairs:
                repairs.append(label)
            return {"ok": True, "value": json.loads(transformed), "repairs": repairs}
        except json.JSONDecodeError:
            continue
    try:
        value = json.loads(_close_brackets(candidate))
        repairs.append("close_brackets")
        return {"ok": True, "value": value, "repairs": repairs}
    except json.JSONDecodeError as exc:
        return {"ok": False, "value": None, "error": str(exc), "repairs": repairs}


def main() -> int:
    """CLI: cat broken.json | python3 -m zeline.zenith_tools.scripts.json_repair"""
    import sys
    text = sys.stdin.read()
    if not text:
        print(__doc__)
        return 2
    import pprint
    result = repair_detailed(text)
    pprint.pprint(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
