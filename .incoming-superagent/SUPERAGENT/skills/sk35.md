# sk35 — Auto Guide Studio (SUPERAGENT V7)
# Load when: panduan airdrop, tutorial airdrop, guide step by step, artikel airdrop, naskah panduan
# Category: Web3 & Crypto

## DOCTRINE — produk inti AirdropFinder, diotomasi
1 airdrop → panduan step-by-step Bahasa Indonesia rapi + varian ringkas TG/X, referral ke-embed otomatis, konsisten brand. Output naik 10x, kompetitor ketinggalan.

Murni penyusun teks (offline, deterministik). Screenshot/anotasi tiap step didelegasi ke skill `browser`; publish ke sk4/sk14.

## TOOL (v4.2, net-new)
- `tools/guide_studio.py` — `GuideSpec` + `GuideStep` → `build_full_guide()` (Markdown lengkap), `build_short(platform)` (TG/X), `build_bundle()` (semua + daftar `screenshot_jobs` buat skill browser).

## ALUR STANDAR
```python
from guide_studio import GuideSpec, GuideStep, build_bundle
spec = GuideSpec(project="ZkProtoX", chain="Base",
    referral_url="https://zkprotox.xyz/?ref=airdropfinder",
    steps=[GuideStep("Connect wallet", url="https://zkprotox.xyz",
                     note="cek domain!", screenshot_hint="halaman connect"),
           GuideStep("Bridge 0.01 ETH ke Base")])
b = build_bundle(spec)
# b["full_markdown"], b["telegram"], b["x"], b["screenshot_jobs"]
```

## SCOPE & DELEGATION
| Butuh | sk35 | Delegasi |
|---|---|---|
| Ambil screenshot tiap step | hasilkan `screenshot_jobs` | skill `browser` (Playwright) |
| Publish ke channel/blog | hasilkan teks | sk4 (telegram) / sk14 / sk9 (web) |
| Cek legitimasi proyek dulu | — | rugcheck.py + sk11 + sk37 |
| Adaptasi multi-platform lain | varian dasar | sk40 omni-repurpose |

## SAFETY RAILS
- SELALU sisipkan reminder verifikasi domain & "jangan kasih seed phrase" (template udah ada).
- Disclaimer DYOR otomatis di footer — airdrop spekulatif.
- Cek proyek lewat sk37/rugcheck SEBELUM publish panduan (jangan promosiin scam).

🔧 Upgrade: rangkai sk37 (anti-scam gate) → sk35 (guide) → sk40 (repurpose) → sk14 (auto-publish) jadi 1 pipeline.
