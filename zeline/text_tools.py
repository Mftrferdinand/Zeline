"""Small pure utilities: text statistics, timezone conversion, regex testing.

These fill three gaps that forced an agent into run_shell for trivial work:

- ``text_stats`` — character/word/line counts, estimated token cost (chars/4
  heuristic, clearly labelled as an estimate), and the top terms. Read-only.
- ``time_convert`` — render one timestamp across IANA timezones; answering
  "when is 1700000000 in Jakarta" previously needed shell or a web call.
- ``regex_test`` — apply a pattern to a text with a bounded match list, so a
  pattern can be proven against evidence before it is used in code.

All three are read-only, dependency-free, and available on every profile.
"""
from __future__ import annotations

import collections
import json
import re
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_TEXT_CHARS = 500_000
MAX_REGEX_TEXT = 200_000
MAX_MATCHES = 50


def _text_stats(text: str, top: int = 10) -> str:
    text = str(text or "")
    if len(text) > MAX_TEXT_CHARS:
        return f"ERROR text_stats: text exceeds the {MAX_TEXT_CHARS // 1000}k character limit."
    words = re.findall(r"[\w']+", text.lower())
    lines = text.count("\n")
    if text and not text.endswith("\n"):
        lines += 1
    result: dict[str, Any] = {
        "characters": len(text),
        "characters_no_spaces": len(text.replace(" ", "").replace("\t", "")),
        "words": len(words),
        "lines": lines,
        "estimated_tokens": round(len(text) / 4),
    }
    if top > 0:
        stopwords = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "it",
                     "for", "on", "with", "as", "at", "by", "be", "this", "that"}
        common = [(word, count) for word, count in collections.Counter(words).most_common(top * 2)
                  if word not in stopwords and len(word) > 2][:max(0, min(top, 50))]
        result["top_terms"] = [{"term": word, "count": count} for word, count in common]
    return json.dumps(result, ensure_ascii=False)


def _time_convert(timestamp: float = 0.0, timezones: str = "UTC,Asia/Jakarta", text_time: str = "") -> str:
    """Render one moment across several IANA timezones."""
    zones = [name.strip() for name in str(timezones or "UTC").split(",") if name.strip()][:8] or ["UTC"]
    if text_time.strip():
        raw = text_time.strip()
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return ("ERROR time_convert: text_time must look like '2026-10-23 09:30' "
                    "or '2026-10-23T09:30:00'.")
        # No explicit zone in the accepted formats: treat the wall clock as UTC
        # so the same input renders identically on any host. Local-time input
        # can be given as a Unix timestamp computed by the caller.
        from datetime import timezone as _tz

        moment = parsed.replace(tzinfo=_tz.utc).timestamp()
    else:
        try:
            moment = float(timestamp)
        except (TypeError, ValueError):
            return "ERROR time_convert: timestamp must be a number (Unix seconds), or pass text_time."
        if moment <= 0:
            moment = time.time()
    lines = [f"Unix: {moment:.0f}"]
    for name in zones:
        try:
            zone = ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            lines.append(f"  {name}: unknown timezone (IANA name required, e.g. Asia/Jakarta)")
            continue
        stamp = datetime.fromtimestamp(moment, zone).strftime("%Y-%m-%d %H:%M:%S %Z (%z)")
        lines.append(f"  {name}: {stamp}")
    return "\n".join(lines)


def _regex_test(pattern: str, text: str, flags: str = "") -> str:
    """Apply a regex to a text and list the bounded matches."""
    if not str(pattern or "").strip():
        return "ERROR regex_test: pattern is required."
    if len(text) > MAX_REGEX_TEXT:
        return f"ERROR regex_test: text exceeds the {MAX_REGEX_TEXT // 1000}k character limit."
    try:
        if len(pattern) > 1000:
            return "ERROR regex_test: pattern exceeds 1000 characters."
        compiled = re.compile(pattern)
    except re.error as exc:
        return f"ERROR regex_test: invalid pattern: {exc}"
    if pattern.startswith(("(", "(?P<")) and not flags.strip():
        compiled = re.compile(pattern)
    matched = list(compiled.finditer(str(text)))[:MAX_MATCHES]
    if not matched:
        return f"NO MATCHES for {pattern!r}."
    lines = [f"{len(matched)} match(es) (max {MAX_MATCHES} shown):"]
    for index, match in enumerate(matched, 1):
        span = f"chars {match.start()}-{match.end()}"
        groups = [g for g in match.groups() if g is not None]
        if match.groupdict():
            named = {k: v for k, v in match.groupdict().items() if v is not None}
            lines.append(f"  {index}. {span}: {match.group(0)!r} groups={named}")
        elif groups:
            lines.append(f"  {index}. {span}: {match.group(0)!r} groups={groups}")
        else:
            lines.append(f"  {index}. {span}: {match.group(0)!r}")
    if len(list(compiled.finditer(str(text)))) > MAX_MATCHES:
        lines.append(f"  ... more matches omitted (limit {MAX_MATCHES})")
    return "\n".join(lines)
