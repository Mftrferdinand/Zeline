# sk38 — Contract-Change & Claim-Address Watcher (SUPERAGENT V7)
# Load when: kontrak berubah, proxy upgrade, claim address, aman claim gak, watch kontrak
# Category: Web3 & Crypto

## DOCTRINE — momen claim = momen rawan drainer
Modus drainer paling sering: kontrak/claim address diganti diam-diam pas hype claim. sk38 diff dua snapshot kontrak → alert berbobot (info/warning/critical) → keputusan `safe_to_claim`. **Read-only & defensif.**

Pengambilan snapshot on-chain (impl/admin/code hash/ABI) didelegasi ke H9 contract_read + sk10. Tool ini CUMA diff — deterministik, offline.

## TOOL (v4.2, net-new)
- `tools/contract_watch.py` — `ContractSnapshot` → `diff(prev, curr)` → `WatchResult` (alerts + `max_severity` + `changed`). `safe_to_claim(prev, curr)` = shortcut (False kalau ada critical). `SENSITIVE_FUNCS` = set fungsi berbahaya (setClaimAddress, upgradeTo, withdrawAll, dst).

## ALERT MAP
- **critical**: claim address berubah · proxy implementation di-upgrade · fungsi sensitif baru · code hash berubah (non-proxy)
- **warning**: admin/owner berubah · code hash berubah (proxy)
- **info**: fungsi baru non-sensitif

## ALUR STANDAR
```python
from contract_watch import ContractSnapshot, diff, safe_to_claim
prev = ContractSnapshot("0xAbc", impl_address="0x111", claim_address="0xC1",
                        code_hash="h1", functions=["claim"])
curr = ContractSnapshot("0xAbc", impl_address="0x222", claim_address="0xC2",
                        code_hash="h2", functions=["claim", "setClaimAddress"])
print(diff(prev, curr).report())     # 🚨 critical alerts
print(safe_to_claim(prev, curr))     # False → JANGAN claim
```

## SCOPE & DELEGATION
| Butuh | sk38 | Delegasi |
|---|---|---|
| Snapshot impl/admin/code hash | konsumsi snapshot | H9 contract_read + sk10 |
| Verifikasi pengumuman resmi | — | sk22 + kanal resmi proyek |
| Eksekusi claim | gate `safe_to_claim` | airdrop_runner + Spend Governor |
| Broadcast warning komunitas | temuan | sk37 warning_post + sk4 |

## SAFETY RAILS
- `safe_to_claim == False` → STOP claim, verifikasi via kanal resmi dulu. No exceptions.
- Perubahan sah (upgrade resmi) tetap butuh konfirmasi pengumuman proyek sebelum lanjut.
- Simpan snapshot berkala (sebelum & saat claim window) biar diff bermakna.

🔧 Upgrade: cron snapshot harian kontrak yang difarming + auto-alert ke operator via sk14.
