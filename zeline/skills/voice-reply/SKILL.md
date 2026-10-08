---
name: voice-reply
description: |
  Balas pakai voice note (VN) suara cewe anime bahasa Indonesia. Kini
  ini fitur CORE, bukan sekadar script: gateway Telegram otomatis transcribe
  VN masuk dan bisa membalas dengan VN per-chat (/voice mirror, /voice always).
  Load saat user minta "balas pakai suara", "bales vn", "suara anime",
  "voice reply", atau kirim VN dan mau dibalas VN.
metadata:
  zeline:
    tags: [voice, vn, tts, edge-tts, anime, indonesia, audio, mirror]
    category: messaging
---

# Voice Reply — balas VN dengan suara cewe anime (Indonesia)

**Status: fitur core** (`zeline/voice.py` + `zeline/voice_prefs.py`). Skill ini
tinggal dokumentasi + CLI wrapper; tidak ada logika ganda.

## Cara pakai (per chat, via Telegram)

| Perintah | Efek |
|---|---|
| `/voice` | lihat mode & suara aktif chat ini |
| `/voice mirror` | **VN masuk → VN keluar**, teks masuk → teks keluar (aturan mirror) |
| `/voice always` | semua balasan diusahakan jadi VN (bila muat di batas) |
| `/voice text` | kembali ke teks biasa (default) |
| `/voice style <nama>` | ganti preset suara tetap chat ini |
| `/voice voices` | daftar preset suara yang tersedia |

Preferensi tersimpan per chat (`~/.zeline/voice-prefs/`, 0600).

## Alur otomatis (tanpa tool agent)

1. User kirim VN → gateway transcribe langsung via provider
   (`/audio/transcriptions`) → transkrip jadi pesan user.
   Kalau transcribe gagal / belum dikonfigurasi → fallback ke alur lama
   (agent diminta transcribe via `analyze_media`).
2. Agent menjawab seperti biasa.
3. Bila mode voice aktif untuk chat itu dan balasan ≤ 600 karakter (tanpa blok
   kode) → teks disintesis (edge-tts → ogg/opus) → dikirim sebagai **voice
   bubble** (`sendVoice`; fallback `sendAudio`).
4. Bila TTS gagal → balasan dikirim sebagai **teks** + catatan singkat
   (tidak pernah diam). Balasan > 600 karakter → selalu teks.

## Prasyarat

- `edge-tts` (`pip install edge-tts`) — gratis, neural, banyak suara
- `ffmpeg` — convert mp3 → ogg/opus (biar jadi voice bubble, bukan attachment)
- Termux: `pip install edge-tts && pkg install ffmpeg`
- Untuk transcribe VN masuk: provider terkonfigurasi + transcription model
  (`ZELINE_AUDIO_MODEL`, mis. `whisper-1`).

## Preset suara (cewe)

| Style | Voice | Karakter |
|---|---|---|
| **emma-anime** (DEFAULT) | en-US-EmmaMultilingual +35Hz | anime, ngomong Indonesia, cheerful |
| ava-anime | en-US-AvaMultilingual +30Hz | anime natural, ngomong Indonesia |
| gadis-anime | id-ID-Gadis +40Hz | Indo asli, super imut |
| gadis | id-ID-Gadis | Indo asli, natural |
| ana | en-US-Ana | cute cartoon (English) |
| nanami | ja-JP-Nanami | seiyuu Jepang |

Multilingual (emma/ava) bisa ngomong Indonesia dengan suara premium + tuning
pitch tinggi → paling "anime tapi ngerti Indonesia".

## CLI manual (opsional)

`scripts/voice_reply.py` — thin wrapper di atas `zeline.voice`, antarmuka lama
tetap jalan:

```bash
SC=zeline/skills/voice-reply/scripts/voice_reply.py
python3 "$SC" voices                                   # daftar preset
python3 "$SC" say "Halo, aku Zeline!"                  # -> ~/.zeline/voice-out/reply.ogg
python3 "$SC" say "teks..." --style gadis-anime --out /tmp/vn
```

Cetak `FILE: <path>` + `VOICE: <voice> (style ...)`.

## Pitfalls

- **edge-tts online**: butuh koneksi (ambil suara dari server Microsoft).
  Kalau offline → gagal → fallback teks + catatan.
- **Teks kepanjangan**: batas TTS 600 karakter. Balasan panjang otomatis jadi
  teks (gateway tidak memotong diam-diam).
- **Blok kode tidak di-TTS**: balasan berisi ``` fence → dikirim sebagai teks
  (kode yang dibacakan terdengar berantakan).
- **Pitch kelewat tinggi** (>+45Hz) mulai pecah/robotik. Sweet spot anime Indo:
  +30 s/d +40Hz.
- **Karakter non-Latin / emoji** di teks: edge-tts baca yang bisa, skip emoji.
- **ffmpeg tak ada**: `zeline.voice.synthesize` menolak dengan pesan jelas
  (tidak ada fallback mp3 diam-diam di core; CLI lama yang masih mencetak
  WARN bila konversi opus gagal).
