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
- Unrendered channels have a bounded delivery/answer wait. A successfully
  rendered interactive question may wait until answer or cancellation; the agent
  excludes ask_user idle time from its active execution budget.
- Only ONE question per identity may be open at a time. A second question
  replaces nothing — it is refused, so the model cannot spam prompts.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from zeline import config

# Answers longer than this are truncated: the model asked a question, not for a
# document, and an unbounded string would land straight in the transcript.
MAX_ANSWER_CHARS = 2000
MAX_QUESTION_CHARS = 500
MAX_OPTIONS = 6
# Choices are explanatory text, not button labels. Retain bounded full choices.
MAX_OPTION_CHARS = 2000


@dataclass
class PendingQuestion:
    identity: str
    question: str
    options: tuple[str, ...]
    created_at: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    answer: str = ""
    cancelled: bool = False
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    wait_forever: bool = False  # A rendered channel may wait until answer or /stop.
    status: str = "pending"
    on_resolved: object = None
    message_id: int | None = None


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


def ask(identity: str, question: str, options: object = None, *, should_stop=None) -> str:
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
        if should_stop and should_stop():
            return "CANCELLED: the user cancelled this question."
        if key in _PENDING:
            return (
                "ERROR ask_user: a question is already awaiting the user's answer. "
                "Wait for it instead of asking again."
            )
        entry = PendingQuestion(identity=key, question=text, options=choices)
        _PENDING[key] = entry

    timeout = _timeout_seconds()
    try:
        from zeline import cancellation
        try:
            delivered = cancellation.wait_io(lambda: _deliver(entry), lambda: entry.cancelled or bool(should_stop and should_stop()))
        except cancellation.Cancelled:
            return "CANCELLED: the user cancelled this question."
        if delivered is not None:
            # A synchronous channel (CLI stdin) already produced the answer.
            entry.answer = delivered
            entry.status = "answered"
            return delivered
        import time
        deadline = None if entry.wait_forever else time.monotonic() + timeout
        while not entry.event.wait(timeout=0.05 if deadline is None else min(0.05, max(0, deadline - time.monotonic()))):
            if should_stop and should_stop():
                return "CANCELLED: the user cancelled this question."
            if deadline is not None and time.monotonic() >= deadline:
                break
        if not entry.event.is_set():
            entry.status = "expired"
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
        if entry.status == "pending":
            entry.status = "cancelled"
        if callable(entry.on_resolved):
            try:
                entry.on_resolved(entry)
            except Exception:  # A failed receipt must not lose the user answer.
                pass


def answer(identity: str, text: str, *, request_id: str | None = None) -> bool:
    """Route a user message to the pending question. True if it was consumed."""
    key = identity or "cli:local"
    with _LOCK:
        entry = _PENDING.get(key)
        if entry is None or entry.event.is_set() or (request_id is not None and entry.request_id != request_id):
            return False
        entry.answer = str(text or "").strip()[:MAX_ANSWER_CHARS]
        entry.status = "answered"
        entry.event.set()
    return True


def answer_option(identity: str, index: int, *, request_id: str | None = None) -> str | None:
    """Answer by option index (button tap). Returns the chosen text, or None."""
    key = identity or "cli:local"
    with _LOCK:
        entry = _PENDING.get(key)
        if entry is None or entry.event.is_set() or (request_id is not None and entry.request_id != request_id):
            return None
        if index < 0 or index >= len(entry.options):
            return None
        chosen = entry.options[index]
        entry.answer = chosen
        entry.status = "answered"
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
        entry.status = "cancelled"
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