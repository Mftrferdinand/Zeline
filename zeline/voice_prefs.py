"""Preferensi voice per chat: mode balasan suara + preset suara TTS.

Satu file JSON per identity di ``~/.zeline/voice-prefs/``, mengikuti pola
``zeline.goals``: nama file di-hash SHA-256 (tanpa chat id di nama file),
mode 0600, direktori 0700, tulis atomik via rename, satu lock per identity
dalam proses.

``voice_mode``:
- ``"text"`` (default) — balasan selalu teks, seperti biasa.
- ``"mirror"`` — VN masuk dibalas VN, teks masuk dibalas teks (aturan skill
  voice-reply yang lama).
- ``"always"`` — balasan selalu diusahakan sebagai VN (bila muat di batas TTS).

``voice_style``: nama preset di ``zeline.voice.PRESETS`` (default
``"emma-anime"``).

Semua pembacaan defensif: file hilang/corrupt/berisi mode tak dikenal tidak
pernah melempar — pemanggil selalu dapat preferensi default yang valid.
Menulis dengan nilai tak valid melempar ``ValueError`` supaya kesalahan
terlihat di permukaan (command handler), bukan tersimpan diam-diam.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from zeline import config
from zeline import voice as _voice

VOICE_MODES = ("text", "mirror", "always")
DEFAULT_MODE = "text"


def prefs_dir() -> Path:
    return config.DATA_DIR / "voice-prefs"


def _key(identity: str) -> str:
    return hashlib.sha256((identity or "cli:local").encode("utf-8")).hexdigest()[:32]


def _path(identity: str) -> Path:
    return prefs_dir() / f"{_key(identity)}.json"


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(identity: str) -> threading.Lock:
    key = _key(identity)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _defaults() -> dict[str, str]:
    return {"voice_mode": DEFAULT_MODE, "voice_style": _voice.DEFAULT_STYLE}


def get_prefs(identity: str) -> dict[str, str]:
    """Baca preferensi voice untuk identity; selalu kembalikan nilai valid."""
    prefs = _defaults()
    try:
        raw = _path(identity).read_text(encoding="utf-8")
    except OSError:
        return prefs
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return prefs
    if not isinstance(data, dict):
        return prefs
    mode = data.get("voice_mode")
    if isinstance(mode, str) and mode in VOICE_MODES:
        prefs["voice_mode"] = mode
    style = data.get("voice_style")
    if isinstance(style, str) and style in _voice.PRESETS:
        prefs["voice_style"] = style
    return prefs


def _write(identity: str, prefs: dict[str, str]) -> None:
    directory = prefs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    target = _path(identity)
    temporary = target.with_name(f"{target.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(
            json.dumps(prefs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def voice_mode(identity: str) -> str:
    """Mode balasan voice untuk identity (default ``"text"``)."""
    return get_prefs(identity)["voice_mode"]


def voice_style(identity: str) -> str:
    """Preset suara TTS untuk identity (default ``"emma-anime"``)."""
    return get_prefs(identity)["voice_style"]


def set_mode(identity: str, mode: str) -> dict[str, str]:
    """Ubah ``voice_mode``; raise ValueError untuk mode tak dikenal."""
    mode = str(mode or "").strip().lower()
    if mode not in VOICE_MODES:
        raise ValueError(
            f"voice_mode '{mode}' tidak dikenal. Pilihan: {', '.join(VOICE_MODES)}."
        )
    with _lock_for(identity):
        prefs = get_prefs(identity)
        prefs["voice_mode"] = mode
        _write(identity, prefs)
        return prefs


def set_style(identity: str, style: str) -> dict[str, str]:
    """Ubah ``voice_style``; raise ValueError untuk preset tak dikenal."""
    style = str(style or "").strip()
    if style not in _voice.PRESETS:
        raise ValueError(
            f"style suara '{style}' tidak dikenal. Pilihan: {', '.join(_voice.styles())}."
        )
    with _lock_for(identity):
        prefs = get_prefs(identity)
        prefs["voice_style"] = style
        _write(identity, prefs)
        return prefs


def wants_voice_reply(identity: str, *, came_from_voice: bool) -> bool:
    """Apakah balasan turn ini sebaiknya dikirim sebagai voice note?

    ``"always"`` → ya untuk semua balasan. ``"mirror"`` → ya hanya bila input
    turn ini adalah voice note. ``"text"`` (default) → tidak pernah.
    """
    mode = voice_mode(identity)
    if mode == "always":
        return True
    if mode == "mirror":
        return bool(came_from_voice)
    return False
