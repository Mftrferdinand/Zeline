# Universal Smart Contract Reader (v4.0)

Baca **kontrak apa pun** di banyak chain EVM tanpa nulis engine khusus. Read-only sepenuhnya — gak ada tx, gak ada dana keluar, jadi gak lewat governor.

Script: `scripts/contract_reader.py`. Dependency: web3, httpx.

## Kapabilitas

- **Fetch ABI otomatis** — Sourcify (keyless) → fallback Blockscout. Gak perlu API key explorer.
- **Panggil fungsi read apa pun** (view/pure), output di-decode web3.
- **List semua fungsi read** dari ABI.
- **Deteksi standar** — ERC-20/721/1155 via ERC-165 on-chain + heuristik ABI.
- **Resolve proxy** (EIP-1967) → alamat implementasi sebenarnya (penting: ABI proxy ≠ logika asli).
- **Info token cepat** — name/symbol/decimals/totalSupply.

## Chain didukung

ethereum, bsc, polygon, base, arbitrum, optimism, avalanche, linea, scroll, zksync (id ada di `CHAINS`). Chain lain: kasih `rpc_url` manual — Sourcify dukung semua chainId standar. Non-EVM (Solana/Sui/Aptos/TON) modelnya beda (program/IDL) → lihat `wallets.md`.

## Pakai

```python
from contract_reader import ContractReader

c = ContractReader("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", chain_id=1)  # USDC
print(c.summary())
# baca fungsi spesifik:
print(c.read("balanceOf", "0x...wallet..."))
print(c.token_info())          # {'name': 'USD Coin', 'symbol': 'USDC', 'decimals': 6, ...}
print(c.detect_standard())     # ['ERC20']
print(c.resolve_proxy())       # alamat implementasi kalau ini proxy
```

ABI gak ketemu (kontrak belum verified)? Kasih `abi=[...]` manual:

```python
c = ContractReader(addr, chain_id, abi=my_abi)
```

## Catatan

- **Proxy**: banyak kontrak modern itu proxy. `resolve_proxy()` ngasih alamat implementasi — fungsi read sebenarnya ada di sana. Selalu cek ini buat kontrak yang ABI-nya kelihatan minim.
- **Verified-at-build**: endpoint Sourcify/Blockscout & RPC publik bisa berubah; pas implement cek yang current. RPC publik rate-limited — sambung ke `RPCRouter` (monitoring.py) buat failover.
- Ini fondasi buat "universal contract interactor" (write/call). Versi write (kirim tx ke fungsi apa pun) itu fitur terpisah dan WAJIB lewat governor — reader ini cuma baca.
