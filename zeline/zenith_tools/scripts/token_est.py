#!/usr/bin/env python3
"""
tools/token_est.py — Offline Token Estimation  (v1.0)

Agent perlu ngira-ngira "ini prompt muat gak di context window?" sebelum
kirim request. counting persis butuh tokenizer per-model (berat, sering gak
tersedia); tool ini kasih estimasi yang cukup akurat buat budgeting,
zero dependency, offline.

Strategi: pakai heuristik karakter-per-token per kelas teks (prose natural
~4 chars/token, kode lebih padat, CJK ~1.5 chars/token) — kalibrasi terhadap
behavior tokenizer BPE umum (cl100k/o200k berada dalam ±15% untuk teks
campuran normal).

API:
- estimate(text) -> int             estimasi total token
- estimate_messages(msgs) -> int    untuk array chat (termasuk overhead per pesan)
- breakdown(text) -> dict           estimasi per kelas + rincian
"""
from __future__ import annotations

import re

# Chars per token by text class, calibrated against common BPE tokenizers.
_CHARS_PER_TOKEN = {
    "prose": 4.0,     # natural language, Latin script
    "code": 3.3,      # identifiers, punctuation-heavy
    "cjk": 1.5,       # CJK ideographs are ~1 token per char
    "whitespace": 2.5,  # runs of spaces/newlines usually merge
    "numbers": 3.0,   # digit runs split roughly every 3 digits
    "other": 3.0,
}

#: Per-message overhead in chat format (role markers, delimiters).
MESSAGE_OVERHEAD = 4

_CJK_RE = re.compile(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]")
_CODE_RE = re.compile(r"[a-zA-Z_$][\w$]*\s*[({\[:=]|[{}()\[\];]|=>|::|^\s{4,}", re.MULTILINE)
_NUM_RE = re.compile(r"\d")
_WS_RE = re.compile(r"\s")


def _classify(text: str) -> dict[str, int]:
    """Count characters per class; classes partition the string."""
    counts = {k: 0 for k in _CHARS_PER_TOKEN}
    counts["cjk"] = len(_CJK_RE.findall(text))
    non_cjk = _CJK_RE.sub("", text)
    counts["whitespace"] = len(_WS_RE.findall(non_cjk))
    body = _WS_RE.sub("", non_cjk)  # letters, digits, punctuation
    counts["numbers"] = len(_NUM_RE.findall(body))
    letters_and_punct = len(body) - counts["numbers"]
    code_hits = len(_CODE_RE.findall(text))
    # Code-ish text uses a denser chars-per-token ratio for the
    # letter/punctuation bucket; otherwise it is prose.
    if body and code_hits / max(1, len(body)) > 0.08:
        counts["code"] = letters_and_punct
    else:
        counts["prose"] = letters_and_punct
    return counts


def estimate(text: str) -> int:
    """Estimate the token count of `text` (≥ 1 for non-empty input)."""
    if not text:
        return 0
    counts = _classify(text)
    total = 0.0
    for kind, chars in counts.items():
        if chars:
            total += chars / _CHARS_PER_TOKEN[kind]
    return max(1, round(total))


def estimate_messages(messages: list[dict]) -> int:
    """Estimate tokens for a chat-format message list.

    Recognizes {'role', 'content'} dicts; non-string content is JSON-dumped.
    """
    total = 0
    for msg in messages:
        total += MESSAGE_OVERHEAD
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        if not isinstance(content, str):
            import json
            content = json.dumps(content, ensure_ascii=False, default=str)
        total += estimate(content)
    return total


def breakdown(text: str) -> dict:
    """Detailed estimate: per-class counts and the projected total."""
    counts = _classify(text)
    parts = {k: round(v / _CHARS_PER_TOKEN[k]) for k, v in counts.items() if v}
    return {
        "estimated_tokens": estimate(text),
        "chars": len(text),
        "by_class_tokens": parts,
    }


def main() -> int:
    """CLI: cat prompt.txt | python3 -m zeline.zenith_tools.scripts.token_est"""
    import json
    import sys
    text = sys.stdin.read()
    if not text:
        print(__doc__)
        return 2
    print(json.dumps(breakdown(text), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
