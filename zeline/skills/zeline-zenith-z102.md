# zeline-zenith-z102 — Video Script-to-Screen Pipeline

> zeline-zenith-z102 — Video Script-to-Screen Pipeline — modul Zeline Zenith (sumber: zeline-zenith-z102).

# Load ONLY when: bikin video, script video, storyboard, voiceover, subtitle, srt, tiktok video, reels, youtube video, video airdrop, naskah video

---

## DOCTRINE — video pendek = mesin growth terbesar
Topik crypto → paket video utuh dalam sekali jalan: script ber-timing (hook 15% / body / CTA 15%), storyboard shot-by-shot, baris voiceover ID (siap TTS), subtitle SRT. Operator tinggal render & post.

Murni penyusun (offline, deterministik). Render aktual didelegasi ke skill `remotion_video`; TTS ke `voice.py`/general_tools.

## TOOL (v4.2, net-new)
- `tools/video_pipeline.py` — `VideoBrief(topic, key_points, platform, duration_sec, cta)` → `build_segments()` (timeline kontigu hook→points→cta), `to_srt()` (subtitle valid), `build_package()` (script + storyboard + voiceover_lines + srt + meta).

## ALUR STANDAR
```python
from video_pipeline import VideoBrief, build_package
brief = VideoBrief(topic="3 Airdrop Base Worth Difarming",
    key_points=["ZkProtoX points", "BaseSwap volume", "Aerodrome LP"],
    platform="tiktok", duration_sec=45)
pkg = build_package(brief)
# pkg["script"] / pkg["storyboard"] / pkg["voiceover_lines"] / pkg["srt"]
```

## STRUKTUR TIMELINE
hook 15% durasi (stop-scroll) → poin dibagi rata → CTA 15%. Floor 10 detik. Timestamps kontigu, SRT format `HH:MM:SS,mmm`.

## SCOPE & DELEGATION
| Butuh | zeline-zenith-z102 | Delegasi |
|---|---|---|
| Hook pembuka terkuat | default template | m42 hook lab |
| Bahan konten | konsumsi brief | m35 / m39 / m40 |
| Render video final | hasilkan package | skill `remotion_video` |
| TTS voiceover | hasilkan lines | voice.py / general_tools |
| Upload/publish | — | m4 / m14 / operator |

## SAFETY RAILS
- Konten video tetap bawa disclaimer DYOR (masukin di CTA/deskripsi).
- Klaim angka di script harus terverifikasi (m22) sebelum render — jangan sebar estimasi sebagai fakta.

🔧 Upgrade: auto-pilih hook via m42, batch render mingguan dari kalender m27.
