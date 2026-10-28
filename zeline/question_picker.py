"""Recognize explicit branching questions omitted from ask_user tool calls.

Only final decision questions with contiguous choices qualify. Informational lists
and incidental questions must remain regular replies.
"""
from __future__ import annotations

import re

_OPTION = re.compile(r"^([1-6A-Fa-f])[.):]\s+(.+?)\s*$")
_BULLET = re.compile(r"^[-*•]\s+(.+?)\s*$")
_DECISION = re.compile(r"\b(?:pilih|pilihan|opsi|mana|mau|choose|which|prefer)\b", re.IGNORECASE)
_FREEFORM = re.compile(r"^(?:atau|or)\s+(?:ketik|tulis|type|write)\b", re.IGNORECASE)
_BINARY = re.compile(
    r"^(?:Pilih|Mau|Choose|Would you prefer)\s+(.{1,150}?)\s+(?:atau|or)\s+(.{1,150}?)\?$",
    re.IGNORECASE,
)


def _choice(line: str) -> tuple[str | None, str] | None:
    """Read numbered, lettered, or bulleted option; remove markup only."""
    item = line.strip().replace("**", "").replace("`", "")
    bullet = _BULLET.fullmatch(item)
    if bullet:
        item = bullet.group(1)
    labelled = _OPTION.fullmatch(item)
    if labelled:
        return labelled.group(1).upper(), labelled.group(2).strip()
    if bullet:
        return None, item.strip()
    return None


def _choices(lines: list[str], question: str) -> tuple[str, ...] | None:
    if not 2 <= len(lines) <= 6:
        return None
    found = [_choice(item) for item in lines]
    if any(item is None or not item[1] for item in found):
        return None
    labels = [item[0] for item in found if item is not None]
    if any(label is None for label in labels):
        if any(label is not None for label in labels) or not _DECISION.search(question):
            return None
    elif labels != [str(i) for i in range(1, len(lines) + 1)] and labels != [chr(65 + i) for i in range(len(lines))]:
        return None
    options = tuple(item[1] for item in found if item is not None)
    return options if len({option.casefold() for option in options}) == len(options) else None


def branching_question(content: str) -> tuple[str, tuple[str, ...]] | None:
    """Return (question, options) only for an explicit final decision request."""
    text = str(content or "").strip()
    if not text or len(text) > 6_000:
        return None
    raw_lines = text.splitlines()
    lines = [line.strip() for line in raw_lines if line.strip()]
    for index, raw_line in enumerate(raw_lines):
        line = raw_line.strip().rstrip("* \t")
        if not line.endswith("?"):
            continue
        start = index
        while start > 0:
            preceding = raw_lines[start - 1].strip()
            if not preceding or preceding.endswith((".", "?", ":")) or _choice(preceding):
                break
            start -= 1
        question = " ".join(raw_lines[pos].strip() for pos in range(start, index + 1))
        question = question.replace("**", "").removeprefix("❓").strip()
        if len(question) > 500:
            continue
        following = [item.strip() for item in raw_lines[index + 1:] if item.strip()]
        if following and following[0].lower().rstrip(":") in {"pilihan", "options", "opsi"}:
            following = following[1:]
        if following and _FREEFORM.match(following[-1]):
            following = following[:-1]
        options = _choices(following, question)
        if options:
            return question, options
        # Some models enumerate first and put "Mau pilih yang mana?" last.
        if index == next((i for i in range(len(raw_lines) - 1, -1, -1) if raw_lines[i].strip()), -1) and _DECISION.search(question):
            before = [item.strip() for item in raw_lines[:index] if item.strip()]
            preceding_choices: list[str] = []
            for item in reversed(before):
                if _choice(item) is None:
                    break
                preceding_choices.append(item)
            options = _choices(list(reversed(preceding_choices)), question)
            if options:
                return question, options
    if len(lines) == 1:
        match = _BINARY.fullmatch(text)
        if match:
            binary_options = (match.group(1).strip(), match.group(2).strip())
            if len({x.casefold() for x in binary_options}) == 2:
                return text, binary_options
    return None
