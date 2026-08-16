#!/usr/bin/env python3
"""
tools/rugcheck.py — Project Legitimacy / Rug Pre-Check  (v4.2, sk11 + H4)

Satu command "cek project ini aman gak". Tool ini AGGREGATE sinyal yang udah diambil
sk11 (audit) + H4 (honeypot.is / GoPlus) + on-chain (sk10) jadi satu verdict yang jelas:
SAFE / CAUTION / DANGER, plus alasan. Pengambilan data eksternal didelegasi (gak ada
network call di sini) → bisa di-eval offline & jadi blok edukasi konten AirdropFinder.

Input = SignalSet (apa yang ditemukan checker lain). Output = RugVerdict.
Zero-dep.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SignalSet:
    contract_verified: Optional[bool] = None
    is_honeypot: Optional[bool] = None
    buy_tax_pct: Optional[float] = None
    sell_tax_pct: Optional[float] = None
    lp_locked: Optional[bool] = None
    lp_lock_days: Optional[float] = None
    owner_can_mint: Optional[bool] = None
    owner_can_pause: Optional[bool] = None
    owner_renounced: Optional[bool] = None
    proxy_upgradeable: Optional[bool] = None
    top10_holders_pct: Optional[float] = None     # konsentrasi holder
    age_days: Optional[float] = None
    liquidity_usd: Optional[float] = None


@dataclass
class RugVerdict:
    verdict: str                   # SAFE | CAUTION | DANGER
    risk_score: int                # 0-100 (tinggi = bahaya)
    critical: list                 # red flag yang langsung bikin DANGER
    warnings: list                 # sinyal hati-hati
    unknowns: list                 # data yang belum ada (harus dicek)

    def report(self) -> str:
        icon = {"SAFE": "✅", "CAUTION": "⚠️", "DANGER": "🛑"}[self.verdict]
        lines = [f"{icon} {self.verdict} (risk {self.risk_score}/100)"]
        for c in self.critical:
            lines.append(f"   🛑 {c}")
        for w in self.warnings:
            lines.append(f"   ⚠️ {w}")
        if self.unknowns:
            lines.append(f"   ❓ belum dicek: {', '.join(self.unknowns)}")
        return "\n".join(lines)


def check(s: SignalSet) -> RugVerdict:
    critical, warnings, unknowns = [], [], []
    risk = 0

    # ---- critical (langsung DANGER) ----
    if s.is_honeypot is True:
        critical.append("HONEYPOT — bisa beli gak bisa jual")
    if s.sell_tax_pct is not None and s.sell_tax_pct >= 30:
        critical.append(f"sell tax ekstrem {s.sell_tax_pct:.0f}%")
    if s.owner_can_mint is True and s.owner_renounced is not True:
        critical.append("owner bisa mint tak terbatas (belum renounce)")
    if s.lp_locked is False:
        critical.append("LP TIDAK terkunci — rug pull klasik")

    # ---- warnings (naikin risk) ----
    if s.contract_verified is False:
        warnings.append("kontrak belum verified di explorer")
        risk += 20
    if s.buy_tax_pct is not None and s.buy_tax_pct >= 10:
        warnings.append(f"buy tax tinggi {s.buy_tax_pct:.0f}%")
        risk += 10
    if s.sell_tax_pct is not None and 10 <= s.sell_tax_pct < 30:
        warnings.append(f"sell tax tinggi {s.sell_tax_pct:.0f}%")
        risk += 15
    if s.owner_can_pause is True and s.owner_renounced is not True:
        warnings.append("owner bisa pause transfer")
        risk += 10
    if s.proxy_upgradeable is True:
        warnings.append("kontrak proxy upgradeable — logic bisa diganti owner")
        risk += 10
    if s.top10_holders_pct is not None and s.top10_holders_pct >= 50:
        warnings.append(f"konsentrasi tinggi: top-10 holder {s.top10_holders_pct:.0f}%")
        risk += 15
    if s.lp_lock_days is not None and 0 < s.lp_lock_days < 30:
        warnings.append(f"LP lock pendek ({s.lp_lock_days:.0f} hari)")
        risk += 10
    if s.age_days is not None and s.age_days < 3:
        warnings.append(f"kontrak sangat baru ({s.age_days:.1f} hari)")
        risk += 10
    if s.liquidity_usd is not None and s.liquidity_usd < 10000:
        warnings.append(f"likuiditas tipis (${s.liquidity_usd:,.0f})")
        risk += 10

    # ---- unknowns (data belum ada → harus dicek, bukan diabaikan) ----
    for attr, label in [
        ("contract_verified", "verifikasi kontrak"), ("is_honeypot", "honeypot test"),
        ("lp_locked", "status LP lock"), ("owner_renounced", "renounce ownership"),
    ]:
        if getattr(s, attr) is None:
            unknowns.append(label)
            risk += 5

    risk = min(100, risk)
    if critical:
        verdict = "DANGER"
        risk = max(risk, 80)
    elif risk >= 40:
        verdict = "CAUTION"
    else:
        verdict = "SAFE"
    return RugVerdict(verdict=verdict, risk_score=risk, critical=critical,
                      warnings=warnings, unknowns=unknowns)


if __name__ == "__main__":
    sig = SignalSet(contract_verified=True, is_honeypot=False, buy_tax_pct=3, sell_tax_pct=4,
                    lp_locked=True, lp_lock_days=180, owner_renounced=True,
                    top10_holders_pct=35, age_days=90, liquidity_usd=250000)
    print(check(sig).report())
    print("---")
    print(check(SignalSet(is_honeypot=True, lp_locked=False)).report())
