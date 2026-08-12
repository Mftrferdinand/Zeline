"""
tools/alerts.py — Conditional Alert Engine  (v4.0)

Trigger persisten "kabarin kalau ...". Sekali set, jalan terus & push pas kondisi
kena — lewat Notifier (Telegram/Discord) yang udah ada di monitoring.py.

Sumber data keyless di mana bisa:
- harga  → DexScreener (free, no key)  [bisa di-inject fetcher sendiri]
- gas    → eth_gasPrice via RPC (inject gas_fn)
- wallet → integrasi monitoring.py (inject activity_fn)

Dedup: tiap rule punya cooldown — alert yang udah nyala gak refire sampai cooldown
lewat. Jadi gak spam.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Awaitable

import httpx

DEFAULT_DB = Path(os.environ.get("HERMES_ALERTS_DB", "~/.hermes/alerts.db")).expanduser()

# kind → field params yang diharapkan (buat dokumentasi/validasi ringan)
KINDS = {
    "price_below":   ("token", "chain", "threshold"),
    "price_above":   ("token", "chain", "threshold"),
    "gas_below":     ("chain", "threshold_gwei"),
    "gas_above":     ("chain", "threshold_gwei"),
    "wallet_activity": ("wallet", "chain"),
    "claim_window":  ("label", "opens_ts"),
    "custom":        ("expr",),
}


@dataclass
class Rule:
    id: int
    kind: str
    params: dict
    cooldown_s: int
    last_fired: float
    active: int
    label: str


class AlertEngine:
    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.db_path))
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT, params TEXT, cooldown_s INTEGER DEFAULT 3600,
                last_fired REAL DEFAULT 0, active INTEGER DEFAULT 1, label TEXT DEFAULT ''
            )""")
        self.db.commit()

    # ---- manage ----
    def add_rule(self, kind: str, params: dict, cooldown_s: int = 3600, label: str = "") -> int:
        if kind not in KINDS:
            raise ValueError(f"kind tidak dikenal: {kind}. Pilihan: {list(KINDS)}")
        cur = self.db.execute(
            "INSERT INTO rules(kind, params, cooldown_s, label) VALUES (?,?,?,?)",
            (kind, json.dumps(params), cooldown_s, label or kind))
        self.db.commit()
        return cur.lastrowid

    def list_rules(self, only_active: bool = True) -> list[Rule]:
        q = "SELECT id, kind, params, cooldown_s, last_fired, active, label FROM rules"
        if only_active:
            q += " WHERE active = 1"
        return [Rule(r[0], r[1], json.loads(r[2]), r[3], r[4], r[5], r[6])
                for r in self.db.execute(q).fetchall()]

    def remove_rule(self, rule_id: int):
        self.db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        self.db.commit()

    def set_active(self, rule_id: int, active: bool):
        self.db.execute("UPDATE rules SET active = ? WHERE id = ?", (1 if active else 0, rule_id))
        self.db.commit()

    def _mark_fired(self, rule_id: int):
        self.db.execute("UPDATE rules SET last_fired = ? WHERE id = ?", (time.time(), rule_id))
        self.db.commit()

    # ---- evaluate ----
    def _check(self, rule: Rule, fetchers: dict) -> Optional[str]:
        """Return pesan alert kalau kondisi kena & gak dalam cooldown. Else None."""
        if time.time() - rule.last_fired < rule.cooldown_s:
            return None
        p = rule.params
        k = rule.kind
        try:
            if k in ("price_below", "price_above"):
                price_fn = fetchers.get("price_fn", default_price_dexscreener)
                price = price_fn(p["token"], p.get("chain", "ethereum"))
                if price is None:
                    return None
                hit = price < p["threshold"] if k == "price_below" else price > p["threshold"]
                if hit:
                    arrow = "≤" if k == "price_below" else "≥"
                    return f"[{rule.label}] {p['token']} = ${price:,.6g} {arrow} target ${p['threshold']}"
            elif k in ("gas_below", "gas_above"):
                gas_fn = fetchers.get("gas_fn")
                if gas_fn is None:
                    return None
                gwei = gas_fn(p.get("chain", "ethereum"))
                hit = gwei < p["threshold_gwei"] if k == "gas_below" else gwei > p["threshold_gwei"]
                if hit:
                    return f"[{rule.label}] gas {p.get('chain')} = {gwei:.1f} gwei (target {p['threshold_gwei']})"
            elif k == "wallet_activity":
                act_fn = fetchers.get("activity_fn")
                if act_fn is None:
                    return None
                ev = act_fn(p["wallet"], p.get("chain", "ethereum"))
                if ev:
                    return f"[{rule.label}] aktivitas wallet {p['wallet'][:10]}…: {ev}"
            elif k == "claim_window":
                if time.time() >= p["opens_ts"]:
                    return f"[{rule.label}] claim window DIBUKA sekarang"
            elif k == "custom":
                cond_fn = fetchers.get("custom_fn")
                if cond_fn and cond_fn(p.get("expr", "")):
                    return f"[{rule.label}] kondisi custom terpenuhi"
        except Exception:
            return None
        return None

    def evaluate_all(self, fetchers: Optional[dict] = None) -> list[tuple[int, str]]:
        fetchers = fetchers or {}
        fired = []
        for rule in self.list_rules(only_active=True):
            msg = self._check(rule, fetchers)
            if msg:
                self._mark_fired(rule.id)
                fired.append((rule.id, msg))
        return fired

    async def run(self, notifier, poll_interval_s: int = 60,
                  fetchers: Optional[dict] = None, severity: str = "warn"):
        """Loop poll terus-menerus. notifier = monitoring.Notifier (punya .send)."""
        while True:
            for _id, msg in self.evaluate_all(fetchers):
                if notifier:
                    await notifier.send(msg, severity=severity)
                else:
                    print("ALERT:", msg)
            await asyncio.sleep(poll_interval_s)


# ---- default keyless price source: DexScreener ----
def default_price_dexscreener(token_address: str, chain: str = "ethereum") -> Optional[float]:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        r = httpx.get(url, timeout=8)
        pairs = r.json().get("pairs") or []
        if not pairs:
            return None
        # ambil pair dengan likuiditas terbesar
        best = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd", 0))
        return float(best["priceUsd"])
    except Exception:
        return None


if __name__ == "__main__":
    import tempfile
    ae = AlertEngine(Path(tempfile.mktemp()))
    rid = ae.add_rule("price_below", {"token": "0xWETH", "chain": "ethereum", "threshold": 2000},
                      cooldown_s=3600, label="ETH dip")
    ae.add_rule("gas_below", {"chain": "ethereum", "threshold_gwei": 10}, label="gas murah")
    ae.add_rule("claim_window", {"label": "LayerZero", "opens_ts": time.time() - 5}, label="LZ claim")
    fired = ae.evaluate_all(fetchers={
        "price_fn": lambda t, c: 1850.0,         # mock: ETH lagi $1850 < 2000 → fire
        "gas_fn": lambda c: 25.0,                # mock: gas 25 gwei, gak < 10 → no fire
    })
    print("fired:", [m for _, m in fired])
    print("rules aktif:", len(ae.list_rules()))
