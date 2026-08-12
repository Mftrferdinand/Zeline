# sk31 — Airdrop Intelligence (SUPERAGENT V7)
# Load when: eligibility airdrop, skor airdrop, layak airdrop, sybil, claim window, jadwal claim, exit plan
# Category: Web3 & Crypto

## DOCTRINE — keputusan airdrop = data, bukan feeling
sk31 ngubah siklus airdrop jadi keputusan ber-angka di 4 tahap:
**(1) FARMING** → eligibility scorer + sybil self-audit
**(2) MENJELANG TGE** → claim-window watcher (kalender)
**(3) SETELAH TGE** → exit planner
Semua tool di sini **murni logika (offline, deterministik)** — pengambilan data on-chain didelegasi ke sk10/hermes. Time SELALU di-inject (TIME.md).

## TOOLS (v4.2, net-new)
- `tools/eligibility.py` — `WalletStats` → `score_wallet()` → skor 0-100 + band + **gaps konkret** (apa yang kurang) + flags (dormant, no-bridge). Rubric default bisa di-override per-project (`Criterion`).
- `tools/sybil_audit.py` — `audit(list[WalletActivity])` → risiko cluster antar-**wallet sendiri** (funding seragam, timing berdempet, gas/tx kembar, overlap kontrak) + **saran de-correlation**. Jaring keselamatan operator, BUKAN buat ngecoh proyek orang.
- `tools/claim_watcher.py` — `ClaimWatcher` + `AirdropEvent` → `due(now)` (alert H-48/H-2/H-0, fire-once) & `upcoming(now)` (kalender). `now` WAJIB di-inject.
- `tools/exit_planner.py` — `build_plan()` → exit ladder (conservative/balanced/degen) + nyesuaiin likuiditas tipis/vesting. Cuma RENCANA — eksekusi via H1 swap + Spend Governor.

## ALUR STANDAR
```python
# 1. Layak gak? (data wallet dari sk10/hermes RPC, lalu skor offline)
from eligibility import WalletStats, score_wallet
print(score_wallet(WalletStats(tx_count=22, age_days=120, volume_usd=4200,
      unique_contracts=9, distinct_chains=2, active_weeks=7, bridged=True)).report())

# 2. Multi-wallet kebaca sybil gak? (SEBELUM eksekusi paralel)
from sybil_audit import audit, WalletActivity
r = audit([WalletActivity(...), WalletActivity(...)])   # wallet milik sendiri
if r.risk == "high": ...  # ikutin r.advice buat de-correlate

# 3. Kapan claim? (time di-inject dari [RUNTIME CONTEXT])
from claim_watcher import ClaimWatcher, AirdropEvent
w = ClaimWatcher(store="~/.superagent/airdrops.json")
w.add(AirdropEvent("ProjectX","claim_open", ts=..., chain="Base"))
for a in w.due(now): notify(a.message)        # H-48 / H-2 / saat kejadian

# 4. Setelah landing → exit terstruktur (eksekusi tetap via governor)
from exit_planner import build_plan
print(build_plan(10000, profile="balanced", hold_pct=20, liquidity_thin=True).report())
```

## SCOPE & DELEGATION
| Butuh | sk31 | Delegasi |
|---|---|---|
| Data on-chain wallet (balance/tx/age) | konsumsi `WalletStats` | sk10 + hermes (RPC/explorer) |
| Legitimasi project (rug/honeypot) | — | sk11 / `tools/rugcheck.py` + H4 |
| Eksekusi farming multi-wallet | input ke audit | sk30 + `airdrop_runner` (WAJIB `governor.py`) |
| Eksekusi exit (jual) | hasilkan rencana | H1 swap + Spend Governor + R9 gate |
| Alert delivery (telegram/dll) | hasilkan pesan | sk14 / sk4 |

## SAFETY RAILS
- Sybil-audit = **buat wallet sendiri** (selamatin effort/dana dari sybil filter). Bukan alat menyerang sistem orang.
- Eksekusi dana apa pun (claim/swap/exit) lewat **Spend Governor** — gak ada bypass.
- Time-sensitive (claim/vesting): kalau gak ada Layer 1/2 time source di strict mode → tahan, jangan nebak (TIME.md).
- Eligibility/exit = estimasi pendukung keputusan, **bukan jaminan** dapat/cuan. Sampaikan apa adanya.

## QUICKSTART
```bash
python3 tools/tests/run_tests.sh   # eligibility/sybil/claim/exit semuanya hijau (offline)
```
🔧 Upgrade: combo sk14 buat auto-alert claim harian, sk5 buat export skor multi-wallet ke XLSX.
