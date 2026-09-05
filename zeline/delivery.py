"""Deliver a file the agent produced to whatever channel the session runs on.

Zeline could already *create* files — `generate_image` writes a PNG, a bundled
skill builds an XLSX dashboard or a PDF report — but nothing could hand one back
to the operator. The agent's only option was to print a filesystem path, which is
useless on a phone: the file sits inside Termux and the chat shows a string.

This module is the counterpart of :mod:`zeline.interaction`. That one lets a tool
ask the user a question through the active channel; this one lets a tool push a
file through it.

Flow:

1. A gateway registers a sender for a session identity via
   :func:`register_channel` when it starts handling a turn.
2. The ``send_file`` tool calls :func:`send`, which looks up the sender and
   returns a short human-readable result the model can quote.
3. If no channel is registered (CLI, cron running headless), delivery is a
   no-op that reports the absolute path instead of failing — the file was still
   produced, and pretending otherwise would make the model retry pointlessly.

Design rules, each one deliberate:

- **Never raise.** A failed upload must not kill the turn; it returns an
  ``ERROR``/notice string exactly like ``ask_user`` does.
- **The channel decides the wire format.** This module does not know about
  photos versus documents; Telegram picks `sendPhoto`/`sendAudio`/`sendDocument`
  from the suffix, because that choice is client-specific.
- **Size is capped here, not in the gateway.** Every chat platform rejects large
  uploads and the failure arrives as an opaque HTTP error; refusing early with a
  clear message is more useful than a 413 the model cannot interpret.
"""
from __future__ import annotations

import threading
from pathlib import Path

# Telegram's Bot API caps a bot upload at 50 MB. Stay under it so the refusal is
# ours (specific, actionable) rather than a bare HTTP failure from the platform.
MAX_DELIVERY_BYTES = 45 * 1024 * 1024
MAX_CAPTION_CHARS = 900

# Suffixes a chat client can preview inline. Kept here so every gateway agrees on
# what "image" means, while each still chooses its own API method.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
AUDIO_SUFFIXES = frozenset({".ogg", ".oga", ".mp3", ".m4a", ".wav", ".opus"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".mkv"})

_LOCK = threading.Lock()
_CHANNELS: dict[str, object] = {}


def register_channel(identity: str, sender: object) -> None:
    """Register how files reach the user for this session identity."""
    with _LOCK:
        _CHANNELS[identity or "cli:local"] = sender


def unregister_channel(identity: str) -> None:
    with _LOCK:
        _CHANNELS.pop(identity or "cli:local", None)


def has_channel(identity: str) -> bool:
    with _LOCK:
        return (identity or "cli:local") in _CHANNELS


def kind_for(path: Path) -> str:
    """Classify a file so a gateway can pick the right upload method."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return "document"


def send(identity: str, path: Path, caption: str = "") -> str:
    """Deliver ``path`` to the operator. Returns a short status for the model."""
    key = identity or "cli:local"
    text = str(caption or "").strip()[:MAX_CAPTION_CHARS]
    try:
        target = Path(path).expanduser()
        if not target.is_file():
            return f"ERROR send_file: not a file or not found: {target}"
        size = target.stat().st_size
    except OSError as exc:
        return f"ERROR send_file: cannot read the file ({exc.__class__.__name__})."
    if size == 0:
        return f"ERROR send_file: {target.name} is empty (0 bytes), nothing to send."
    if size > MAX_DELIVERY_BYTES:
        megabytes = size / (1024 * 1024)
        limit = MAX_DELIVERY_BYTES // (1024 * 1024)
        return (
            f"ERROR send_file: {target.name} is {megabytes:.1f} MB, over the "
            f"{limit} MB chat upload limit. Compress it or split it first."
        )

    with _LOCK:
        sender = _CHANNELS.get(key)
    if sender is None:
        # Headless run (CLI, cron): the file exists, so report where it is
        # instead of failing — the model must not retry a delivery that has no
        # channel to deliver through.
        return (
            f"NOT DELIVERED: this session has no chat channel to send files to. "
            f"The file is saved at {target} ({size} bytes)."
        )
    try:
        ok = bool(sender(target, text, kind_for(target)))  # type: ignore[operator]
    except Exception as exc:  # noqa: BLE001 — a broken channel must not kill the turn
        return f"ERROR send_file: delivery failed ({exc.__class__.__name__})."
    if not ok:
        return f"ERROR send_file: the chat platform rejected {target.name}."
    return f"Sent {target.name} ({size} bytes) to the user."
