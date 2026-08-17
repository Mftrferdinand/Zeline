# zeline-zenith-z96 — Auto Guide Studio (panduan airdrop otomatis)

> zeline-zenith-z96 — Auto Guide Studio (panduan airdrop otomatis) — modul Zeline Zenith (sumber: zeline-zenith-z96).

# Load ONLY when: bikin panduan airdrop, tutorial airdrop, guide step by step, panduan lengkap, artikel airdrop, postingan tutorial, panduan + screenshot, naskah panduan, embed referral

---

## DOCTRINE — produk inti AirdropFinder, diotomasi
1 airdrop → panduan step-by-step Bahasa Indonesia rapi + varian ringkas TG/X, referral ke-embed otomatis, konsisten brand. Output naik 10x, kompetitor ketinggalan.

Murni penyusun teks (offline, deterministik). Screenshot/anotasi tiap step didelegasi ke skill `browser`; publish ke m4/m14.

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
| Butuh | zeline-zenith-z96 | Delegasi |
|---|---|---|
| Ambil screenshot tiap step | hasilkan `screenshot_jobs` | skill `browser` (Playwright) |
| Publish ke channel/blog | hasilkan teks | m4 (telegram) / m14 / m9 (web) |
| Cek legitimasi proyek dulu | — | rugcheck.py + m11 + m37 |
| Adaptasi multi-platform lain | varian dasar | m40 omni-repurpose |

## SAFETY RAILS
- SELALU sisipkan reminder verifikasi domain & "jangan kasih seed phrase" (template udah ada).
- Disclaimer DYOR otomatis di footer — airdrop spekulatif.
- Cek proyek lewat m37/rugcheck SEBELUM publish panduan (jangan promosiin scam).

🔧 Upgrade: rangkai m37 (anti-scam gate) → zeline-zenith-z96 (guide) → m40 (repurpose) → m14 (auto-publish) jadi 1 pipeline.
