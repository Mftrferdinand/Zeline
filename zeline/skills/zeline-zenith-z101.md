# zeline-zenith-z101 — Omni-Repurpose Engine (1 konten → semua platform)

> zeline-zenith-z101 — Omni-Repurpose Engine (1 konten → semua platform) — modul Zeline Zenith (sumber: zeline-zenith-z101).

# Load ONLY when: repurpose konten, ubah jadi thread, carousel IG, script tiktok, script youtube, multi platform, distribusi konten, konversi format konten, sekali bikin posting semua

---

## DOCTRINE — distribusi > produksi
Konten bagus yang cuma tayang di 1 platform = sia-sia. zeline-zenith-z101 ubah 1 sumber → X thread + post TG + carousel IG + script TikTok + script YouTube sekaligus, lokal ID, konsisten brand, dengan batasan tiap platform (280 char X, slide IG, durasi video).

Murni penyusun teks (offline, deterministik). Pelengkap m27 (kalender/strategi) — zeline-zenith-z101 fokus TRANSFORMASI format. Publish didelegasi ke m4/m14.

## TOOL (v4.2, net-new)
- `tools/repurpose.py` — `SourceContent(title, key_points, cta, referral_url, hashtags)` → `to_x_thread()` (≤280/tweet), `to_telegram()`, `to_ig_carousel()` (cover+poin+CTA), `to_tiktok_script()` (hook/body/cta + estimasi detik), `to_youtube_script()` (intro/segmen/outro), `repurpose_all()`.

## ALUR STANDAR
```python
from repurpose import SourceContent, repurpose_all
src = SourceContent(title="3 Airdrop Base Worth Difarming",
    key_points=["ZkProtoX points aktif", "BaseSwap volume tinggi"],
    cta="Cek panduan di channel.", referral_url="https://airdropfinder.id",
    hashtags=["airdrop", "base"])
out = repurpose_all(src)   # x_thread / telegram / ig_carousel / tiktok / youtube
```

## SCOPE & DELEGATION
| Butuh | zeline-zenith-z101 | Delegasi |
|---|---|---|
| Sumber konten (panduan) | konsumsi `SourceContent` | m35 guide / m39 insight |
| Hook pembuka terbaik | pakai default | m42 hook lab (skor + varian) |
| Render video beneran | hasilkan script | m41 + skill remotion_video |
| Publish/jadwal | hasilkan teks | m4 / m14 / m27 kalender |

## SAFETY RAILS
- Semua varian bawa disclaimer/DYOR yang sama dengan sumber — jangan hilang pas dipotong.
- Cek batas karakter kehormat (clip otomatis udah ada, tapi review hasil clip).

🔧 Upgrade: pipeline penuh m35 → m42 (pilih hook) → zeline-zenith-z101 (semua format) → m41 (video) → m4 (publish).
