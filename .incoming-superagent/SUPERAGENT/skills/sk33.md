# sk33 — Pre-TGE Alpha Radar (SUPERAGENT V7)
# Load when: alpha airdrop, pre-TGE, points program, testnet incentivized, radar airdrop, proyek belum ada token
# Category: Web3 & Crypto

## DOCTRINE — discovery, bukan eligibility
sk31 jawab "wallet gue layak gak buat airdrop X?" — **sk33 jawab "proyek mana yang worth difarming SEKARANG?"** sebelum token-nya ada. Ini alpha mentah: jualan utama komunitas.

Murni logika scoring (offline, deterministik). Pengumpulan sinyal mentah (funding, deploy kontrak, points program, GitHub, governance) didelegasi ke sk6/sk22/sk10.

## TOOL (v4.2, net-new)
- `tools/alpha_radar.py` — `ProjectSignal` → `score_project()` → skor 0-100 + tier (cold/watch/warm/hot) + alasan konkret + estimasi effort. `rank(list)` urutkan banyak proyek.

## SINYAL YANG DINILAI
points program (paling kuat) · testnet incentivized · governance tanpa token · backing VC tier-1 · besar funding · momentum dev (commit/kontrak) · pertumbuhan sosial · timing TGE (6-18 bln sejak raise). **Token udah ada → skor di-cap 30** (peluang airdrop utama lewat).

## ALUR STANDAR
```python
from alpha_radar import ProjectSignal, rank
sigs = [ProjectSignal("ZkProtoX", funded_usd=30e6, points_program=True,
        testnet_live=True, github_commits_30d=80, backed_by_tier1=True,
        days_since_last_round=300, social_growth_pct=60)]
for r in rank(sigs, top=10):
    print(r.report())   # 📡 ZkProtoX: 78/100 (hot) · effort high
```

## SCOPE & DELEGATION
| Butuh | sk33 | Delegasi |
|---|---|---|
| Data funding/sosial/GitHub | konsumsi `ProjectSignal` | sk6 (API) / sk22 (riset) |
| Aktivitas on-chain (deploy/testnet) | konsumsi sinyal | sk10 + hermes |
| Cek wallet sendiri layak | — | sk31 eligibility |
| Eksekusi farming | hasilkan watchlist | sk30 + airdrop_runner (governor) |

## SAFETY RAILS
- Skor = **probabilitas + estimasi**, BUKAN jaminan ada airdrop/cuan. Sampaikan apa adanya.
- Effort tinggi = modal/waktu besar — pasangkan sama sk34 (ROI) sebelum all-in.

🔧 Upgrade: combo sk34 buat hitung ROI kandidat, sk35 buat auto-bikin panduan begitu masuk tier hot.
