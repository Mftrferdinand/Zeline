#!/usr/bin/env python3
"""
tools/unlock_engine.py — Tokenomics & Unlock Pressure Engine  (v4.2, sk36)

Susun kalender vesting/unlock + prediksi TEKANAN JUAL tiap event (nilai unlock vs
likuiditas harian). Bantu jawab "jual sebelum unlock besar?". Pelengkap exit_planner
(sk31) — fokus di sisi supply/sell-pressure makro.

Murni logika (offline, deterministik). `now` WAJIB di-inject (TIME.md) — gak ada
fabrikasi waktu. Data unlock/likuiditas mentah didelegasi ke sk10/sk22. Zero-dep.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UnlockEvent:
    label: str                       # mis. "Team cliff", "Investor linear"
    ts: float                        # epoch detik tanggal unlock
    pct_of_supply: float             # % dari total supply yang unlock di event ini


@dataclass
class MarketState:
    price_usd: float                 # harga token saat ini
    total_supply: float              # total supply token
    circulating_supply: float        # beredar saat ini
    daily_volume_usd: float          # volume perdagangan harian (proxy likuiditas)


@dataclass
class UnlockVerdict:
    label: str
    days_until: float
    unlock_value_usd: float
    pct_of_circulating: float        # unlock vs beredar
    pressure_ratio: float            # unlock_value / daily_volume
    pressure: str                    # low | medium | high | extreme
    signal: str                      # catatan aksi

    def report(self) -> str:
        return (f"🔓 {self.label}: T-{self.days_until:.0f}h · ${self.unlock_value_usd:,.0f} "
                f"({self.pct_of_circulating:.1f}% circ) · pressure {self.pressure} "
                f"({self.pressure_ratio:.1f}x vol) — {self.signal}")


def _pressure(ratio: float) -> str:
    if ratio >= 3:
        return "extreme"
    if ratio >= 1:
        return "high"
    if ratio >= 0.3:
        return "medium"
    return "low"


def assess_event(ev: UnlockEvent, mkt: MarketState, now: float) -> UnlockVerdict:
    """Nilai satu unlock event relatif ke kondisi pasar. `now` di-inject."""
    days = (ev.ts - now) / 86400.0
    unlock_tokens = mkt.total_supply * (ev.pct_of_supply / 100.0)
    value = unlock_tokens * mkt.price_usd
    pct_circ = (unlock_tokens / mkt.circulating_supply * 100.0) if mkt.circulating_supply > 0 else 0.0
    ratio = (value / mkt.daily_volume_usd) if mkt.daily_volume_usd > 0 else float("inf")
    pressure = _pressure(ratio)

    if days < 0:
        signal = "sudah lewat"
    elif pressure in ("high", "extreme") and days <= 14:
        signal = "⚠️ tekanan jual besar < 2 minggu — pertimbangkan kurangi posisi DULUAN"
    elif pressure in ("high", "extreme"):
        signal = "tandai: unlock besar di depan, siapkan rencana exit"
    else:
        signal = "dampak relatif kecil"

    return UnlockVerdict(label=ev.label, days_until=days, unlock_value_usd=round(value, 2),
                         pct_of_circulating=round(pct_circ, 2),
                         pressure_ratio=(ratio if ratio != float("inf") else 999.0),
                         pressure=pressure, signal=signal)


def build_calendar(events: list, mkt: MarketState, now: float,
                   horizon_days: float = 365) -> list:
    """Kalender unlock ke depan dalam horizon, urut tanggal terdekat dulu."""
    out = []
    for ev in events:
        v = assess_event(ev, mkt, now)
        if -1 <= v.days_until <= horizon_days:
            out.append(v)
    return sorted(out, key=lambda v: v.days_until)


def biggest_pressure(events: list, mkt: MarketState, now: float):
    """Event dengan tekanan jual paling tinggi (yang belum lewat)."""
    future = [assess_event(e, mkt, now) for e in events]
    future = [v for v in future if v.days_until >= 0]
    if not future:
        return None
    return max(future, key=lambda v: v.pressure_ratio)


if __name__ == "__main__":
    now = 1_760_000_000.0
    mkt = MarketState(price_usd=2.0, total_supply=1_000_000_000,
                      circulating_supply=200_000_000, daily_volume_usd=5_000_000)
    evs = [
        UnlockEvent("Investor cliff", now + 10 * 86400, 8.0),
        UnlockEvent("Team linear", now + 120 * 86400, 2.0),
    ]
    for v in build_calendar(evs, mkt, now):
        print(v.report())
