#!/usr/bin/env python3
"""
flag_validator.py — Validate an extracted flag against the challenge format.

A flag is only accepted if:
  1. It matches the challenge-specific regex (or a known common format), AND
  2. It was sourced from the target (the caller asserts source != "invented").

This guards against the most common autonomous-agent failure: hallucinating a
plausible-looking flag to "finish" a challenge.

Usage:
    python3 flag_validator.py 'flag{abc_123}' 'flag\\{.*\\}'
    -> [+] VALID

    from flag_validator import validate
    ok = validate(candidate, fmt_regex)
"""
from __future__ import annotations

import re
import sys

# Common formats seen across platforms. The challenge-specific regex (if given)
# always takes priority over these.
COMMON_FORMATS = [
    r"flag\{[^}]+\}",
    r"CTF\{[^}]+\}",
    r"[A-Za-z0-9_]+\{[^}]+\}",      # generic prefix{...}
    r"picoCTF\{[^}]+\}",
    r"HTB\{[^}]+\}",
]

# Reject obvious placeholders the model might emit.
PLACEHOLDER_BLOCKLIST = {
    "flag{...}", "flag{your_flag_here}", "flag{example}",
    "flag{redacted}", "ctf{example}", "flag{}", "flag{flag}",
}


def validate(candidate: str, fmt_regex: str | None = None) -> bool:
    if not candidate:
        return False
    cand = candidate.strip()

    if cand.lower() in PLACEHOLDER_BLOCKLIST:
        return False

    # Strict: the WHOLE candidate must be a flag (fullmatch). This blocks
    # accepting junk-wrapped strings like "see flag{x} here" as a flag —
    # extract_all() is the right entry point for scraping flags out of blobs.
    patterns = [fmt_regex] if fmt_regex else COMMON_FORMATS
    for pat in patterns:
        try:
            if re.fullmatch(pat, cand):
                return True
        except re.error:
            continue
    return False


def extract_all(blob: str, fmt_regex: str | None = None) -> list[str]:
    """Pull every flag-shaped substring out of a text blob (e.g. exploit output)."""
    patterns = [fmt_regex] if fmt_regex else COMMON_FORMATS
    found: list[str] = []
    for pat in patterns:
        try:
            found.extend(m.group(0) for m in re.finditer(pat, blob))
        except re.error:
            continue
    # dedupe, keep order
    seen: set[str] = set()
    out: list[str] = []
    for f in found:
        if f not in seen and f.lower() not in PLACEHOLDER_BLOCKLIST:
            seen.add(f)
            out.append(f)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: flag_validator.py <candidate> [fmt_regex]")
        sys.exit(2)
    fmt = sys.argv[2] if len(sys.argv) > 2 else None
    if validate(sys.argv[1], fmt):
        print("[+] VALID")
        sys.exit(0)
    print("[-] INVALID (bad format or placeholder)")
    sys.exit(1)
