#!/usr/bin/env python3
"""
tools/secret_redactor.py — Detect & Redact Secrets in Text  (v1.0)

Agent sering motong output (read_file, curl, env) yang diam-diam isinya
API key / token, terus output itu terkirim ke gateway atau disimpen di
session db. Tool ini deteksi pola secret umum, redaksi, dan kasih laporan
berapa yang kena — keyless, pure regex, tanpa dependency.

Zeline sudah punya secret_tripwire (audit file); yang ini buat teks
runtime / tool output, bisa dipanggil dari plugin before-hook.

API:
- find_secrets(text) -> list[SecretHit]
- redact(text, replacement) -> (clean_text, hits)
- report(text) -> dict ringkas untuk agent
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from typing import Optional


@dataclass
class SecretHit:
    kind: str
    start: int
    end: int
    preview: str = field(repr=False, default="")  # masked, never the secret

    def masked(self) -> str:
        return f"<{self.kind}:{self.start}-{self.end}>"


# Each pattern: (kind, compiled regex). Only high-confidence token formats
# with provider-specific prefixes/lengths, to keep false positives low.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("github_fine_grained", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("openai", re.compile(r"sk-(?:proj-)?[A-Za-z0-9\-_]{20,}")),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("slack_bot", re.compile(r"xoxb-[A-Za-z0-9\-]{10,}")),
    ("slack_user", re.compile(r"xoxp-[A-Za-z0-9\-]{10,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("telegram_bot", re.compile(r"\b[0-9]{8,10}:AA[A-Za-z0-9\-_]{33}\b")),
    ("private_key_block", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("generic_bearer", re.compile(
        r"(?i)\bbearer\s+[A-Za-z0-9\-_.~+/]{32,}={0,2}")),
    ("password_kv", re.compile(
        r"(?i)\b(passw(or)?d|api[_\-]?key|secret|token)\b\s*[:=]\s*"
        r"['\"]?[A-Za-z0-9\-_.+/]{16,}['\"]?")),
]


def find_secrets(text: str) -> list[SecretHit]:
    """All high-confidence secret matches in `text`, in document order.

    Provider-specific patterns win over the generic bearer/password_kv
    patterns when spans overlap, so `Bearer ghp_...` is reported as a
    GitHub token, not a generic bearer string.
    """
    hits: list[SecretHit] = []
    taken: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(not (end <= s or start >= e) for s, e in taken)

    def take(kind: str, pattern: re.Pattern[str]) -> None:
        for match in pattern.finditer(text):
            if overlaps(match.start(), match.end()):
                continue
            hits.append(SecretHit(kind, match.start(), match.end()))
            taken.append((match.start(), match.end()))

    specific = [(k, p) for k, p in _PATTERNS if k not in ("generic_bearer", "password_kv")]
    generic = [(k, p) for k, p in _PATTERNS if k in ("generic_bearer", "password_kv")]
    for kind, pattern in [*specific, *generic]:
        take(kind, pattern)
    hits.sort(key=lambda h: h.start)
    return hits


def redact(text: str, replacement: str = "[REDACTED]") -> tuple[str, list[SecretHit]]:
    """Return (text with secrets replaced, hits)."""
    hits = find_secrets(text)
    if not hits:
        return text, []
    out: list[str] = []
    cursor = 0
    for hit in hits:
        out.append(text[cursor:hit.start])
        out.append(f"{replacement}({hit.kind})")
        cursor = hit.end
    out.append(text[cursor:])
    return "".join(out), hits


def report(text: str) -> dict:
    """Compact summary for the agent: counts per kind + redacted preview."""
    clean, hits = redact(text)
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.kind] = counts.get(hit.kind, 0) + 1
    preview = clean[:400] + ("..." if len(clean) > 400 else "")
    return {
        "secrets_found": len(hits),
        "by_kind": counts,
        "redacted": clean != text,
        "preview": preview,
    }


def main() -> int:
    """CLI: cat file | python3 -m zeline.zenith_tools.scripts.secret_redactor"""
    import json
    import sys
    text = sys.stdin.read()
    if not text:
        print(__doc__)
        return 2
    print(json.dumps(report(text), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
