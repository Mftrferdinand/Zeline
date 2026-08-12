#!/usr/bin/env python3
"""
tools/eligibility.py — Airdrop Eligibility Scorer  (v4.2, sk31)

Kasih statistik on-chain sebuah wallet → skor "seberapa mungkin lolos kriteria
airdrop" + apa yang KURANG biar makin kuat. Murni logika scoring; pengambilan data
on-chain didelegasi ke sk10/hermes (RPC, explorer) — tool ini gak nyentuh jaringan,
jadi bisa di-eval offline & deterministik.

Model skor: weighted rubric. Tiap kriteria punya target & bobot; skor = berapa dekat
wallet ke target, dijumlah berbobot, dinormalisasi 0-100. Rubric default mencerminkan
sinyal airdrop umum (volume, umur, frekuensi, keragaman protokol/chain, retensi).
Operator bisa override rubric per-project (tiap project beda kriteria).

Zero-dep (dataclasses/math). Input = WalletStats; output = EligibilityResult.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WalletStats:
    tx_count: int = 0
    age_days: float = 0.0
    volume_usd: float = 0.0
    unique_contracts: int = 0
    distinct_chains: int = 0
    active_weeks: int = 0          # minggu unik dengan minimal 1 tx (retensi)
    bridged: bool = False
    holds_lp_or_stake: bool = False
    last_active_days_ago: float = 0.0


@dataclass
class Criterion:
    key: str
    target: float                  # nilai yang dianggap "penuh" (skor 1.0)
    weight: float
    label: str = ""

    def score(self, value: float) -> float:
        if self.target <= 0:
            return 1.0 if value > 0 else 0.0
        return max(0.0, min(1.0, value / self.target))


# Rubric default — sinyal airdrop generik. Tiap project sebaiknya disesuaikan.
DEFAULT_RUBRIC = [
    Criterion("tx_count", 40, 0.20, "jumlah transaksi"),
    Criterion("age_days", 180, 0.15, "umur wallet (hari)"),
    Criterion("volume_usd", 10000, 0.20, "volume USD"),
    Criterion("unique_contracts", 15, 0.15, "kontrak unik di-interaksi"),
    Criterion("distinct_chains", 3, 0.10, "jumlah chain aktif"),
    Criterion("active_weeks", 12, 0.20, "minggu aktif (retensi)"),
]


@dataclass
class EligibilityResult:
    score: int                     # 0-100
    band: str                      # weak | moderate | strong
    breakdown: dict                # key -> {"score": 0..1, "weight": w, "value": v, "target": t}
    gaps: list                     # saran konkret apa yang kurang
    flags: list                    # catatan kualitatif (dormant, no bridge, dll)

    def report(self) -> str:
        lines = [f"🎯 eligibility: {self.score}/100 ({self.band})"]
        for g in self.gaps:
            lines.append(f"   ↑ {g}")
        for f in self.flags:
            lines.append(f"   ⚑ {f}")
        return "\n".join(lines)


def _band(score: int) -> str:
    if score >= 70:
        return "strong"
    if score >= 40:
        return "moderate"
    return "weak"


def score_wallet(stats: WalletStats, rubric: Optional[list] = None,
                 bonus: Optional[dict] = None) -> EligibilityResult:
    """Hitung skor eligibility 0-100 dari WalletStats vs rubric.

    bonus: dict opsional {flag_attr: poin_bonus} buat sinyal boolean (mis. bridged).
    Default kasih sedikit bonus buat bridged & holds_lp_or_stake.
    """
    rubric = rubric or DEFAULT_RUBRIC
    bonus = bonus if bonus is not None else {"bridged": 3, "holds_lp_or_stake": 4}
    total_w = sum(c.weight for c in rubric) or 1.0
    breakdown: dict = {}
    acc = 0.0
    gaps = []
    for c in rubric:
        value = float(getattr(stats, c.key, 0) or 0)
        s = c.score(value)
        acc += s * c.weight
        breakdown[c.key] = {"score": round(s, 3), "weight": c.weight,
                            "value": value, "target": c.target}
        if s < 0.6:
            need = c.target - value
            gaps.append(f"{c.label}: {value:g} → target {c.target:g} (kurang {need:g})")
    base = (acc / total_w) * 100.0

    # bonus boolean (di-cap supaya gak nembus 100)
    bonus_pts = sum(pts for attr, pts in bonus.items() if getattr(stats, attr, False))
    score = int(round(min(100.0, base + bonus_pts)))

    flags = []
    if stats.last_active_days_ago and stats.last_active_days_ago > 30:
        flags.append(f"dormant {stats.last_active_days_ago:.0f} hari — banyak project kecualikan wallet pasif")
    if not stats.bridged:
        flags.append("belum pernah bridge — sebagian project nge-reward cross-chain activity")
    if stats.distinct_chains <= 1:
        flags.append("aktivitas 1 chain doang — keragaman chain sering jadi sinyal")

    return EligibilityResult(score=score, band=_band(score),
                             breakdown=breakdown, gaps=gaps[:5], flags=flags)


if __name__ == "__main__":
    w = WalletStats(tx_count=22, age_days=120, volume_usd=4200, unique_contracts=9,
                    distinct_chains=2, active_weeks=7, bridged=True, last_active_days_ago=5)
    print(score_wallet(w).report())
