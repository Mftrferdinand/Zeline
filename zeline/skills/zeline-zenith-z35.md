# Auto Guide Studio [Zeline Zenith]

> Auto Guide Studio — modul Zeline Zenith (sumber: zeline-zenith-z35).

# Load when: panduan airdrop, tutorial airdrop, guide step by step, artikel airdrop, naskah panduan
# Category: Web3 & Crypto

## DOCTRINE — produk inti AirdropFinder, diotomasi
1 airdrop → panduan step-by-step Bahasa Indonesia rapi + varian ringkas TG/X, referral ke-embed otomatis, konsisten brand. Output naik 10x, kompetitor ketinggalan.

Murni penyusun teks (offline, deterministik). Screenshot/anotasi tiap step didelegasi ke skill `browser`; publish ke z4/z14.

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
| Butuh | z35 | Delegasi |
|---|---|---|
| Ambil screenshot tiap step | hasilkan `screenshot_jobs` | skill `browser` (Playwright) |
| Publish ke channel/blog | hasilkan teks | zeline-zenith-z4 (telegram) / z14 / zeline-zenith-z9 (web) |
| Cek legitimasi proyek dulu | — | rugcheck.py + z11 + z37 |
| Adaptasi multi-platform lain | varian dasar | z40 omni-repurpose |

## SAFETY RAILS
- SELALU sisipkan reminder verifikasi domain & "jangan kasih seed phrase" (template udah ada).
- Disclaimer DYOR otomatis di footer — airdrop spekulatif.
- Cek proyek lewat z37/rugcheck SEBELUM publish panduan (jangan promosiin scam).

🔧 Upgrade: rangkai zeline-zenith-z37 (anti-scam gate) → zeline-zenith-z35 (guide) → zeline-zenith-z40 (repurpose) → zeline-zenith-z14 (auto-publish) jadi 1 pipeline.
