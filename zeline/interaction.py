"""Human-in-the-loop: let a tool ask the operator a question mid-turn.

The agent runs its turn on a worker thread while the gateway keeps polling, so
a tool can block on a question and the reply arrives through the normal message
path. This module is the meeting point between the two.

Flow:

1. ``ask_user`` (tool) calls :func:`ask`, which registers a pending question for
   that session identity and blocks on an event.
2. The gateway sees a pending question via :func:`pending` and renders it
   (Telegram inline keyboard, CLI prompt, ...). The user's next message — or a
   button tap — is routed to :func:`answer` instead of starting a new turn.
3. :func:`ask` wakes up and returns the answer to the model.

Design rules learned from the ``/stop`` work:

- A blocking wait MUST be cancellable. ``/stop`` and ``/new`` call
  :func:`cancel` so a pending question never wedges a session.
- The wait MUST have a ceiling below ``config.MAX_TURN_SECONDS``; otherwise the
  turn budget expires while a tool sits waiting and the user sees nothing.
- Only ONE question per identity may be open at a time. A second question
  replaces nothing — it is refused, so the model cannot spam prompts.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from zeline import config

# Answers longer than this are truncated: the model asked a question, not for a
# document, and an unbounded string would land straight in the transcript.
MAX_ANSWER_CHARS = 2000
MAX_QUESTION_CHARS = 500
MAX_OPTIONS = 6
MAX_OPTION_CHARS = 80


@dataclass
class PendingQuestion:
    identity: str
    question: str
    options: tuple[str, ...]
    created_at: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    answer: str = ""
    cancelled: bool = False


_LOCK = threading.Lock()
_PENDING: dict[str, PendingQuestion] = {}


def _timeout_seconds() -> float:
    raw = getattr(config, "ASK_USER_TIMEOUT", 180.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 180.0
    # Never outlive the turn budget: a question that expires after the turn is
    # already dead just produces a silent hang.
    ceiling = max(30.0, float(getattr(config, "MAX_TURN_SECONDS", 360.0)) - 30.0)
    return max(5.0, min(value, ceiling))


def normalize_options(options: object) -> tuple[str, ...]:
    """Coerce model-supplied options into a short, clean tuple."""
    if options is None or isinstance(options, (str, bytes)):
        # A single string is not a list of choices; treat it as free-form.
        return ()
    try:
        items = list(options)  # type: ignore[arg-type]
    except TypeError:
        return ()
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        text = text[:MAX_OPTION_CHARS]
        # Two identical buttons are indistinguishable to the user and waste a
        # row; keep the first occurrence and preserve the model's ordering.
        if text.casefold() in seen:
            continue
        seen.add(text.casefold())
        cleaned.append(text)
        if len(cleaned) >= MAX_OPTIONS:
            break
    return tuple(cleaned)


def pending(identity: str) -> PendingQuestion | None:
    with _LOCK:
        return _PENDING.get(identity or "cli:local")


def has_pending(identity: str) -> bool:
    return pending(identity) is not None


def ask(identity: str, question: str, options: object = None) -> str:
    """Block until the operator answers, or the wait is cancelled / times out.

    Returns the answer text, or an ``ERROR``/notice string the model can act on.
    Never raises: a failed question must not kill the turn.
    """
    key = identity or "cli:local"
    text = str(question or "").strip()[:MAX_QUESTION_CHARS]
    if not text:
        return "ERROR ask_user: question is empty."
    choices = normalize_options(options)

    with _LOCK:
        if key in _PENDING:
            return (
                "ERROR ask_user: a question is already awaiting the user's answer. "
                "Wait for it instead of asking again."
            )
        entry = PendingQuestion(identity=key, question=text, options=choices)
        _PENDING[key] = entry

    timeout = _timeout_seconds()
    try:
        delivered = _deliver(entry)
        if delivered is not None:
            # A synchronous channel (CLI stdin) already produced the answer.
            return delivered
        if not entry.event.wait(timeout=timeout):
            return (
                f"NO ANSWER: the user did not reply within {int(timeout)}s. "
                "Proceed with your best judgement and say which assumption you made."
            )
        if entry.cancelled:
            return "CANCELLED: the user cancelled this question."
        return entry.answer or "(empty answer)"
    finally:
        with _LOCK:
            if _PENDING.get(key) is entry:
                del _PENDING[key]


def answer(identity: str, text: str) -> bool:
    """Route a user message to the pending question. True if it was consumed."""
    key = identity or "cli:local"
    with _LOCK:
        entry = _PENDING.get(key)
        if entry is None or entry.event.is_set():
            return False
        entry.answer = str(text or "").strip()[:MAX_ANSWER_CHARS]
    entry.event.set()
    return True


def answer_option(identity: str, index: int) -> str | None:
    """Answer by option index (button tap). Returns the chosen text, or None."""
    key = identity or "cli:local"
    with _LOCK:
        entry = _PENDING.get(key)
        if entry is None or entry.event.is_set():
            return None
        if index < 0 or index >= len(entry.options):
            return None
        chosen = entry.options[index]
        entry.answer = chosen
    entry.event.set()
    return chosen


def cancel(identity: str) -> bool:
    """Release a pending question (used by /stop and /new). True if one existed."""
    key = identity or "cli:local"
    with _LOCK:
        entry = _PENDING.get(key)
        if entry is None or entry.event.is_set():
            return False
        entry.cancelled = True
    entry.event.set()
    return True


# --------------------------------------------------------------- delivery
#
# A channel registers how a question reaches its user. Gateways register an
# async renderer (returns None; the answer arrives later through `answer`).
# The CLI registers a synchronous prompt that returns the answer immediately.

_CHANNELS: dict[str, object] = {}


def register_channel(identity: str, renderer: object) -> None:
    with _LOCK:
        _CHANNELS[identity or "cli:local"] = renderer


def unregister_channel(identity: str) -> None:
    with _LOCK:
        _CHANNELS.pop(identity or "cli:local", None)


def _deliver(entry: PendingQuestion) -> str | None:
    with _LOCK:
        renderer = _CHANNELS.get(entry.identity)
    if renderer is None:
        return None
    try:
        return renderer(entry)  # type: ignore[operator]
    except Exception:  # noqa: BLE001 — a broken renderer must never strand the tool
        # Fall back to waiting so a plain text reply still works.
        return None
