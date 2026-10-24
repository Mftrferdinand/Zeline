#!/usr/bin/env python3
"""
tools/url_extract.py — Extract, Validate & Normalize URLs from Text  (v1.0)

Agent sering nerima teks bebas — chat user, halaman web, output tool — dan
perlu ngambil semua URL dari situ, filter yang beneran valid, hapus tracking
param, dan normalisasi. Inspired by URLExtract, tapi zero dependency dan
murni stdlib (urllib.parse + re).

API:
- extract(text) -> list[str]           semua URL mentah dari teks
- extract_valid(text) -> list[str]     hanya URL yang lolos validasi
- is_valid(url) -> bool
- normalize(url, drop_tracking=True) -> str
- domains(text) -> list[str]           unique domain dari teks
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_URL_RE = re.compile(
    r"\bhttps?://[^\s<>()\"'\[\]{}]+",
    re.IGNORECASE,
)
_BARE_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?\.)+"
    r"(?:com|org|net|io|dev|ai|app|co|id|gov|edu|info|biz|xyz|me|sh|so|to|gg|fm|tv)\b"
    r"(?:/[^\s<>()\"'\[\]{}]*)?",
    re.IGNORECASE,
)

#: Query params that exist only to track the click, not to affect content.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "msclkid", "dclid", "twclid",
    "ref", "ref_src", "ref_url", "igshid", "mc_cid", "mc_eid",
}

#: Hosts where these params are functional, not trackers.
FUNCTIONAL_PARAM_HOSTS = {"github.com", "gitlab.com", "pypi.org", "npmjs.com"}


def is_valid(url: str) -> bool:
    """True when `url` parses as http(s) with a plausible host."""
    if not url or any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        # Control chars/whitespace inside the URL string itself (urlparse
        # silently strips tabs/newlines from the netloc, so check first).
        return False
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname
    if not host or "." not in host and host != "localhost":
        return False
    if len(host) > 253:
        return False
    # Reject spaces/control chars smuggled into the netloc.
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in parts.netloc):
        return False
    return True


def extract(text: str) -> list[str]:
    """All explicit http(s) URLs in `text`, in order, deduplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?)'\"")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def extract_bare(text: str) -> list[str]:
    """Bare domains without a scheme, e.g. 'example.com/docs'."""
    explicit = {urlparse(u).netloc + urlparse(u).path for u in extract(text)}
    seen: set[str] = set()
    out: list[str] = []
    for match in _BARE_DOMAIN_RE.finditer(text or ""):
        candidate = match.group(0)
        # Skip anything already covered by an explicit URL.
        if any(candidate in u or u.endswith(candidate) for u in explicit):
            continue
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            out.append("https://" + candidate)
    return out


def extract_valid(text: str) -> list[str]:
    """extract() filtered through is_valid()."""
    return [u for u in extract(text) if is_valid(u)]


def normalize(url: str, drop_tracking: bool = True) -> str:
    """Lowercase host, sort query params, optionally strip tracking params."""
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if not host:
        return url
    scheme = parts.scheme.lower() or "https"
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    if parts.username:
        cred = parts.username
        if parts.password:
            cred += f":{parts.password}"
        netloc = f"{cred}@{netloc}"
    query = parts.query
    if drop_tracking and parts.query:
        pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if k.lower() not in TRACKING_PARAMS
                 or host in FUNCTIONAL_PARAM_HOSTS]
        query = urlencode(pairs)
    return urlunparse((scheme, netloc, parts.path or "/", "", query, ""))


def domains(text: str) -> list[str]:
    """Unique registrable hosts in `text`, lowercased, in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for url in extract_valid(text) + extract_bare(text):
        try:
            host = urlparse(url).hostname or ""
        except ValueError:
            continue
        host = host.lower().removeprefix("www.")
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    return out


def main() -> int:
    """CLI: cat page.txt | python3 -m zeline.zenith_tools.scripts.url_extract"""
    import json
    import sys
    text = sys.stdin.read()
    if not text:
        print(__doc__)
        return 2
    print(json.dumps({
        "urls": extract_valid(text),
        "bare": extract_bare(text),
        "domains": domains(text),
        "normalized": [normalize(u) for u in extract_valid(text)],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
