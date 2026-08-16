# Universal Contract Interactor: WRITE (v4.0)

Pasangan write dari `contract_read.md`. Kirim tx ke fungsi **apa pun** di kontrak mana pun, dengan ABI auto-fetch. **Mindahin dana → wajib lewat gerbang penuh.** Gak ada mode "tembak langsung".

Script: `scripts/contract_writer.py`. Dependency: web3, httpx. Reuse: contract_reader, governor, web3_connect, mev.

## Dua versi (read vs write)

| | reader (`contract_read.md`) | writer (ini) |
|---|---|---|
| Aksi | baca state | ubah state (kirim tx) |
| Dana | gak ada | bisa keluar |
| Gerbang | gak perlu | **governor + sim + konfirmasi (wajib)** |

## Gerbang (tidak bisa di-skip)

```
build tx → SIMULATE (eth_call) → screen_tx (decode human-readable)
         → governor.authorize (cap per-tx/harian/sesi + kill-switch)
         → konfirmasi operator → sign → send (private/MEV opsional)
         → governor.record
```

Salah satu gagal/diblok → STOP, gak broadcast. Ini beda fundamental dari reader: writer ngeluarin dana, jadi rail-nya non-negotiable.

## Pakai

```python
from contract_writer import ContractWriter

w = ContractWriter("0x...contract...", chain_id=8453, account=acct)
print(w.writable_functions())   # list fungsi yang ngubah state

async def confirm(info):
    print(info["call"], info["screen"], info["decision"]); return True

res = await w.call("stake", amount_wei,
                   value=0, usd_value=usd_est,   # usd_value buat cap governor
                   confirm_cb=confirm, private=True)   # private=True → lewat MEV relay
print(res.status, res.tx_hash)   # "sent" / "blocked" / "halt" / "sim_failed" / ...
```

## Catatan

- **Proxy**: kalau target proxy, fungsi write ada di implementasi — pakai `contract_reader.resolve_proxy()` dulu buat tau ABI yang bener.
- **usd_value**: dari price oracle lo. Tanpa itu, cap USD governor di-skip (cek slippage/gas/rate/sim tetap jalan).
- **private=True**: broadcast lewat Flashbots/MEV Blocker (anti-frontrun). Default public.
- Ini "universal interactor" — bisa manggil fungsi apa pun, jadi governor + konfirmasi itu yang ngejaga lo dari kontrak jahat / fungsi yang gak lo paham. Selalu baca `screen` (human-readable) sebelum approve.
