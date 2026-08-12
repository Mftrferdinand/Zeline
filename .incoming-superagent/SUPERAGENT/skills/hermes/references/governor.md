# Spend Governor — Circuit Breaker (v4.0)

Rail keras buat operasi yang memindahkan dana. `Confirm before signing` melindungi tx pertama, tapi **bocor saat `auto_confirm=True`** (mode "mint cepat tanpa mikir", airdrop runner multi-wallet, sniping). Governor nutup celah itu: dia jadi gerbang non-negotiable yang dilewati SETIAP tx sebelum broadcast, dan punya kill-switch yang nge-halt semua operasi kalau ada anomali.

Script: `scripts/governor.py`. Dependency: stdlib only (sqlite3).

## Kapan governor jalan

```
SELALU untuk action yang mindahin dana / approve spending:
swap, snipe, bridge, send, NFT buy, DeFi deposit/withdraw, approve.

Tidak perlu untuk: read-only (balance, quote, simulate, monitoring).
```

Tidak ada override. `auto_confirm=True` mematikan PROMPT, bukan governor.

## Yang dicek tiap `authorize(intent)`

| Cek | Default | Hasil kalau lewat |
|---|---|---|
| Sudah disimulasi? | `require_simulation=ON` | **block** |
| Slippage > batas | 5% | **block** |
| Nilai tx > cap per-tx | dari env | **block** |
| Akumulasi harian (per wallet) > cap | dari env | **block** |
| Akumulasi sesi (semua wallet) > cap | dari env | **block** |
| Gas price > N× baseline rolling | 4× | **HALT** (kill-switch trip) |
| Rate tx > N/menit | 12 | **HALT** (runaway protection) |

`block` = tolak 1 tx ini, governor tetap jalan. `HALT` = trip kill-switch, **semua** authorize berikutnya ditolak sampai operator `reset_killswitch()` manual.

## Pola integrasi (wajib di tiap engine yang broadcast)

```python
from governor import SpendGovernor, TxIntent

gov = SpendGovernor()   # limit dari env var

intent = TxIntent(
    wallet=account.address, chain_id=chain_id, action="swap",
    usd_value=usd_est,            # dari price oracle kamu; None → cap USD di-skip
    slippage_pct=slippage,
    gas_price_wei=tx["gasPrice"],
    simulated_ok=sim_result.ok,   # hasil eth_call / fork sim
)

decision = gov.authorize(intent)
if not decision.allowed:
    return {"status": decision.verdict, "reason": decision.summary()}   # JANGAN broadcast

tx_hash = broadcast(signed)       # baru kirim
gov.record(intent, tx_hash)       # update cap setelah sukses
```

Urutan benar: **safety check token → simulate → governor.authorize → (MEV) send → governor.record**. Governor di antara simulasi dan broadcast.

## Env var

```bash
export HERMES_MAX_TX_USD=500          # cap nilai 1 tx
export HERMES_DAILY_CAP_USD=2000      # cap 24 jam per wallet
export HERMES_SESSION_CAP_USD=1000    # cap per sesi (semua wallet)
export HERMES_MAX_SLIPPAGE_PCT=5      # tolak swap di atas ini
export HERMES_MAX_GAS_MULTIPLE=4      # gas spike → halt
export HERMES_MAX_TX_PER_MIN=12       # runaway protection
export HERMES_REQUIRE_SIM=1           # 0 = matikan (TIDAK disarankan)
export HERMES_GOVERNOR_DB=~/.hermes/governor.db
export HERMES_KILLSWITCH=~/.hermes/HALT   # `touch` file ini = halt darurat manual
```

## Kill-switch darurat

Operator bisa nge-halt agent kapan pun, dari shell, tanpa nyentuh kode:

```bash
echo "manual halt: liat tx aneh" > ~/.hermes/HALT
```

Sekali file itu ada (atau anomali nge-trip otomatis), semua `authorize()` return `HALT`. Reset cuma lewat `gov.reset_killswitch()` atau hapus file + flag manual — sengaja bikin friksi biar gak ke-bypass reflek.

## Batasan jujur

- Governor butuh `usd_value` yang akurat dari price oracle kamu. Kalau None, cap berbasis USD di-skip — yang jalan cuma cek slippage/gas/rate/sim. Jangan anggap cap USD aktif kalau oracle-nya belum disambung.
- Governor nahan broadcast, bukan reverse tx yang sudah terkirim. Dia gerbang pre-flight, bukan undo.
