#!/usr/bin/env python3
"""
tools/farm_roi.py — Farming Portfolio & ROI Optimizer  (v4.2, sk34)

Ubah farming airdrop dari nebak jadi keputusan ber-angka. Lacak tiap posisi farming
(gas + modal terkunci vs estimasi/realisasi nilai airdrop) → ROI, ranking
keep/trim/drop, deteksi wallet nganggur yang cuma bakar gas.

Murni logika (offline, deterministik). Data biaya/aktivitas mentah didelegasi ke
sk10/hermes (on-chain) + cost_ledger.py. Zero-dep (dataclasses).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FarmPosition:
    project: str
    wallets: int = 1
    gas_spent_usd: float = 0.0       # total gas yang udah dibakar
    capital_locked_usd: float = 0.0  # modal terkunci (bridge/LP/stake)
    est_airdrop_usd: float = 0.0     # estimasi nilai airdrop (dari sk33/riset)
    realized_usd: float = 0.0        # yang udah benar-benar cair
    hours_invested: float = 0.0      # waktu operator (buat ROI/jam)
    last_activity_days: float = 0.0  # hari sejak aktivitas terakhir
    confidence: float = 0.5          # 0-1 keyakinan airdrop kejadian


@dataclass
class PositionVerdict:
    project: str
    cost_usd: float                  # gas (modal dianggap recoverable)
    expected_value_usd: float        # est_airdrop * confidence + realized
    net_usd: float
    roi: float                       # net / cost (0 cost → inf-safe)
    action: str                      # keep | trim | drop | claim-soon
    notes: list = field(default_factory=list)

    def report(self) -> str:
        roi_s = "∞" if self.roi == float("inf") else f"{self.roi:.2f}x"
        line = (f"💼 {self.project}: EV ${self.expected_value_usd:,.0f} − cost "
                f"${self.cost_usd:,.0f} = net ${self.net_usd:,.0f} (ROI {roi_s}) → {self.action.upper()}")
        for n in self.notes:
            line += f"\n   • {n}"
        return line


def evaluate(p: FarmPosition) -> PositionVerdict:
    """Hitung EV, ROI, dan rekomendasi aksi untuk satu posisi farming."""
    cost = max(0.0, p.gas_spent_usd)
    ev = p.est_airdrop_usd * max(0.0, min(1.0, p.confidence)) + p.realized_usd
    net = ev - cost
    roi = (net / cost) if cost > 0 else (float("inf") if net > 0 else 0.0)

    notes = []
    # idle wallet detection
    if p.last_activity_days > 30 and p.realized_usd == 0:
        notes.append(f"nganggur {p.last_activity_days:.0f} hari — banyak project gugurin wallet pasif")
    if p.confidence < 0.3:
        notes.append("keyakinan airdrop rendah — pertimbangkan stop kalau gas naik")
    if p.capital_locked_usd > 0 and p.est_airdrop_usd < p.capital_locked_usd * 0.05:
        notes.append("est airdrop < 5% modal terkunci — opportunity cost tinggi")

    # action decision
    if p.realized_usd > 0 and p.est_airdrop_usd == 0:
        action = "claim-soon" if p.realized_usd > 0 and p.last_activity_days < 1 else "trim"
    elif roi == float("inf") or roi >= 3:
        action = "keep"
    elif roi >= 1:
        action = "keep"
    elif net > 0:
        action = "trim"
    else:
        action = "drop"

    return PositionVerdict(project=p.project, cost_usd=round(cost, 2),
                           expected_value_usd=round(ev, 2), net_usd=round(net, 2),
                           roi=roi, action=action, notes=notes)


@dataclass
class PortfolioSummary:
    total_cost_usd: float
    total_ev_usd: float
    total_net_usd: float
    by_action: dict
    verdicts: list

    def report(self) -> str:
        lines = [f"📊 Portfolio: EV ${self.total_ev_usd:,.0f} − cost "
                 f"${self.total_cost_usd:,.0f} = net ${self.total_net_usd:,.0f}"]
        lines.append("   aksi: " + ", ".join(f"{k}={len(v)}" for k, v in self.by_action.items()))
        for v in self.verdicts:
            lines.append(v.report())
        return "\n".join(lines)


def analyze(positions: list) -> PortfolioSummary:
    """Evaluasi semua posisi, ranking by net value desc, agregasi per aksi."""
    verdicts = sorted((evaluate(p) for p in positions),
                      key=lambda v: v.net_usd, reverse=True)
    by_action: dict = {}
    for v in verdicts:
        by_action.setdefault(v.action, []).append(v.project)
    return PortfolioSummary(
        total_cost_usd=round(sum(v.cost_usd for v in verdicts), 2),
        total_ev_usd=round(sum(v.expected_value_usd for v in verdicts), 2),
        total_net_usd=round(sum(v.net_usd for v in verdicts), 2),
        by_action=by_action, verdicts=verdicts)


if __name__ == "__main__":
    port = [
        FarmPosition("LayerZero", wallets=5, gas_spent_usd=120, capital_locked_usd=500,
                     est_airdrop_usd=2000, confidence=0.6, hours_invested=10, last_activity_days=3),
        FarmPosition("DeadFarm", wallets=2, gas_spent_usd=80, est_airdrop_usd=50,
                     confidence=0.1, last_activity_days=60),
    ]
    print(analyze(port).report())
