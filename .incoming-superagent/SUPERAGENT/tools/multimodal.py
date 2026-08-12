"""
tools/multimodal.py — Voice & Screenshot Input  (v4.0)

Operator kirim voice note → transcribe (Whisper LOKAL, gratis).
Operator kirim screenshot dApp/error/chart → describe (lewat provider vision sk7).

Backend pluggable & guarded: kalau lib/provider belum ada, kasih pesan jelas —
gak crash. STT default coba faster-whisper → openai-whisper. Vision di-inject dari
sk7 (lo udah punya multi-provider; arahin ke yang support vision).
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Callable, Optional

WHISPER_MODEL = os.environ.get("HERMES_WHISPER_MODEL", "base")   # tiny/base/small/medium


# ───────────────── Voice → text (lokal, gratis) ─────────────────
def transcribe(audio_path: str, backend: Optional[Callable[[str], str]] = None) -> str:
    if backend is not None:
        return backend(audio_path)
    # 1) faster-whisper (cepat, CTranslate2)
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path)
        return " ".join(s.text for s in segments).strip()
    except Exception:
        pass
    # 2) openai-whisper (lokal, bukan API)
    try:
        import whisper  # type: ignore
        model = whisper.load_model(WHISPER_MODEL)
        return model.transcribe(audio_path)["text"].strip()
    except Exception as e:
        raise RuntimeError(
            "STT belum siap. Install salah satu (gratis, lokal):\n"
            "  pip install faster-whisper   # ringan, disarankan\n"
            "  pip install openai-whisper    # alternatif\n"
            f"detail: {e!r}")


# ───────────────── Screenshot → understanding (via sk7 vision) ─────────────────
def _to_b64(image_path: str) -> tuple[str, str]:
    data = Path(image_path).read_bytes()
    ext = Path(image_path).suffix.lower().lstrip(".") or "png"
    media = {"jpg": "jpeg"}.get(ext, ext)
    return base64.b64encode(data).decode(), f"image/{media}"


def describe_image(image_path: str, prompt: str,
                   vision_fn: Optional[Callable[[str, str, str], str]] = None) -> str:
    """
    vision_fn(prompt, image_b64, media_type) -> str.
    Wire dari sk7 provider lo yang support vision (Claude/GPT/Gemini, dll).
    Tanpa vision_fn → kasih instruksi, jangan diam-diam gagal.
    """
    b64, media = _to_b64(image_path)
    if vision_fn is None:
        raise RuntimeError(
            "vision_fn belum di-wire. Sambungin ke provider vision di sk7 "
            "(arahkan ke model multimodal), lalu lewatin sebagai vision_fn.")
    return vision_fn(prompt, b64, media)


# Telegram handler pattern (pseudo — wire di sk4):
#   on voice  → file = download(); text = transcribe(file); route ke agent
#   on photo  → file = download(); desc = describe_image(file, "apa isi screenshot ini?", vision_fn)
#   lalu proses 'text'/'desc' kayak pesan teks biasa (tetap lewat governor utk aksi dana)


if __name__ == "__main__":
    # tes dispatch logika tanpa lib berat
    try:
        describe_image.__wrapped__ if hasattr(describe_image, "__wrapped__") else None
    except Exception:
        pass
    print("transcribe backend injectable:",
          transcribe("x.wav", backend=lambda p: "halo dunia") == "halo dunia")
    try:
        describe_image(__file__, "test")   # no vision_fn → harus kasih instruksi
    except RuntimeError as e:
        print("vision guard works:", str(e)[:40], "...")
