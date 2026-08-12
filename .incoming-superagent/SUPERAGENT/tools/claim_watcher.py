#!/usr/bin/env python3
"""
tools/claim_watcher.py — Airdrop Calendar & Claim-Window Watcher  (v4.2, sk31)

Gabungin sk14 (alert engine) + TIME.md (5-layer time awareness). Simpen event airdrop
(claim window buka/tutup, vesting unlock, snapshot) → tool ini ngitung "berapa lama
lagi / udah lewat?" dan ngembaliin alert yang due. Time SELALU di-inject (TIME.md
Layer 1/2): `now` adalah parameter wajib — tool ini gak pernah nebak waktu sendiri.

Zero-dep (dataclasses/time-math). Persisten opsional ke JSON. Deterministik (now di-inject).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class AirdropEvent:
    project: str
    kind: str                      # "claim_open" | "claim_close" | "vesting_unlock" | "snapshot"
    ts: float                      # epoch detik (UTC)
    note: str = ""
    chain: str = ""
    alerted_offsets: list = field(default_factory=list)   # offset (jam) yang udah di-alert


# Default: ingetin H-48 jam, H-2 jam, dan saat kejadian (offset jam sebelum ts)
DEFAULT_OFFSETS_H = [48, 2, 0]


def _fmt_delta(seconds: float) -> str:
    past = seconds < 0
    s = abs(seconds)
    d, rem = divmod(int(s), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(f"{d}h")        # hari
    if h or d:
        parts.append(f"{h}j")
    parts.append(f"{m}m")
    body = " ".join(parts)
    return f"{body} lalu (LEWAT)" if past else f"{body} lagi"


@dataclass
class DueAlert:
    event: AirdropEvent
    offset_h: int
    message: str


class ClaimWatcher:
    def __init__(self, store: Optional[str | Path] = None,
                 offsets_h: Optional[list] = None):
        self.store = Path(store) if store else None
        self.offsets_h = sorted(offsets_h or DEFAULT_OFFSETS_H, reverse=True)
        self.events: list = []
        if self.store and self.store.exists():
            self._load()

    def add(self, event: AirdropEvent) -> None:
        self.events.append(event)
        self._save()

    def due(self, now: float, tolerance_h: float = 1.0) -> list:
        """Return alert yang due pada `now` (epoch). Time WAJIB di-inject (TIME.md).

        Sebuah event nge-trigger alert di tiap offset (H-48/H-2/H-0) sekali saja.
        tolerance_h: jendela toleransi biar alert gak kelewat kalau poll-nya jarang.
        """
        out = []
        tol = tolerance_h * 3600
        for ev in self.events:
            for off in self.offsets_h:
                if off in ev.alerted_offsets:
                    continue
                fire_at = ev.ts - off * 3600
                if fire_at <= now <= fire_at + tol or (off == 0 and 0 <= now - ev.ts <= tol):
                    delta = ev.ts - now
                    label = {"claim_open": "CLAIM BUKA", "claim_close": "CLAIM TUTUP",
                             "vesting_unlock": "VESTING UNLOCK", "snapshot": "SNAPSHOT"}.get(
                        ev.kind, ev.kind.upper())
                    chain = f" [{ev.chain}]" if ev.chain else ""
                    msg = (f"⏰ {ev.project}{chain} — {label} {_fmt_delta(delta)}"
                           + (f" · {ev.note}" if ev.note else ""))
                    out.append(DueAlert(ev, off, msg))
                    ev.alerted_offsets.append(off)
        if out:
            self._save()
        return out

    def upcoming(self, now: float, within_days: float = 14) -> list:
        """Event mendatang dalam N hari, urut paling dekat dulu (buat 'kalender')."""
        horizon = now + within_days * 86400
        future = [e for e in self.events if now <= e.ts <= horizon]
        future.sort(key=lambda e: e.ts)
        return future

    def _load(self) -> None:
        data = json.loads(self.store.read_text())
        self.events = [AirdropEvent(**d) for d in data]

    def _save(self) -> None:
        if not self.store:
            return
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps([asdict(e) for e in self.events], indent=2))


if __name__ == "__main__":
    now = 1_700_000_000.0
    w = ClaimWatcher()
    w.add(AirdropEvent("ProjectX", "claim_open", now + 2 * 3600, note="claim di app.x.io", chain="Base"))
    w.add(AirdropEvent("ProjectY", "vesting_unlock", now + 5 * 86400))
    for a in w.due(now):
        print(a.message)
    print("Kalender 14h:", [(e.project, e.kind) for e in w.upcoming(now)])
