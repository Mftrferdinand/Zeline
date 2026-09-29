#!/usr/bin/env python3
"""voice_reply.py — teks jadi voice note (ogg/opus) untuk skill voice-reply.

Pakai edge-tts (gratis, neural, online) → mp3, lalu ffmpeg → ogg/opus supaya
Telegram render sebagai voice bubble (bukan file attachment).

Suara default = Emma multilingual (bisa Bahasa Indonesia) dengan tuning "anime":
pitch +35Hz, rate +10%. Bisa diganti lewat --voice / preset --style.

Perintah:
  say "<teks>" [--out PATH] [--voice V] [--rate R] [--pitch P] [--style S]
      → hasilkan voice note ogg. Cetak "FILE: <path>".
  voices                → daftar preset suara cewe (kode → voice/tuning)
  presets               → alias sama dengan voices

Preset (--style):
  emma-anime  (DEFAULT) Emma multilingual, pitch +35Hz rate +10% — anime, Indo OK
  ava-anime   Ava multilingual, pitch +30Hz rate +8% — anime natural
  gadis       id-ID-Gadis normal — Indo asli, natural
  gadis-anime id-ID-Gadis pitch +40Hz rate +15% — Indo asli super imut
  ana         en-US-Ana — cute cartoon (English)
  nanami      ja-JP-Nanami — seiyuu Jepang
"""
import argparse
import os
import shutil
import subprocess
import sys

OUT_DIR = os.path.expanduser("~/.zeline/voice-out")

# preset: style -> (voice, rate, pitch)
PRESETS = {
    "emma-anime": ("en-US-EmmaMultilingualNeural", "+10%", "+35Hz"),
    "ava-anime":  ("en-US-AvaMultilingualNeural",  "+8%",  "+30Hz"),
    "gadis":      ("id-ID-GadisNeural",            "+0%",  "+0Hz"),
    "gadis-anime":("id-ID-GadisNeural",            "+15%", "+40Hz"),
    "ana":        ("en-US-AnaNeural",              "+0%",  "+0Hz"),
    "nanami":     ("ja-JP-NanamiNeural",           "+0%",  "+0Hz"),
}
DEFAULT_STYLE = "emma-anime"


def _ensure_dir():
    os.makedirs(OUT_DIR, exist_ok=True)


def cmd_voices(_a):
    print("Preset suara (pakai --style <kode>):")
    for k, (v, r, p) in PRESETS.items():
        star = "  ← DEFAULT" if k == DEFAULT_STYLE else ""
        print(f"  {k:<12} {v:<32} rate {r:<5} pitch {p}{star}")
    return 0


def cmd_say(a):
    if not shutil.which("edge-tts"):
        print("ERROR: edge-tts belum terpasang. `pip install edge-tts`", file=sys.stderr)
        return 1
    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg belum terpasang (perlu buat ogg/opus voice bubble).",
              file=sys.stderr)
        return 1

    # tentukan voice/rate/pitch: --style jadi basis, --voice/--rate/--pitch override
    style = a.style or DEFAULT_STYLE
    if style not in PRESETS:
        print(f"ERROR: style '{style}' tidak dikenal. Lihat: voice_reply.py voices",
              file=sys.stderr)
        return 1
    voice, rate, pitch = PRESETS[style]
    voice = a.voice or voice
    rate = a.rate or rate
    pitch = a.pitch or pitch

    _ensure_dir()
    stem = a.out or os.path.join(OUT_DIR, "reply")
    stem = stem[:-4] if stem.endswith((".ogg", ".mp3")) else stem
    mp3 = stem + ".mp3"
    ogg = stem + ".ogg"

    # 1) edge-tts → mp3
    cmd = ["edge-tts", "--voice", voice, "--rate", rate, "--pitch", pitch,
           "--text", a.text, "--write-media", mp3]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not os.path.exists(mp3):
        print("ERROR edge-tts:", (r.stderr or r.stdout or "").strip()[:200], file=sys.stderr)
        return 1

    # 2) ffmpeg mp3 → ogg/opus (voice bubble Telegram)
    r2 = subprocess.run(
        ["ffmpeg", "-y", "-i", mp3, "-c:a", "libopus", "-b:a", "48k", ogg],
        capture_output=True, text=True, timeout=120)
    if r2.returncode != 0 or not os.path.exists(ogg):
        # fallback: kirim mp3 kalau opus gagal
        print(f"FILE: {mp3}")
        print(f"VOICE: {voice} (rate {rate}, pitch {pitch})")
        print("WARN: konversi opus gagal, pakai mp3.", file=sys.stderr)
        return 0

    try:
        os.remove(mp3)
    except OSError:
        pass
    print(f"FILE: {ogg}")
    print(f"VOICE: {voice} (rate {rate}, pitch {pitch}, style {style})")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Teks → voice note (edge-tts + ffmpeg opus)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("say")
    s.add_argument("text")
    s.add_argument("--out", help="path output (tanpa/dengan .ogg)")
    s.add_argument("--voice", help="override voice edge-tts")
    s.add_argument("--rate", help="override rate mis. +10%%")
    s.add_argument("--pitch", help="override pitch mis. +35Hz")
    s.add_argument("--style", help=f"preset (default {DEFAULT_STYLE})")
    s.set_defaults(func=cmd_say)

    v = sub.add_parser("voices"); v.set_defaults(func=cmd_voices)
    pr = sub.add_parser("presets"); pr.set_defaults(func=cmd_voices)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
