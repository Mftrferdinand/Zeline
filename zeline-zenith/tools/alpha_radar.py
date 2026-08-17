#!/usr/bin/env python3
"""
tools/alpha_radar.py — Pre-TGE Airdrop Alpha Radar  (v4.2, sk33)

Nilai SEBUAH PROYEK seberapa besar peluangnya bagi-bagi airdrop SEBELUM token
diumumkan. Beda dari sk31 (eligibility = cek wallet kita ke airdrop yang udah ada),
sk33 = discovery: proyek mana yang LAYAK DIFARMING sekarang.

Murni logika scoring (offline, deterministik). Pengumpulan sinyal mentah (funding,
deploy kontrak testnet, points program, aktivitas GitHub/governance) didelegasi ke
sk6/sk22/sk10 — tool ini gak nyentuh jaringan, jadi bisa di-eval offline.

Model: weighted rubric atas sinyal "probabilitas airdrop". Skor 0-100 → tier
(cold/watch/warm/hot) + alasan konkret + estimasi effort. Zero-dep (dataclasses).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProjectSignal:
    name: str
    funded_usd: float = 0.0          # total raise; makin gede makin mungkin ada token
    has_token: bool = False          # True = udah ada token → peluang airdrop turun drastis
    points_program: bool = False     # ada sistem poin/XP (sinyal kuat pre-TGE)
    testnet_live: bool = False       # testnet incentivized jalan
    testnet_contracts: int = 0       # jumlah kontrak baru ke-deploy (aktivitas dev)
    github_commits_30d: int = 0      # momentum dev 30 hari
    governance_active: bool = False  # ada governance/DAO tapi belum ada token
    social_growth_pct: float = 0.0   # pertumbuhan follower/komunitas 30 hari (%)
    days_since_last_round: float = 0.0  # makin lama sejak raise → makin deket TGE
    backed_by_tier1: bool = False    # di-back VC tier-1 (a16z, paradigm, dll)


@dataclass
class AlphaResult:
    name: str
    score: int                       # 0-100
    tier: str                        # cold | watch | warm | hot
    reasons: list                    # kenapa skornya segitu
    effort: str                      # low | medium | high (estimasi modal/waktu farming)

    def report(self) -> str:
        lines = [f"📡 {self.name}: {self.score}/100 ({self.tier}) · effort {self.effort}"]
        for r in self.reasons:
            lines.append(f"   • {r}")
        return "\n".join(lines)


def _tier(score: int) -> str:
    if score >= 75:
        return "hot"
    if score >= 50:
        return "warm"
    if score >= 25:
        return "watch"
    return "cold"


def score_project(sig: ProjectSignal) -> AlphaResult:
    """Skor probabilitas airdrop 0-100 dari sinyal proyek.

    Kalau token udah ada → peluang airdrop (untuk token utama) drastis turun: di-cap.
    """
    reasons = []
    score = 0.0

    # Sinyal positif berbobot
    if sig.points_program:
        score += 22
        reasons.append("ada points/XP program — sinyal pre-TGE paling kuat")
    if sig.testnet_live:
        score += 12
        reasons.append("testnet incentivized aktif")
    if sig.governance_active and not sig.has_token:
        score += 10
        reasons.append("governance jalan tapi belum ada token")
    if sig.backed_by_tier1:
        score += 12
        reasons.append("di-back VC tier-1 (precedent airdrop tinggi)")

    # Funding: makin gede raise, makin mungkin ada token (kurva melandai)
    if sig.funded_usd > 0:
        f = min(15.0, (sig.funded_usd / 50_000_000) * 15.0)
        score += f
        reasons.append(f"raise ~${sig.funded_usd:,.0f}")

    # Momentum dev
    if sig.github_commits_30d >= 50:
        score += 8
        reasons.append(f"momentum dev tinggi ({sig.github_commits_30d} commit/30h)")
    elif sig.github_commits_30d >= 10:
        score += 4
    if sig.testnet_contracts >= 5:
        score += 4
        reasons.append(f"{sig.testnet_contracts} kontrak baru ke-deploy")

    # Pertumbuhan sosial
    if sig.social_growth_pct >= 50:
        score += 6
        reasons.append(f"komunitas tumbuh {sig.social_growth_pct:.0f}%/30h")
    elif sig.social_growth_pct >= 20:
        score += 3

    # Timing: makin lama sejak raise (6-18 bln) makin deket TGE
    if 180 <= sig.days_since_last_round <= 540:
        score += 9
        reasons.append("window timing TGE (6-18 bln sejak raise)")
    elif sig.days_since_last_round > 540:
        score += 4

    # Penalti: token udah ada → airdrop utama kemungkinan lewat
    if sig.has_token:
        score = min(score, 30.0)
        reasons.append("⚠️ token sudah ada — peluang airdrop utama rendah (cap 30)")

    score = int(round(max(0.0, min(100.0, score))))

    # Estimasi effort dari sinyal aktivitas yang diharapkan
    if sig.testnet_live and sig.points_program:
        effort = "high"
    elif sig.points_program or sig.testnet_live:
        effort = "medium"
    else:
        effort = "low"

    return AlphaResult(name=sig.name, score=score, tier=_tier(score),
                       reasons=reasons, effort=effort)


def rank(signals: list, top: Optional[int] = None) -> list:
    """Urutkan banyak proyek dari peluang tertinggi → terendah."""
    results = sorted((score_project(s) for s in signals),
                     key=lambda r: r.score, reverse=True)
    return results[:top] if top else results


if __name__ == "__main__":
    demo = [
        ProjectSignal("ZkProtoX", funded_usd=30_000_000, points_program=True,
                      testnet_live=True, github_commits_30d=80, backed_by_tier1=True,
                      days_since_last_round=300, social_growth_pct=60),
        ProjectSignal("OldChain", funded_usd=5_000_000, has_token=True),
    ]
    for r in rank(demo):
        print(r.report())
