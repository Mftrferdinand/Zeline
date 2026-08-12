# sk37 — Anti-Scam Sentinel (SUPERAGENT V7)
# Load when: situs palsu, domain palsu, typosquat, phishing airdrop, scam check, fake claim, drainer
# Category: Web3 & Crypto

## DOCTRINE — defensif, read-only, whitehat
Anggota komunitas yang kena drainer = bencana reputasi. sk37 deteksi domain/situs PALSU yang niru proyek resmi (typosquat + sinyal halaman berbahaya) → skor risiko + draft warning siap broadcast. **Read-only — gak nyerang, gak nge-exploit.**

Beda fokus dari rugcheck/sk11 (proyek itu sendiri scam?) — sk37 jawab "situs INI asli atau imitasi?".

## TOOL (v4.2, net-new)
- `tools/scam_sentinel.py` — `levenshtein()` (edit distance), `analyze(candidate, official, PageSignals)` → `ScamVerdict` (skor 0-100, likely-safe / suspicious / likely-scam, findings). `warning_post(verdict, project)` → draft broadcast siap kirim.

## SINYAL
typosquat (≤2 huruf beda = +45) · substring impersonasi · 🚩 minta seed phrase (+40) · 🚩 pola drainer (+35) · redirect saat connect · SSL umur <14 hari · tombol claim di domain baru. ≥60 = likely-scam, ≥25 = suspicious.

## ALUR STANDAR
```python
from scam_sentinel import analyze, warning_post, PageSignals
v = analyze("zkprot0x.xyz", "zkprotox.xyz",
            PageSignals(asks_seed_phrase=True, ssl_age_days=3))
print(v.report())                       # 🛡️ ... LIKELY-SCAM
print(warning_post(v, "ZkProtoX"))      # draft 🚨 AWAS SCAM siap broadcast
```

## SCOPE & DELEGATION
| Butuh | sk37 | Delegasi |
|---|---|---|
| Konten halaman / umur SSL | konsumsi `PageSignals` | skill `browser` / sk6 |
| Legitimasi proyeknya sendiri | — | rugcheck.py + sk11 + holder_xray |
| Broadcast warning | hasilkan draft | sk4 (telegram) — operator approve dulu |
| Laporan formal | temuan | sk17 responsible disclosure |

## SAFETY RAILS
- Verdict publik (warning post) HANYA setelah operator review — false positive bisa fitnah proyek sah.
- Jangan kunjungi situs scam pakai wallet aktif; analisis via browser sandbox.
- likely-safe ≠ jaminan aman — selalu sertakan DYOR.

🔧 Upgrade: gate wajib sebelum sk35 publish panduan; patroli berkala domain mirip proyek yang lagi difarming.
