#!/usr/bin/env python3
"""voice_reply.py — thin CLI wrapper di atas ``zeline.voice`` (core TTS).

Logika TTS (preset, edge-tts -> mp3 -> ffmpeg ogg/opus) dipromosikan
dari script ini ke ``zeline/voice.py`` supaya gateway bisa membalas dengan
voice note secara otomatis. Script ini dipertahankan sebagai wrapper CLI yang
kompatibel ke belakang: antarmuka ``say``/``voices``/``presets`` dan semua flag
tidak berubah.

Perintah:
  say "<teks>" [--out PATH] [--voice V] [--rate R] [--pitch P] [--style S]
      -> hasilkan voice note ogg. Cetak "FILE: <path>".
  voices / presets -> daftar preset suara
"""
import argparse
import os
import sys
from pathlib import Path

# Pastikan ``zeline`` bisa diimport saat script dijalankan langsung dari repo
# (bukan dari instalasi pip): cari direktori yang memuat paket ``zeline``.
_here = Path(__file__).resolve()
_repo_root = next(
    (p for p in _here.parents if (p / "zeline" / "__init__.py").is_file()),
    None,
)
if _repo_root is not None and str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from zeline.voice import DEFAULT_STYLE, PRESETS, VoiceError, synthesize

OUT_DIR = os.path.expanduser("~/.zeline/voice-out")


def cmd_voices(_a):
    print("Preset suara (pakai --style <kode>):")
    for k in sorted(PRESETS):
        v, r, p = PRESETS[k]
        star = "  <- DEFAULT" if k == DEFAULT_STYLE else ""
        print(f"  {k:<12} {v:<32} rate {r:<5} pitch {p}{star}")
    return 0


def cmd_say(a):
    style = a.style or DEFAULT_STYLE
    if style not in PRESETS:
        print(f"ERROR: style '{style}' tidak dikenal. Lihat: voice_reply.py voices",
              file=sys.stderr)
        return 1
    stem = a.out or os.path.join(OUT_DIR, "reply")
    stem = stem[:-4] if stem.endswith((".ogg", ".mp3")) else stem
    out_dir = os.path.dirname(stem) or OUT_DIR
    # zeline.voice.synthesize selalu menulis reply.<ogg|mp3> di out_dir;
    # pindahkan ke stem yang diminta supaya --out tetap dihormati.
    try:
        result = synthesize(
            a.text,
            style=style,
            voice=a.voice or "",
            rate=a.rate or "",
            pitch=a.pitch or "",
            out_dir=Path(out_dir),
        )
    except VoiceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    wanted = Path(stem + result.suffix)
    if result != wanted:
        try:
            result.replace(wanted)
            result = wanted
        except OSError:
            pass  # nama alternatif gagal: pakai hasil asli, tetap valid
    voice_name, rate, pitch = PRESETS[style]
    voice_name = a.voice or voice_name
    rate = a.rate or rate
    pitch = a.pitch or pitch
    if result.suffix == ".mp3":
        print(f"WARN: konversi opus gagal, pakai mp3 (bukan voice bubble).", file=sys.stderr)
    print(f"FILE: {result}")
    print(f"VOICE: {voice_name} (rate {rate}, pitch {pitch}, style {style})")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Teks -> voice note (edge-tts + ffmpeg opus)")
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
