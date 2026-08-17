#!/usr/bin/env python3
"""
tools/api_harvester.py — API-first data harvesting  (v4.1.2)

Ganti "klik-klik browser" jadi "replikasi request". Alur khas anti-browser:
  DevTools → Network → Copy as cURL → parse_curl() → paginate_* → extract() → to_*

Murni stdlib untuk semua logika (parsing, paginasi, ekstraksi, tulis file).
`send()` butuh httpx — di-import lazy supaya modul tetap bisa diuji tanpa deps.
"""
from __future__ import annotations

import csv
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator


# ─────────────── pagination ───────────────
def paginate_offset(
    fetch: Callable[[int, int], list],
    *,
    limit: int = 100,
    start: int = 0,
    max_pages: int | None = None,
) -> Iterator[Any]:
    """fetch(offset, limit) -> list. Berhenti saat halaman kosong/short, atau
    setelah max_pages."""
    offset, pages = start, 0
    while True:
        page = fetch(offset, limit)
        if not page:
            return
        yield from page
        pages += 1
        if len(page) < limit:
            return
        if max_pages is not None and pages >= max_pages:
            return
        offset += limit


def paginate_cursor(
    fetch: Callable[[Any], tuple[list, Any]],
    *,
    start_cursor: Any = None,
    max_pages: int | None = None,
) -> Iterator[Any]:
    """fetch(cursor) -> (items, next_cursor). Berhenti saat next_cursor falsy."""
    cursor, pages = start_cursor, 0
    while True:
        items, cursor = fetch(cursor)
        yield from items
        pages += 1
        if not cursor:
            return
        if max_pages is not None and pages >= max_pages:
            return


# ─────────────── JSON path extract ───────────────
def _parse_path(path: str) -> list[str]:
    tokens: list[str] = []
    for part in path.split("."):
        for seg in re.findall(r"[^\[\]]+|\[[^\]]*\]", part):
            tokens.append(seg)
    return tokens


def extract(obj: Any, path: str) -> list:
    """Ambil nilai lewat path bertitik dengan dukungan index `[0]` dan
    wildcard `[*]`. Selalu balikin list match (kosong kalau gak ketemu).
    Contoh: extract(o, 'data.items[*].addr')."""
    current = [obj]
    for tok in _parse_path(path):
        nxt: list = []
        for c in current:
            if tok == "[*]":
                if isinstance(c, list):
                    nxt.extend(c)
            elif tok.startswith("[") and tok.endswith("]"):
                try:
                    idx = int(tok[1:-1])
                except ValueError:
                    continue
                if isinstance(c, list) and -len(c) <= idx < len(c):
                    nxt.append(c[idx])
            else:
                if isinstance(c, dict) and tok in c:
                    nxt.append(c[tok])
        current = nxt
    return current


# ─────────────── cURL → RequestSpec ───────────────
@dataclass
class RequestSpec:
    method: str = "GET"
    url: str = ""
    headers: dict = field(default_factory=dict)
    data: str | None = None


def parse_curl(command: str) -> RequestSpec:
    """Parse perintah cURL (mis. dari DevTools 'Copy as cURL') jadi RequestSpec
    yang bisa direplikasi di kode. Mendukung -X/--request, -H/--header,
    -d/--data/--data-raw/--data-binary, dan flag lain diabaikan."""
    tokens = shlex.split(command.replace("\\\n", " "))
    if tokens and tokens[0] == "curl":
        tokens = tokens[1:]
    spec = RequestSpec()
    method: str | None = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1]
            i += 2
            continue
        if t in ("-H", "--header") and i + 1 < len(tokens):
            h = tokens[i + 1]
            i += 2
            if ":" in h:
                k, v = h.split(":", 1)
                spec.headers[k.strip()] = v.strip()
            continue
        if t in ("-d", "--data", "--data-raw", "--data-binary") and i + 1 < len(tokens):
            spec.data = tokens[i + 1]
            i += 2
            continue
        if t.startswith("-"):
            i += 1  # flag tanpa nilai yang kita pedulikan (mis. --compressed)
            continue
        if not spec.url:
            spec.url = t
        i += 1
    spec.method = (method or ("POST" if spec.data else "GET")).upper()
    return spec


def send(spec: RequestSpec, *, client: Any = None, timeout: float = 30.0):
    """Eksekusi RequestSpec pakai httpx (lazy import). Balikin httpx.Response.
    Hanya dipakai saat benar-benar nembak jaringan."""
    import httpx  # lazy: cuma perlu pas hit jaringan

    c = client or httpx.Client(timeout=timeout)
    return c.request(
        spec.method, spec.url, headers=spec.headers or None, content=spec.data
    )


# ─────────────── writers ───────────────
def to_jsonl(rows: Any, path: str | Path) -> int:
    n = 0
    with Path(path).open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def to_csv(rows: Any, path: str | Path, fieldnames: list[str] | None = None) -> int:
    rows = list(rows)
    if fieldnames is None:
        fieldnames = []
        for r in rows:
            for k in r:
                if k not in fieldnames:
                    fieldnames.append(k)
    with Path(path).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    return len(rows)
