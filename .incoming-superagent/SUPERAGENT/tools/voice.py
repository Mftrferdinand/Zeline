"""
tools/voice.py — Voice Conversation Mode  (v4.0)

Lengkapin voice input (sk15) jadi DUA ARAH: agent ngomong balik (TTS).
converse() = STT → LLM → TTS, kayak nelpon agent.

Backend pluggable & guarded (gak crash kalau lib belum ada):
- TTS default: pyttsx3 (offline) → piper. Bisa di-inject.
- STT: pakai multimodal.transcribe (Whisper lokal).
- LLM: model_registry cascade (atau inject).
"""
from __future__ import annotations

import os
from typing import Callable, Optional

TTS_OUT = os.environ.get("HERMES_TTS_OUT", "/tmp/hermes_reply.wav")


def speak(text: str, out_path: str = TTS_OUT,
          backend: Optional[Callable[[str, str], str]] = None) -> str:
    """text → file audio. Return path. backend(text, out)->path kalau di-inject."""
    if backend is not None:
        return backend(text, out_path)
    # 1) pyttsx3 (offline, lintas-OS)
    try:
        import pyttsx3
        eng = pyttsx3.init()
        eng.save_to_file(text, out_path)
        eng.runAndWait()
        return out_path
    except Exception:
        pass
    # 2) piper (neural, lokal)
    try:
        import subprocess, shutil
        if shutil.which("piper"):
            subprocess.run(["piper", "--output_file", out_path], input=text.encode(),
                           capture_output=True, timeout=60)
            return out_path
    except Exception:
        pass
    raise RuntimeError("TTS belum siap. Install (gratis, lokal):\n"
                       "  pip install pyttsx3      # ringan, offline\n"
                       "  atau piper (neural)")


def converse(audio_in: str,
             stt_fn: Optional[Callable[[str], str]] = None,
             llm_fn: Optional[Callable[[list], str]] = None,
             tts_backend: Optional[Callable[[str, str], str]] = None,
             out_path: str = TTS_OUT) -> dict:
    """
    Satu giliran percakapan suara: audio masuk → teks → jawaban LLM → audio balik.
    Aksi yang nyentuh dana TETAP lewat governor di layer pemrosesan teks (bukan di sini).
    """
    # 1) STT
    if stt_fn is None:
        from multimodal import transcribe
        stt_fn = transcribe
    user_text = stt_fn(audio_in)

    # 2) LLM
    if llm_fn is None:
        from model_registry import ModelRegistry
        reg = ModelRegistry()
        llm_fn = lambda msgs: reg.call_with_cascade(msgs)[0]
    reply = llm_fn([{"role": "user", "content": user_text}])

    # 3) TTS
    audio_out = speak(reply, out_path, backend=tts_backend)
    return {"heard": user_text, "reply": reply, "audio_out": audio_out}


if __name__ == "__main__":
    # tes dispatch tanpa lib berat
    out = converse("in.wav",
                   stt_fn=lambda p: "berapa harga eth sekarang",
                   llm_fn=lambda m: "ETH sekitar $3.400 berdasarkan data terakhir.",
                   tts_backend=lambda t, o: o)   # mock TTS
    print("heard:", out["heard"])
    print("reply:", out["reply"])
    print("audio_out:", out["audio_out"])
    try:
        speak("test", backend=None)   # no lib → guard
    except RuntimeError as e:
        print("TTS guard works:", str(e)[:40], "...")
