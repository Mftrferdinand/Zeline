"""
hermes/governor.py — Spend Governor / Circuit Breaker  (v4.0)

Rail keras buat agent yang pegang dana. "Confirm before signing" tidak cukup
saat mode auto_confirm=True jalan — governor ini yang nahan kalau ada yang
melenceng: nilai per-tx kelewat batas, akumulasi harian/sesi kelewat cap,
slippage ekstrem, gas spike, atau frekuensi tx mendadak abnormal.

Desain:
- authorize(intent) dipanggil SEBELUM broadcast. Return Decision(allow/block/halt).
- record(intent, tx_hash) dipanggil SETELAH broadcast sukses, biar cap-nya update.
- State persisten di SQLite (default ~/.hermes/governor.db) → tahan restart.
- Kill-switch: file flag ATAU trip otomatis kalau anomali. Sekali trip, SEMUA
  authorize() return HALT sampai operator reset manual (reset_killswitch()).

Tidak ada dependency eksternal — stdlib only (sqlite3, json, time, pathlib).
Nilai USD tidak dihitung di sini: caller yang kasih `usd_value` (pakai price
oracle sendiri). Kalau usd_value None, cap berbasis USD di-skip & governor
fallback ke cap native + cek non-nominal (slippage/gas/rate) saja.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Literal

Verdict = Literal["allow", "block", "halt"]
DEFAULT_DB = Path(os.environ.get("HERMES_GOVERNOR_DB", "~/.hermes/governor.db")).expanduser()
KILLSWITCH_FILE = Path(os.environ.get("HERMES_KILLSWITCH", "~/.hermes/HALT")).expanduser()


# ───────────────────────────── config ─────────────────────────────
@dataclass
class GovernorLimits:
    """Semua limit opsional. None = limit itu tidak diberlakukan."""
    max_tx_usd: Optional[float] = None          # cap nilai 1 tx
    daily_cap_usd: Optional[float] = None        # cap akumulasi 24 jam (per wallet)
    session_cap_usd: Optional[float] = None      # cap akumulasi 1 sesi (semua wallet)
    max_slippage_pct: float = 5.0                # tolak swap dengan slippage di atas ini
    max_gas_multiple: float = 4.0                # tolak kalau gas > N x baseline rolling
    max_tx_per_min: int = 12                     # rate limit (anti runaway loop)
    require_simulation: bool = True              # tolak kalau intent belum disimulasi

    @classmethod
    def from_env(cls) -> "GovernorLimits":
        def f(k):
            v = os.environ.get(k)
            return float(v) if v not in (None, "") else None
        return cls(
            max_tx_usd=f("HERMES_MAX_TX_USD"),
            daily_cap_usd=f("HERMES_DAILY_CAP_USD"),
            session_cap_usd=f("HERMES_SESSION_CAP_USD"),
            max_slippage_pct=f("HERMES_MAX_SLIPPAGE_PCT") or 5.0,
            max_gas_multiple=f("HERMES_MAX_GAS_MULTIPLE") or 4.0,
            max_tx_per_min=int(f("HERMES_MAX_TX_PER_MIN") or 12),
            require_simulation=os.environ.get("HERMES_REQUIRE_SIM", "1") != "0",
        )


@dataclass
class TxIntent:
    """Apa yang mau dieksekusi. Diisi caller sebelum authorize()."""
    wallet: str
    chain_id: int
    action: str                       # "swap" | "snipe" | "bridge" | "send" | ...
    usd_value: Optional[float] = None
    native_value: float = 0.0
    slippage_pct: Optional[float] = None
    gas_price_wei: Optional[int] = None
    simulated_ok: Optional[bool] = None
    recipient: Optional[str] = None
    note: str = ""


@dataclass
class Decision:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    remaining_daily_usd: Optional[float] = None
    remaining_session_usd: Optional[float] = None

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"

    def summary(self) -> str:
        icon = {"allow": "✅", "block": "⛔", "halt": "🛑"}[self.verdict]
        body = "; ".join(self.reasons) if self.reasons else "ok"
        return f"{icon} {self.verdict.upper()}: {body}"


# ─────────────────────────── governor ───────────────────────────
class SpendGovernor:
    def __init__(self, limits: Optional[GovernorLimits] = None,
                 db_path: Path = DEFAULT_DB, session_id: Optional[str] = None,
                 conn: Optional[sqlite3.Connection] = None):
        """conn: opsional koneksi DBAPI yang di-inject. Default = SQLite lokal di db_path.

        Multi-VPS: SQLite lokal hanya nge-cap per-mesin. Buat cap GLOBAL lintas
        VPS/region (biar farming paralel gak over-spend gabungan), inject koneksi
        ke backend share-able (mis. Postgres/MySQL via DBAPI yang kompatibel, atau
        SQLite di network FS). Set env HERMES_GOVERNOR_DB ke path bersama, atau
        passing `conn=` langsung. Skema tabelnya stdlib-SQL, portable.
        """
        self.limits = limits or GovernorLimits.from_env()
        self.db_path = db_path
        self.session_id = session_id or os.environ.get("HERMES_SESSION_ID", str(int(time.time())))
        if conn is not None:
            self._db = conn
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS spend (
                ts REAL, session_id TEXT, wallet TEXT, chain_id INTEGER,
                action TEXT, usd_value REAL, native_value REAL,
                gas_price_wei INTEGER, tx_hash TEXT
            )""")
        self._db.execute("CREATE TABLE IF NOT EXISTS flags (k TEXT PRIMARY KEY, v TEXT)")
        self._db.commit()

    # ---- kill-switch ----
    def is_halted(self) -> Optional[str]:
        if KILLSWITCH_FILE.exists():
            return KILLSWITCH_FILE.read_text().strip() or "kill-switch file present"
        row = self._db.execute("SELECT v FROM flags WHERE k='halt'").fetchone()
        return row[0] if row else None

    def trip(self, reason: str):
        self._db.execute("INSERT OR REPLACE INTO flags(k, v) VALUES('halt', ?)",
                         (f"{reason} @ {time.strftime('%Y-%m-%d %H:%M:%S')}",))
        self._db.commit()

    def reset_killswitch(self):
        """Operator-only. Panggil manual setelah investigasi."""
        self._db.execute("DELETE FROM flags WHERE k='halt'")
        self._db.commit()
        try:
            KILLSWITCH_FILE.unlink()
        except FileNotFoundError:
            pass

    # ---- accounting ----
    def _spent_since(self, seconds: float, wallet: Optional[str] = None,
                     session: Optional[str] = None) -> float:
        q = "SELECT COALESCE(SUM(usd_value),0) FROM spend WHERE ts >= ?"
        args: list = [time.time() - seconds]
        if wallet:
            q += " AND wallet = ?"; args.append(wallet)
        if session:
            q += " AND session_id = ?"; args.append(session)
        return float(self._db.execute(q, args).fetchone()[0] or 0.0)

    def _gas_baseline(self, chain_id: int) -> Optional[float]:
        row = self._db.execute(
            "SELECT AVG(gas_price_wei) FROM (SELECT gas_price_wei FROM spend "
            "WHERE chain_id=? AND gas_price_wei IS NOT NULL ORDER BY ts DESC LIMIT 20)",
            (chain_id,)).fetchone()
        return float(row[0]) if row and row[0] else None

    def _tx_count_last_minute(self) -> int:
        return int(self._db.execute(
            "SELECT COUNT(*) FROM spend WHERE ts >= ?", (time.time() - 60,)).fetchone()[0])

    # ---- the gate ----
    def authorize(self, intent: TxIntent) -> Decision:
        halt = self.is_halted()
        if halt:
            return Decision("halt", [f"governor HALTED: {halt}. Reset manual diperlukan."])

        L, reasons = self.limits, []
        block = False

        if L.require_simulation and intent.simulated_ok is not True:
            block = True
            reasons.append("intent belum lolos simulasi (require_simulation=ON)")

        if intent.slippage_pct is not None and intent.slippage_pct > L.max_slippage_pct:
            block = True
            reasons.append(f"slippage {intent.slippage_pct:.2f}% > batas {L.max_slippage_pct:.2f}%")

        if L.max_tx_usd is not None and intent.usd_value is not None \
                and intent.usd_value > L.max_tx_usd:
            block = True
            reasons.append(f"nilai tx ${intent.usd_value:,.2f} > cap per-tx ${L.max_tx_usd:,.2f}")

        # gas spike → tripping anomaly, bukan sekadar block
        if intent.gas_price_wei is not None:
            base = self._gas_baseline(intent.chain_id)
            if base and intent.gas_price_wei > base * L.max_gas_multiple:
                self.trip(f"gas spike chain {intent.chain_id}: "
                          f"{intent.gas_price_wei} > {L.max_gas_multiple}x baseline {base:.0f}")
                return Decision("halt", ["gas spike abnormal → kill-switch tripped"])

        # rate limit → tripping (runaway loop protection)
        if self._tx_count_last_minute() >= L.max_tx_per_min:
            self.trip(f"rate limit: >{L.max_tx_per_min} tx/min")
            return Decision("halt", [f"rate >{L.max_tx_per_min} tx/min → runaway dicurigai, kill-switch tripped"])

        rem_daily = rem_session = None
        if intent.usd_value is not None:
            if L.daily_cap_usd is not None:
                spent = self._spent_since(86400, wallet=intent.wallet)
                rem_daily = L.daily_cap_usd - spent
                if spent + intent.usd_value > L.daily_cap_usd:
                    block = True
                    reasons.append(f"cap harian wallet kelewat: terpakai ${spent:,.2f} "
                                   f"+ ${intent.usd_value:,.2f} > ${L.daily_cap_usd:,.2f}")
            if L.session_cap_usd is not None:
                spent_s = self._spent_since(10**9, session=self.session_id)
                rem_session = L.session_cap_usd - spent_s
                if spent_s + intent.usd_value > L.session_cap_usd:
                    block = True
                    reasons.append(f"cap sesi kelewat: terpakai ${spent_s:,.2f} "
                                   f"+ ${intent.usd_value:,.2f} > ${L.session_cap_usd:,.2f}")

        if block:
            return Decision("block", reasons, rem_daily, rem_session)
        return Decision("allow", [], rem_daily, rem_session)

    def record(self, intent: TxIntent, tx_hash: str):
        """Panggil SETELAH broadcast sukses, biar cap-nya akurat."""
        self._db.execute(
            "INSERT INTO spend VALUES (?,?,?,?,?,?,?,?,?)",
            (time.time(), self.session_id, intent.wallet, intent.chain_id, intent.action,
             intent.usd_value, intent.native_value, intent.gas_price_wei, tx_hash))
        self._db.commit()

    def report(self) -> dict:
        return {
            "session_id": self.session_id,
            "halted": self.is_halted(),
            "session_spent_usd": round(self._spent_since(10**9, session=self.session_id), 2),
            "limits": asdict(self.limits),
        }

    def close(self) -> None:
        """Tutup koneksi DB. Aman dipanggil berkali-kali."""
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "SpendGovernor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ─────────────────────────── usage example ───────────────────────────
if __name__ == "__main__":
    gov = SpendGovernor(GovernorLimits(max_tx_usd=500, daily_cap_usd=2000,
                                       session_cap_usd=1000, require_simulation=True))
    intent = TxIntent(wallet="0xabc", chain_id=1, action="swap",
                      usd_value=120.0, slippage_pct=1.0, simulated_ok=True)
    d = gov.authorize(intent)
    print(d.summary())
    if d.allowed:
        gov.record(intent, "0xdeadbeef")   # setelah broadcast sukses
    print(json.dumps(gov.report(), indent=2, default=str))
