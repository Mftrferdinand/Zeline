---
name: voice-reply
description: |
  Balas pakai voice note (VN) suara cewe anime bahasa Indonesia. User kirim VN
  → Zeline transcribe (denger) → proses → jawabannya diubah jadi suara (TTS
  edge-tts) → dikirim balik sebagai audio. Mirror mode: kalau user kirim VN,
  balas VN; kalau ketik, balas teks. Suara bisa dipilih (preset emma-anime,
  ava-anime, gadis-anime, gadis, ana, nanami) via /voice. Load saat user minta
  "balas pakai suara", "bales vn", "suara anime", "voice reply", atau kirim VN
  dan mau dibalas VN.
metadata:
  zeline:
    tags: [voice, vn, tts, edge-tts, anime, indonesia, audio, mirror]
    category: messaging
---

# Voice Reply — balas VN dengan suara cewe anime (Indonesia)

User kirim VN → Zeline denger (transcribe, sudah built-in) → jawab → suara
jawaban dikirim balik. Script: `scripts/voice_reply.py` (edge-tts + ffmpeg opus).

## Alur (mirror mode)

```
User kirim VN  → transcribe (built-in analyze_media) → proses → TTS → kirim audio balik
User ketik teks → jawab teks biasa (TIDAK auto-VN)
```

Aturan: **input VN → output VN; input teks → output teks.** Kalau user eksplisit
minta "balas pakai suara" walau dia ngetik, boleh VN.

## Prasyarat

- `edge-tts` (`pip install edge-tts`) — gratis, neural, banyak suara
- `ffmpeg` — convert mp3 → ogg/opus (biar jadi voice, bukan file mentah)
- Termux: `pip install edge-tts && pkg install ffmpeg`

## Perintah script

```bash
SC=~/.zeline/skills/.../voice_reply.py

# lihat preset suara
python3 "$SC" voices

# hasilkan VN dari teks (default: emma-anime, bisa Indonesia)
python3 "$SC" say "Halo, aku Zeline! Gimana kabar kamu?" --out ~/.zeline/voice-out/reply

# pilih preset lain
python3 "$SC" say "teks..." --style gadis-anime
python3 "$SC" say "teks..." --style ava-anime

# tuning manual (override preset)
python3 "$SC" say "teks..." --voice id-ID-GadisNeural --pitch "+40Hz" --rate "+15%"
```

Script cetak `FILE: <path.ogg>` + `VOICE: <voice> (style ...)`.

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

## Cara agent kirim VN

Setelah script hasilkan `.ogg`, kirim ke chat pakai tool `send_file` dengan path
itu — Zeline kirim sebagai audio yang bisa langsung diputar:

```
1. python3 SC say "<jawaban>" --out ~/.zeline/voice-out/reply
2. baca FILE: dari output
3. send_file(path=<file.ogg>)   → user dengar jawaban
```

## Ganti suara default (opsional)

Kalau user minta ganti suara tetap (mis. "pakai gadis-anime terus"), catat
preferensi itu — panggil `say` dengan `--style` yang dipilih di semua balasan
VN berikutnya. Untuk sampel banding, generate beberapa preset lalu kirim
supaya user pilih.

## Pitfalls

- **Voice bubble vs audio attachment**: `.ogg/opus` dikirim via `send_file` muncul
  sebagai audio playable. Kalau mau bubble VN "asli" (waveform), gateway perlu
  `sendVoice` — belum wajib; audio attachment sudah cukup untuk dengar jawaban.
- **edge-tts online**: butuh koneksi (ambil suara dari server Microsoft). Kalau
  offline, gagal — fallback: kirim teks biasa + kasih tau user.
- **Teks kepanjangan**: VN panjang = file besar + lama generate. Untuk jawaban
  panjang, ringkas dulu ke inti (2-4 kalimat) sebelum TTS, atau kirim teks +
  VN ringkasan.
- **Pitch kelewat tinggi** (>+45Hz) mulai pecah/robotik. Sweet spot anime Indo:
  +30 s/d +40Hz.
- **Karakter non-Latin / emoji** di teks: edge-tts baca yang bisa, skip emoji.
  Bersihkan emoji dari teks sebelum TTS kalau mau bersih.
- **ffmpeg tak ada**: script fallback kirim mp3 (tetap bisa diputar), tapi bukan
  format voice. Pasang ffmpeg untuk hasil terbaik.
