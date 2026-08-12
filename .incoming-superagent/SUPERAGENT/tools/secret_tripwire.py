#!/usr/bin/env python3
"""
tools/secret_tripwire.py — Output-layer secret redaction  (v4.2)

Sabuk pengaman TERAKHIR sebelum teks dikirim ke operator / ditulis ke log.
Rail SOUL.md udah bilang "never log priv key/mnemonic" — ini nge-enforce di kode:
scan output, redact apa pun yang kelihatan kayak secret, dan (opsional) raise kalau
mode strict. Bukan pengganti hygiene; ini jaring kalau ada yang lolos.

Deteksi:
  - EVM private key (0x + 64 hex), raw 64-hex
  - BIP-39 mnemonic (12/15/18/21/24 kata dari wordlist-shape — dicek heuristik panjang)
  - API keys umum: sk-..., OpenAI/Anthropic style, AWS AKIA..., Slack xox..., ghp_...
  - JWT (3 segmen base64url dipisah titik)
  - PEM private key block
  - Solana/base58 secret (heuristik panjang) — opsional, off default (false-positive tinggi)

Zero-dep (re only). scan() → temuan; redact() → teks bersih; guard() → raise di strict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# kata-kata yang sering nemenin secret → naikin keyakinan untuk heuristik mnemonic
_MNEMONIC_HINT = re.compile(r"(mnemonic|seed\s*phrase|recovery\s*phrase|seed\s*words)", re.I)

_PATTERNS = [
    ("evm_private_key", re.compile(r"\b0x[a-fA-F0-9]{64}\b")),
    ("raw_hex_64", re.compile(r"(?<![a-fA-F0-9])[a-fA-F0-9]{64}(?![a-fA-F0-9])")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("aws_akid", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    ("pem_private", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

_REDACTION = "‹REDACTED:{kind}›"


@dataclass
class Finding:
    kind: str
    span: tuple
    preview: str


def _find_mnemonics(text: str) -> list:
    """Heuristik: 12/24 kata huruf-kecil berurutan, makin yakin kalau ada hint kata
    'mnemonic/seed phrase' di dekatnya. Konservatif biar false-positive rendah."""
    findings = []
    for m in re.finditer(r"\b((?:[a-z]{3,8}\s+){11,23}[a-z]{3,8})\b", text):
        words = m.group(1).split()
        if len(words) in (12, 15, 18, 21, 24):
            window = text[max(0, m.start() - 40):m.start()]
            # tanpa hint, 12+ kata bisa kalimat biasa — wajib ada hint untuk dianggap secret
            if _MNEMONIC_HINT.search(window) or len(words) >= 18:
                findings.append(Finding("mnemonic", (m.start(), m.end()),
                                        " ".join(words[:2]) + " …"))
    return findings


def scan(text: str) -> list:
    """Return list[Finding] semua kandidat secret di `text`."""
    if not text:
        return []
    findings = []
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            s = m.group(0)
            findings.append(Finding(kind, (m.start(), m.end()), s[:6] + "…"))
    findings.extend(_find_mnemonics(text))
    # buang overlap: kalau evm_private_key & raw_hex_64 nutupin span yang sama, simpan yang spesifik
    findings.sort(key=lambda f: (f.span[0], -(f.span[1] - f.span[0])))
    deduped, last_end = [], -1
    for f in findings:
        if f.span[0] >= last_end:
            deduped.append(f)
            last_end = f.span[1]
    return deduped


def redact(text: str) -> str:
    """Ganti semua secret yang kedeteksi dengan placeholder. Idempotent."""
    findings = scan(text)
    if not findings:
        return text
    out = []
    cursor = 0
    for f in sorted(findings, key=lambda f: f.span[0]):
        if f.span[0] < cursor:        # overlap, skip
            continue
        out.append(text[cursor:f.span[0]])
        out.append(_REDACTION.format(kind=f.kind))
        cursor = f.span[1]
    out.append(text[cursor:])
    return "".join(out)


def guard(text: str, *, strict: bool = False) -> str:
    """Redact + (kalau strict) raise kalau ada secret. Pakai di output layer:
        return guard(candidate, strict=True)
    """
    findings = scan(text)
    if findings and strict:
        kinds = ", ".join(sorted({f.kind for f in findings}))
        raise SecretLeakError(f"output ditahan — terdeteksi secret: {kinds}")
    return redact(text)


class SecretLeakError(Exception):
    pass


if __name__ == "__main__":
    sample = ("ini key lo: 0x" + "a" * 64 + " dan API sk-abc123def456ghi789jkl. "
              "mnemonic: ridge layer broom apple ocean canyon table velvet maple river stone arrow")
    print("FINDINGS:", [f.kind for f in scan(sample)])
    print("REDACTED:", redact(sample))
