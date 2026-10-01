#!/usr/bin/env python3
"""video_dl.py — download video/audio dari link (YouTube, TikTok, IG, X, dll)
via yt-dlp, untuk skill video-downloader.

Dipakai agent Zeline: user kirim link + optional "mp3"/"mp4". Kalau format tidak
disebut, agent WAJIB tanya dulu (mp3 atau mp4) sebelum panggil script ini.

Perintah:
  info  <url>                 → metadata (judul, durasi, uploader, platform) tanpa download
  mp3   <url> [--quality Q]   → audio mp3 (default quality 5 / VBR ~128k)
  mp4   <url> [--max H]       → video mp4, tinggi maksimal H px (default 720)

Output: unduh ke ~/downloads/, cetak baris "FILE: <path>" biar agent tahu file
mana yang dikirim ke chat Telegram. Cetak juga "SIZE_MB: <n>" untuk cek limit
Telegram (50MB bot API).

Catatan: YouTube sering ganti signature → kalau 403, script auto-coba
`pip install -U yt-dlp` sekali lalu retry.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

DL_DIR = os.path.expanduser("~/downloads")
TELEGRAM_LIMIT_MB = 50


def _ensure_dir():
    os.makedirs(DL_DIR, exist_ok=True)


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def _newest(pattern):
    files = glob.glob(os.path.join(DL_DIR, pattern))
    return max(files, key=os.path.getmtime) if files else None


def _upgrade_ytdlp():
    subprocess.run([sys.executable, "-m", "pip", "install", "-U", "--quiet", "yt-dlp"],
                   capture_output=True, text=True, timeout=180)


def cmd_info(a):
    r = _run(["yt-dlp", "--no-warnings", "-J", a.url])
    if r.returncode != 0:
        print("ERROR info:", (r.stderr or "").strip()[:200], file=sys.stderr)
        return 1
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        print("ERROR: gagal parse metadata", file=sys.stderr)
        return 1
    dur = d.get("duration")
    mins = f"{int(dur)//60}:{int(dur)%60:02d}" if dur else "?"
    print(f"TITLE: {d.get('title','?')}")
    print(f"PLATFORM: {d.get('extractor_key', d.get('extractor','?'))}")
    print(f"UPLOADER: {d.get('uploader','?')}")
    print(f"DURATION: {mins}")
    if d.get("view_count"):
        print(f"VIEWS: {d['view_count']:,}")
    return 0


def _download(fmt_args, url, out_tmpl, expect_ext, retry_on_403=True):
    _ensure_dir()
    cmd = ["yt-dlp", "--no-warnings", "--no-playlist",
           "-o", os.path.join(DL_DIR, out_tmpl)] + fmt_args + [url]
    r = _run(cmd)
    if r.returncode != 0:
        err = (r.stderr or "") + (r.stdout or "")
        # YouTube signature rot → upgrade yt-dlp sekali lalu retry
        if retry_on_403 and ("403" in err or "Forbidden" in err or "not available" in err):
            _upgrade_ytdlp()
            return _download(fmt_args, url, out_tmpl, expect_ext, retry_on_403=False)
        print("ERROR download:", err.strip()[:300], file=sys.stderr)
        return None
    return _newest(f"*.{expect_ext}")


def cmd_mp3(a):
    q = str(a.quality if a.quality is not None else 5)
    path = _download(
        ["-x", "--audio-format", "mp3", "--audio-quality", q],
        a.url, "%(title).60s.%(ext)s", "mp3")
    return _emit(path)


def cmd_mp4(a):
    h = a.max or 720
    fmt = f"bv*[height<={h}]+ba/b[height<={h}]/b"
    path = _download(
        ["-f", fmt, "--merge-output-format", "mp4"],
        a.url, "%(title).60s.%(ext)s", "mp4")
    return _emit(path)


def _emit(path):
    if not path or not os.path.exists(path):
        print("ERROR: file hasil tidak ditemukan", file=sys.stderr)
        return 1
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"FILE: {path}")
    print(f"SIZE_MB: {size_mb:.1f}")
    if size_mb > TELEGRAM_LIMIT_MB:
        print(f"WARN: {size_mb:.1f}MB > {TELEGRAM_LIMIT_MB}MB limit Telegram bot API. "
              "Kirim path lokal ke user, atau turunkan kualitas (mp4 --max 480).",
              file=sys.stderr)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Download video/audio via yt-dlp")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("info"); i.add_argument("url"); i.set_defaults(func=cmd_info)
    m3 = sub.add_parser("mp3"); m3.add_argument("url")
    m3.add_argument("--quality", type=int, help="0(best)-9(worst), default 5")
    m3.set_defaults(func=cmd_mp3)
    m4 = sub.add_parser("mp4"); m4.add_argument("url")
    m4.add_argument("--max", type=int, help="tinggi maks px, default 720")
    m4.set_defaults(func=cmd_mp4)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
