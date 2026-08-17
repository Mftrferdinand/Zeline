# zeline-zenith-z97 — Tokenomics & Unlock Pressure Engine

> zeline-zenith-z97 — Tokenomics & Unlock Pressure Engine — modul Zeline Zenith (sumber: zeline-zenith-z97).

# Load ONLY when: unlock token, vesting, cliff, kalender unlock, tekanan jual, sell pressure, jual sebelum unlock, jadwal vesting, tokenomics, supply unlock, dump unlock

---

## DOCTRINE — supply makro = sinyal exit
Pelengkap exit_planner (m31). zeline-zenith-z97 fokus **tekanan jual makro**: kalender vesting/unlock + prediksi dampak tiap event (nilai unlock vs likuiditas harian) → sinyal "kurangi posisi sebelum unlock besar".

Murni logika (offline, deterministik). `now` WAJIB di-inject (TIME.md) — gak ada fabrikasi waktu. Data unlock/likuiditas mentah didelegasi ke m10/m22.

## TOOL (v4.2, net-new)
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
| Butuh | zeline-zenith-z97 | Delegasi |
|---|---|---|
| Jadwal vesting & supply | konsumsi `UnlockEvent` | m22 (riset) / m10 (on-chain) |
| Harga & volume live | konsumsi `MarketState` | m10 + Zeline |
| Eksekusi jual | hasilkan sinyal | m31 exit_planner + H1 + governor |
| Time source strict | wajib `now` | TIME.md (Layer 1/2) |

## SAFETY RAILS
- Time-sensitive: tanpa Layer 1/2 time di strict mode → tahan, jangan nebak.
- Sinyal = pendukung keputusan, bukan jaminan harga turun/naik.

🔧 Upgrade: combo m14 buat alert H-3 sebelum unlock besar otomatis.
