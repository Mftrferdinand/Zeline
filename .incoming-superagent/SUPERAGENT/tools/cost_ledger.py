#!/usr/bin/env python3
"""
tools/cost_ledger.py — Unified Cost & Usage Ledger  (v4.2)

Satu tempat buat ngeliat "agent ini udah makan berapa": token per provider (LLM),
nilai USD on-chain (dari governor), dan API call (dari revenue_engine). Tujuannya
observability terpusat — dashboard.py / explain.py tinggal baca dari sini.

Desain:
- SQLite tunggal (default ~/.superagent/ledger.db) → tahan restart, zero-dep.
- record_tokens / record_onchain / record_api → 3 jenis entri, satu tabel.
- summary(window) → agregasi per kind + per provider/chain, plus estimasi USD.
- Harga token TIDAK dihitung di sini: caller kasih usd_cost (pakai tarif provider
  sendiri) ATAU set price map via PRICE_PER_1K. Kalau gak ada → biaya USD di-skip.

Zero dependency eksternal (sqlite3, time, json, pathlib). Connection bisa di-inject
buat test (in-memory). Aman dipanggil multi-thread (sqlite check_same_thread=False
+ lock internal).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_DB = Path(os.environ.get("SUPERAGENT_LEDGER_DB", "~/.superagent/ledger.db")).expanduser()

# Estimasi USD per 1k token (input+output digabung). Operator override via env JSON:
#   SUPERAGENT_PRICE_PER_1K='{"claude":0.009,"gpt-4o":0.005}'
_DEFAULT_PRICE_PER_1K = {
    "claude": 0.009,
    "kimi": 0.0015,
    "openrouter": 0.004,
    "deepseek": 0.0003,
    "groq": 0.0001,
}


def _load_price_map() -> dict:
    raw = os.environ.get("SUPERAGENT_PRICE_PER_1K")
    if raw:
        try:
            return {**_DEFAULT_PRICE_PER_1K, **json.loads(raw)}
        except Exception:  # noqa: BLE001 — kalau JSON rusak, pakai default
            pass
    return dict(_DEFAULT_PRICE_PER_1K)


@dataclass
class LedgerSummary:
    window_s: float
    token_total: int
    token_usd: float
    onchain_usd: float
    api_calls: int
    by_provider: dict          # provider -> {"tokens": int, "usd": float}
    by_chain: dict             # chain_id -> usd
    entries: int

    @property
    def total_usd(self) -> float:
        return round(self.token_usd + self.onchain_usd, 4)

    def report(self) -> str:
        prov = ", ".join(
            f"{p}:{v['tokens']}tok(${v['usd']:.3f})" for p, v in sorted(self.by_provider.items())
        ) or "—"
        chains = ", ".join(f"chain{c}:${u:.2f}" for c, u in sorted(self.by_chain.items())) or "—"
        hrs = self.window_s / 3600
        return (
            f"💸 cost ledger (last {hrs:.1f}h | {self.entries} entries)\n"
            f"   LLM tokens: {self.token_total:,} (~${self.token_usd:.3f}) | {prov}\n"
            f"   on-chain:   ${self.onchain_usd:,.2f} | {chains}\n"
            f"   API calls:  {self.api_calls:,}\n"
            f"   TOTAL est:  ${self.total_usd:,.3f}"
        )


class CostLedger:
    def __init__(self, db_path: Path = DEFAULT_DB, conn: Optional[sqlite3.Connection] = None,
                 price_per_1k: Optional[dict] = None):
        self._lock = threading.Lock()
        self.price_per_1k = price_per_1k if price_per_1k is not None else _load_price_map()
        if conn is not None:
            self._db = conn
        else:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS ledger (
                   ts REAL, kind TEXT, provider TEXT, chain_id INTEGER,
                   tokens INTEGER, usd REAL, session_id TEXT, note TEXT
               )"""
        )
        self._db.commit()

    # ---- writers ----
    def record_tokens(self, provider: str, tokens: int, *, usd: Optional[float] = None,
                      session_id: str = "", note: str = "") -> float:
        """Catat pemakaian token. Kalau usd None → estimasi dari price_per_1k."""
        if usd is None:
            rate = self.price_per_1k.get(provider, 0.0)
            usd = (tokens / 1000.0) * rate
        self._insert("tokens", provider=provider, tokens=int(tokens), usd=float(usd),
                     session_id=session_id, note=note)
        return float(usd)

    def record_onchain(self, chain_id: int, usd: float, *, session_id: str = "",
                       note: str = "") -> None:
        """Catat belanja on-chain (nilai USD dari caller/governor)."""
        self._insert("onchain", chain_id=int(chain_id), usd=float(usd),
                     session_id=session_id, note=note)

    def record_api(self, provider: str, calls: int = 1, *, usd: float = 0.0,
                   session_id: str = "", note: str = "") -> None:
        """Catat panggilan API eksternal (default biaya 0)."""
        self._insert("api", provider=provider, tokens=int(calls), usd=float(usd),
                     session_id=session_id, note=note)

    def _insert(self, kind: str, *, provider: str = "", chain_id: Optional[int] = None,
                tokens: int = 0, usd: float = 0.0, session_id: str = "", note: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), kind, provider, chain_id, tokens, usd, session_id, note),
            )
            self._db.commit()

    # ---- reader ----
    def summary(self, window_s: float = 86400.0, session_id: Optional[str] = None) -> LedgerSummary:
        since = time.time() - window_s
        q = "SELECT kind, provider, chain_id, tokens, usd FROM ledger WHERE ts >= ?"
        args: list = [since]
        if session_id is not None:
            q += " AND session_id = ?"
            args.append(session_id)
        rows = self._db.execute(q, args).fetchall()
        token_total = token_usd = onchain_usd = api_calls = 0
        token_usd = onchain_usd = 0.0
        by_provider: dict = {}
        by_chain: dict = {}
        for kind, provider, chain_id, tokens, usd in rows:
            if kind == "tokens":
                token_total += tokens or 0
                token_usd += usd or 0.0
                p = by_provider.setdefault(provider or "?", {"tokens": 0, "usd": 0.0})
                p["tokens"] += tokens or 0
                p["usd"] += usd or 0.0
            elif kind == "onchain":
                onchain_usd += usd or 0.0
                by_chain[chain_id] = by_chain.get(chain_id, 0.0) + (usd or 0.0)
            elif kind == "api":
                api_calls += tokens or 0
        return LedgerSummary(
            window_s=window_s,
            token_total=token_total,
            token_usd=round(token_usd, 6),
            onchain_usd=round(onchain_usd, 6),
            api_calls=api_calls,
            by_provider={k: {"tokens": v["tokens"], "usd": round(v["usd"], 6)}
                         for k, v in by_provider.items()},
            by_chain={k: round(v, 6) for k, v in by_chain.items()},
            entries=len(rows),
        )

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "CostLedger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


if __name__ == "__main__":
    led = CostLedger(conn=sqlite3.connect(":memory:"))
    led.record_tokens("claude", 12000, session_id="demo")
    led.record_tokens("deepseek", 50000, session_id="demo")
    led.record_onchain(1, 42.5, session_id="demo")
    led.record_api("opensea", 3, session_id="demo")
    print(led.summary().report())
