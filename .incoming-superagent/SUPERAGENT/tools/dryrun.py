#!/usr/bin/env python3
"""
tools/dryrun.py — Global Dry-Run / Simulation Mode  (v4.2)

Satu flag yang dihormatin SEMUA engine (swap, bridge, mint, airdrop_runner, dll):
kalau aktif → jalanin full pipeline TANPA broadcast, dan kumpulin "ini yang BAKAL
kejadian". Buat testing playbook baru tanpa resiko, dan buat ngajarin member
(AirdropFinder) langkah demi langkah tanpa ngegerakin dana.

Cara pakai di engine:
    from dryrun import is_dry_run, plan
    if is_dry_run():
        plan("swap", chain=1, detail="100 USDC → ETH @1inch", est_usd=100)
        return                      # JANGAN broadcast
    # ... broadcast asli ...

Aktivasi:
    - env SUPERAGENT_DRY_RUN=1
    - atau context manager: `with dry_run(): ...`
    - atau set_dry_run(True)

Thread-aware via contextvars (aman buat BulkRunner thread pool: state diset di
thread utama ke-inherit; tiap thread bisa override sendiri). Zero-dep.
"""
from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from dataclasses import dataclass, field

_DRY = contextvars.ContextVar("superagent_dry_run", default=None)
# Buffer rencana per-run (di-share via list di contextvar biar thread pool ngumpul ke satu tempat)
_PLAN = contextvars.ContextVar("superagent_dry_plan", default=None)


def _env_default() -> bool:
    return os.environ.get("SUPERAGENT_DRY_RUN", "0") not in ("0", "", "false", "False")


def is_dry_run() -> bool:
    v = _DRY.get()
    if v is None:
        return _env_default()
    return bool(v)


def set_dry_run(on: bool) -> None:
    _DRY.set(bool(on))


@dataclass
class PlannedAction:
    action: str
    chain: int | None = None
    detail: str = ""
    est_usd: float | None = None
    meta: dict = field(default_factory=dict)

    def line(self) -> str:
        c = f" chain={self.chain}" if self.chain is not None else ""
        u = f" (~${self.est_usd:,.2f})" if self.est_usd is not None else ""
        return f"DRY {self.action}{c}: {self.detail}{u}"


def _buffer() -> list:
    buf = _PLAN.get()
    if buf is None:
        buf = []
        _PLAN.set(buf)
    return buf


def plan(action: str, *, chain: int | None = None, detail: str = "",
         est_usd: float | None = None, **meta) -> PlannedAction:
    """Catat satu aksi yang BAKAL dijalankan (dipanggil engine saat dry-run)."""
    pa = PlannedAction(action=action, chain=chain, detail=detail, est_usd=est_usd, meta=meta)
    _buffer().append(pa)
    return pa


def get_plan() -> list:
    return list(_buffer())


def clear_plan() -> None:
    _PLAN.set([])


def render_plan() -> str:
    actions = get_plan()
    if not actions:
        return "🧪 DRY-RUN: tidak ada aksi yang ter-rencana."
    total = sum(a.est_usd or 0.0 for a in actions)
    lines = [f"🧪 DRY-RUN PLAN — {len(actions)} aksi (TIDAK ada yang di-broadcast):"]
    lines += [f"   {i+1}. {a.line()}" for i, a in enumerate(actions)]
    if total:
        lines.append(f"   → total nilai yang AKAN bergerak: ~${total:,.2f}")
    return "\n".join(lines)


@contextmanager
def dry_run():
    """Scope dry-run: aktif di dalam blok, plan bersih, otomatis balik setelahnya."""
    tok_d = _DRY.set(True)
    tok_p = _PLAN.set([])
    try:
        yield
    finally:
        _DRY.reset(tok_d)
        _PLAN.reset(tok_p)


if __name__ == "__main__":
    with dry_run():
        plan("swap", chain=1, detail="100 USDC → ETH @1inch", est_usd=100)
        plan("bridge", chain=8453, detail="0.05 ETH ETH→Base via LI.FI", est_usd=180)
        plan("mint", chain=1, detail="Zora edition x3 wallets")
        print(render_plan())
    print("after scope, is_dry_run():", is_dry_run())
