#!/usr/bin/env python3
"""Scrub identitas pribadi dari skill sebelum publik.

Mengganti nama/handle/project/chat-id milik operator dengan placeholder generik
supaya skill aman dipakai orang lain. TIDAK menyentuh token/API key (itu sudah
dicek terpisah dan tidak boleh ada di skill sama sekali).

Idempotent & case-insensitive untuk nama. Jalankan atas ~/.zeline/skills/public.
"""
from __future__ import annotations

import os
import re
import sys

TARGET_DIR = os.path.expanduser("~/.zeline/skills/public")

# (regex, pengganti). Urut dari spesifik → umum. Case-insensitive kecuali dinyatakan.
SCRUBS = [
    (r"(?i)\bmftrferdinand@gmail\.com\b", "user@example.com"),
    (r"(?i)\bmahesa f\.? ferdinand\b", "the user"),
    # compound / handle forms (spesifik dulu, sebelum kata dasar)
    (r"(?i)MftrferdinandDocs", "UserDocs"),
    (r"(?i)@?kedaicloudcsbot", "@mystore_cs_bot"),
    (r"(?i)@?kedaicloudbot", "@mystore_bot"),
    (r"(?i)MyStore-backend", "store-backend"),
    (r"(?i)@?web3addicter_bot", "@community_bot"),
    (r"(?i)Web3addicterSite", "CommunitySite"),
    (r"(?i)\bkd-fresh\b", "store-frontend"),
    (r"(?i)\bdompetin\b", "walletapp"),
    (r"(?i)\baequitas\b", "SampleApp"),
    (r"(?i)\bmftrferdinand\b", "user"),
    (r"(?i)\bmahesa\b", "the user"),
    (r"(?i)\bkedaicloud\b", "MyStore"),
    (r"(?i)\bweb3\s?addicter\b", "the community"),
    (r"(?i)\btwenty3ph?\b", "SampleProject"),
    (r"(?i)\bzeline-guide\b", "docs-site"),
    (r"\b7387183839\b", "<OWNER_CHAT_ID>"),
    # 'aes' sebagai kata utuh (bukan bagian kata spt 'phases'): ganti ke 'the user'
    (r"(?i)(?<![A-Za-z])aes(?![A-Za-z])", "the user"),
]


def scrub_text(text: str) -> tuple[str, int]:
    total = 0
    for pat, repl in SCRUBS:
        text, n = re.subn(pat, repl, text)
        total += n
    return text, total


def main() -> int:
    files = sys.argv[1:] or [
        os.path.join(TARGET_DIR, f) for f in os.listdir(TARGET_DIR) if f.endswith(".md")
    ]
    touched = 0
    for path in files:
        if not os.path.isfile(path):
            continue
        original = open(path, encoding="utf-8", errors="replace").read()
        scrubbed, n = scrub_text(original)
        if n and scrubbed != original:
            open(path, "w", encoding="utf-8").write(scrubbed)
            os.chmod(path, 0o600)
            touched += 1
            print(f"  {n:4} scrub  {os.path.basename(path)}")
    print(f"\nFile disentuh: {touched}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
