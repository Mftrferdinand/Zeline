# Farming Portfolio & ROI Optimizer [Zeline Zenith]

> Farming Portfolio & ROI Optimizer — modul Zeline Zenith (sumber: zeline-zenith-z34).

# Load when: ROI farming, portfolio airdrop, untung rugi farming, gas vs hasil, farm mana lanjut
# Category: Web3 & Crypto

## DOCTRINE — farming = keputusan ber-angka
Berhenti nebak. Tiap posisi farming dihitung: biaya (gas) vs expected value (estimasi airdrop × keyakinan + realisasi) → ROI → aksi keep/trim/drop. Deteksi wallet nganggur yang cuma bakar gas.

Blueprint logika offline/deterministik. Data mentah datang dari on-chain tools;
`cost_ledger.py` and `farm_roi.py` are pseudocode components to implement.

## TOOL (v4.2, net-new)
The module/import example below is not bundled or directly runnable.
- `tools/farm_roi.py` — `FarmPosition` → `evaluate()` → `PositionVerdict` (cost, EV, net, ROI, action, notes). `analyze(list)` → `PortfolioSummary` (agregat + ranking by net + grouping per aksi).

## ALUR STANDAR
```python
from farm_roi import FarmPosition, analyze
port = [FarmPosition("LayerZero", gas_spent_usd=120, est_airdrop_usd=2000,
        confidence=0.6, last_activity_days=3),
        FarmPosition("DeadFarm", gas_spent_usd=80, est_airdrop_usd=50,
        confidence=0.1, last_activity_days=60)]
print(analyze(port).report())   # ranking + keep/trim/drop + idle warning
```

## LOGIKA AKSI
- ROI ≥ 1 → **keep** · net>0 tapi ROI<1 → **trim** · net<0 → **drop**
- realized>0 & est=0 → **claim-soon/trim** · idle >30 hari tanpa realisasi → flag

## SCOPE & DELEGATION
| Butuh | z34 | Delegasi |
|---|---|---|
| Gas terpakai / modal terkunci on-chain | konsumsi `FarmPosition` | z10 plus available on-chain tools and cost_ledger |
| Estimasi nilai airdrop | input `est_airdrop_usd` | zeline-zenith-z33 (alpha) / zeline-zenith-z22 (riset) |
| Eksekusi exit dari posisi | hasilkan aksi | z31 exit_planner + H1 + governor |

## SAFETY RAILS
- EV pakai `confidence` (0-1) — estimasi pendukung, bukan janji cuan.
- Keputusan drop = rekomendasi; operator tetap pegang kendali (R9 kalau ada konsekuensi dana).

🔧 Upgrade: feed `est_airdrop_usd` dari z33, export ranking ke XLSX via z5/z8, auto-reminder task harian per posisi via z14.
