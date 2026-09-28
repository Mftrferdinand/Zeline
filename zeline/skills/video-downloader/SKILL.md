---
name: video-downloader
description: |
  Download video atau audio dari link — YouTube (mp4/mp3), TikTok (tanpa
  watermark), Instagram, Twitter/X, Facebook, dan 1000+ situs via yt-dlp. User
  kirim link; kalau format TIDAK disebut ("mp3"/"mp4"), agent WAJIB tanya dulu
  mau audio (mp3) atau video (mp4). Hasil dikirim sebagai file ke chat Telegram
  (MEDIA:path). Kalau file > 50MB (limit bot Telegram), kirim path lokal / turunkan
  kualitas. Load saat user kirim link video + minta download, bilang "ytmp3",
  "ytmp4", "download tiktok", "simpan video ini", "ambil audionya".
metadata:
  zeline:
    tags: [download, video, audio, youtube, tiktok, ytdlp, mp3, mp4, media]
    category: media
---

# Video Downloader — download video/audio dari link

Kirim link → agent download → kirim file ke chat. Pakai `yt-dlp` (support 1000+
situs) + `ffmpeg` (convert/merge). Script: `scripts/video_dl.py`.

## Prasyarat

- `yt-dlp` di PATH (`pip install -U yt-dlp` — YouTube sering ganti signature,
  selalu pakai versi terbaru; script auto-upgrade sekali kalau kena 403)
- `ffmpeg` di PATH (buat mp3 convert + mp4 merge video/audio)
- Termux: `pkg install ffmpeg && pip install -U yt-dlp`

## Alur (WAJIB tanya format kalau tidak disebut)

```
1. User kirim link (± kata "mp3"/"mp4"/"audio"/"video")
2. Kalau format JELAS → langsung download
   Kalau TIDAK ada format → agent TANYA: "Mau audio (mp3) atau video (mp4)?"
3. (opsional) agent tampilkan info dulu: judul, durasi, platform
4. Download → dapat path file
5. Kirim ke chat Telegram: MEDIA:<path>
   Kalau > 50MB → kirim path lokal + saran turunkan kualitas
```

## Perintah script

```bash
SC=~/.zeline/skills/.../video_dl.py   # path skill di install

# metadata dulu (judul/durasi/platform) — cepat, tanpa download
python3 "$SC" info "<url>"

# audio mp3 (default quality 5 ≈ 128k VBR)
python3 "$SC" mp3 "<url>" [--quality 0-9]

# video mp4 (default tinggi maks 720p)
python3 "$SC" mp4 "<url>" [--max 480|720|1080]
```

Script cetak:
- `FILE: <path absolut>` → file hasil, kirim ini ke chat
- `SIZE_MB: <n>` → ukuran; kalau > 50 muncul `WARN` di stderr

## Cara agent pakai (contoh)

**User cuma kirim link (tanpa format):**
```
User: https://youtu.be/xxxx
Agent: "Mau audio (mp3) atau video (mp4)?"
User: mp3
Agent: [python3 SC mp3 <url>] → MEDIA:/…/lagu.mp3
```

**User sebut format:**
```
User: ytmp4 https://youtu.be/xxxx
Agent: [python3 SC mp4 <url>] → MEDIA:/…/video.mp4
```

**TikTok (auto tanpa watermark):**
```
User: download tiktok https://tiktok.com/@x/video/123
Agent: [python3 SC mp4 <url>] → MEDIA:/…/tiktok.mp4
```

## Pengiriman file

- Selalu kirim via `MEDIA:<path>` — Telegram render mp4 inline, mp3 sebagai voice/audio.
- **Limit Telegram bot API = 50MB.** Kalau `SIZE_MB` > 50:
  - Untuk mp4: ulangi dengan `--max 480` (atau 360) → file lebih kecil.
  - Kalau tetap gede: kirim **path lokal** ke user ("filenya di ~/downloads/…,
    ambil dari file manager") — jangan paksa upload yang bakal gagal.
- File tersimpan di `~/downloads/`.

## Platform yang didukung (via yt-dlp)

YouTube, TikTok, Instagram (reel/post), Twitter/X, Facebook, Twitch clips,
Vimeo, Dailymotion, SoundCloud, Bilibili, + 1000+ situs. Kalau ragu, jalankan
`info` dulu — kalau metadata keluar, download-nya bakal jalan.

## Pitfalls

- **YouTube 403 / "format not available"** → signature YouTube berubah. Script
  auto `pip install -U yt-dlp` sekali lalu retry. Kalau masih gagal, yt-dlp versi
  terbaru mungkin belum nutup — coba lagi beberapa jam kemudian.
- **Playlist link** → script pakai `--no-playlist`, cuma ambil 1 video. Kalau user
  mau seluruh playlist, itu beda kebutuhan (banyak file, bisa flood chat).
- **File > 50MB** → jangan diam-diam gagal upload; cek `SIZE_MB`, turunkan
  kualitas atau kirim path lokal.
- **Judul dengan karakter aneh** (emoji, non-ASCII) → aman, template `%(title).60s`
  dipotong 60 char, ffmpeg/yt-dlp handle unicode.
- **Live stream** → yt-dlp bisa, tapi butuh flag beda (`--live-from-start`); di
  luar scope default. Kasih tau user kalau link-nya live.
- **Konten berhak cipta** → tool ini netral (yt-dlp). Download buat pemakaian
  pribadi user; jangan bantu redistribusi massal konten berlisensi.
