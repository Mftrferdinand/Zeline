# sk40 — Omni-Repurpose Engine (SUPERAGENT V7)
# Load when: repurpose konten, ubah jadi thread, carousel IG, multi platform, script tiktok, distribusi konten
# Category: Content & Marketing

## DOCTRINE — distribusi > produksi
Konten bagus yang cuma tayang di 1 platform = sia-sia. sk40 ubah 1 sumber → X thread + post TG + carousel IG + script TikTok + script YouTube sekaligus, lokal ID, konsisten brand, dengan batasan tiap platform (280 char X, slide IG, durasi video).

Murni penyusun teks (offline, deterministik). Pelengkap sk27 (kalender/strategi) — sk40 fokus TRANSFORMASI format. Publish didelegasi ke sk4/sk14.

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
| Butuh | sk40 | Delegasi |
|---|---|---|
| Sumber konten (panduan) | konsumsi `SourceContent` | sk35 guide / sk39 insight |
| Hook pembuka terbaik | pakai default | sk42 hook lab (skor + varian) |
| Render video beneran | hasilkan script | sk41 + skill remotion_video |
| Publish/jadwal | hasilkan teks | sk4 / sk14 / sk27 kalender |

## SAFETY RAILS
- Semua varian bawa disclaimer/DYOR yang sama dengan sumber — jangan hilang pas dipotong.
- Cek batas karakter kehormat (clip otomatis udah ada, tapi review hasil clip).

🔧 Upgrade: pipeline penuh sk35 → sk42 (pilih hook) → sk40 (semua format) → sk41 (video) → sk4 (publish).
