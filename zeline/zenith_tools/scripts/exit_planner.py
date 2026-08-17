#!/usr/bin/env python3
"""
tools/exit_planner.py — Post-Airdrop Exit Playbook  (v4.2, sk31)

Begitu token landing, banyak yang telat jual / kena dump. Tool ini nyiapin RENCANA
exit terstruktur (split-sell ladder / TWAP-ish, sisihin buat hold, target stable),
bukan eksekusi. Eksekusi mainnet tetap lewat H1 swap + Spend Governor + R9 gate.

Murni perencanaan (math). Output = jadwal tranche + alasan. Gak nyentuh jaringan/dana.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExitTranche:
    pct: float                     # persen dari total alokasi-untuk-jual
    trigger: str                   # "TGE" | "+24h" | "price:2x" | dst
    detail: str = ""

    def line(self) -> str:
        return f"{self.pct:.0f}% @ {self.trigger}" + (f" — {self.detail}" if self.detail else "")


@dataclass
class ExitPlan:
    total_tokens: float
    hold_pct: float
    sell_tokens: float
    tranches: list
    notes: list

    def report(self) -> str:
        lines = [f"📤 exit plan: jual {100 - self.hold_pct:.0f}% ({self.sell_tokens:g} token), "
                 f"hold {self.hold_pct:.0f}%"]
        for i, t in enumerate(self.tranches, 1):
            tok = self.sell_tokens * t.pct / 100.0
            lines.append(f"   {i}. {t.line()} (~{tok:g} token)")
        for n in self.notes:
            lines.append(f"   ⚑ {n}")
        return "\n".join(lines)


# Preset ladder berdasar profil risiko
_PRESETS = {
    "conservative": [
        ExitTranche(50, "TGE", "amankan modal/effort segera saat likuiditas paling tebal"),
        ExitTranche(30, "+24h", "hindari panic-dump jam pertama"),
        ExitTranche(20, "+7d", "sisa di-TWAP seminggu"),
    ],
    "balanced": [
        ExitTranche(30, "TGE", "ambil sebagian saat unlock"),
        ExitTranche(30, "price:2x", "scale-out kalau pump"),
        ExitTranche(40, "+7d", "TWAP sisanya"),
    ],
    "degen": [
        ExitTranche(20, "TGE", "recoup gas/effort"),
        ExitTranche(30, "price:3x", "biarkan jalan dulu"),
        ExitTranche(50, "price:5x_or_-50%", "exit di euforia atau stop-loss"),
    ],
}


def build_plan(total_tokens: float, *, profile: str = "balanced",
               hold_pct: float = 0.0, liquidity_thin: bool = False,
               vesting: bool = False) -> ExitPlan:
    """Bangun rencana exit. profile ∈ {conservative,balanced,degen}.

    hold_pct: persen yang sengaja di-hold (gak dijual).
    liquidity_thin / vesting: nyesuaiin catatan & geser lebih konservatif.
    """
    profile = profile if profile in _PRESETS else "balanced"
    if liquidity_thin and profile == "degen":
        profile = "balanced"        # jangan degen di pool tipis
    tranches = [ExitTranche(t.pct, t.trigger, t.detail) for t in _PRESETS[profile]]
    sell_tokens = total_tokens * (100 - hold_pct) / 100.0

    notes = []
    if liquidity_thin:
        notes.append("LIKUIDITAS TIPIS → pakai limit order / pecah lebih kecil, cek price impact tiap tranche")
    if vesting:
        notes.append("ADA VESTING → align tranche ke jadwal unlock (jangan rencanain jual token yang masih locked)")
    notes.append("eksekusi via H1 swap → WAJIB lewat Spend Governor + simulate; R9 gate buat tx pertama")
    notes.append("set alert harga (sk14) di tiap trigger, jangan pantau manual")

    return ExitPlan(total_tokens=total_tokens, hold_pct=hold_pct,
                    sell_tokens=sell_tokens, tranches=tranches, notes=notes)


if __name__ == "__main__":
    print(build_plan(10000, profile="balanced", hold_pct=20, liquidity_thin=True).report())
