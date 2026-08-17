# Pre-TGE Alpha Radar [Zeline Zenith]

> Pre-TGE Alpha Radar — modul Zeline Zenith (sumber: zeline-zenith-z33).

# Load when: alpha airdrop, pre-TGE, points program, testnet incentivized, radar airdrop, proyek belum ada token
# Category: Web3 & Crypto

## DOCTRINE — discovery, bukan eligibility
z31 jawab "wallet gue layak gak buat airdrop X?" — **z33 jawab "proyek mana yang worth difarming SEKARANG?"** sebelum token-nya ada. Ini alpha mentah: jualan utama komunitas.

Murni logika scoring (offline, deterministik). Pengumpulan sinyal mentah (funding, deploy kontrak, points program, GitHub, governance) didelegasi ke z6/z22/z10.

## TOOL (v4.2, net-new)
**Blueprint:** `alpha_radar.py` and the snippet below are pseudocode for a
component to implement and test in the target project.
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
| Butuh | z33 | Delegasi |
|---|---|---|
| Data funding/sosial/GitHub | konsumsi `ProjectSignal` | zeline-zenith-z6 (API) / zeline-zenith-z22 (riset) |
| Aktivitas on-chain (deploy/testnet) | konsumsi sinyal | z10 plus available on-chain tools |
| Cek wallet sendiri layak | — | z31 eligibility |
| Eksekusi farming | hasilkan watchlist | implemented runner plus spend governor |

## SAFETY RAILS
- Skor = **probabilitas + estimasi**, BUKAN jaminan ada airdrop/cuan. Sampaikan apa adanya.
- Effort tinggi = modal/waktu besar — pasangkan sama zeline-zenith-z34 (ROI) sebelum all-in.

🔧 Upgrade: combo z34 buat hitung ROI kandidat, z35 buat auto-bikin panduan begitu masuk tier hot.
