# Tokenomics & Unlock Pressure Engine [Zeline Zenith]

> Tokenomics & Unlock Pressure Engine — modul Zeline Zenith (sumber: zeline-zenith-z36).

# Load when: unlock token, vesting, cliff, kalender unlock, sell pressure, tokenomics, jual sebelum unlock
# Category: Web3 & Crypto

## DOCTRINE — supply makro = sinyal exit
Pelengkap exit_planner (z31). z36 fokus **tekanan jual makro**: kalender vesting/unlock + prediksi dampak tiap event (nilai unlock vs likuiditas harian) → sinyal "kurangi posisi sebelum unlock besar".

Murni logika (offline, deterministik). `now` WAJIB di-inject (TIME.md) — gak ada fabrikasi waktu. Data unlock/likuiditas mentah didelegasi ke z10/z22.

## TOOL (v4.2, net-new)
**Blueprint:** `unlock_engine.py` and the snippet below are pseudocode for a
component to implement and test in the target project.
- `tools/unlock_engine.py` — `UnlockEvent` + `MarketState` → `assess_event(ev, mkt, now)` → `UnlockVerdict` (days_until, unlock value, % circulating, pressure_ratio, pressure low/medium/high/extreme, signal). `build_calendar()` urut tanggal; `biggest_pressure()` cari event terberat.

## ALUR STANDAR
```python
from unlock_engine import UnlockEvent, MarketState, build_calendar
# now di-inject dari [RUNTIME CONTEXT]
mkt = MarketState(price_usd=2.0, total_supply=1e9, circulating_supply=2e8,
                  daily_volume_usd=5e6)
evs = [UnlockEvent("Investor cliff", now + 10*86400, 8.0)]
for v in build_calendar(evs, mkt, now):
    print(v.report())   # 🔓 ... pressure extreme — kurangi posisi DULUAN
```

## METRIK
- `pressure_ratio` = nilai unlock / volume harian → extreme ≥3x, high ≥1x, medium ≥0.3x.
- `pct_of_circulating` = berapa % beredar yang membengkak.

## SCOPE & DELEGATION
| Butuh | z36 | Delegasi |
|---|---|---|
| Jadwal vesting & supply | konsumsi `UnlockEvent` | zeline-zenith-z22 (riset) / zeline-zenith-z10 (on-chain) |
| Harga & volume live | konsumsi `MarketState` | z10 plus available on-chain tools |
| Eksekusi jual | hasilkan sinyal | z31 exit_planner + H1 + governor |
| Time source strict | wajib `now` | TIME.md (Layer 1/2) |

## SAFETY RAILS
- Time-sensitive: tanpa Layer 1/2 time di strict mode → tahan, jangan nebak.
- Sinyal = pendukung keputusan, bukan jaminan harga turun/naik.

🔧 Upgrade: combo z14 buat alert H-3 sebelum unlock besar otomatis.
