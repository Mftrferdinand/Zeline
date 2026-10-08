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

import re
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
#: Ceiling for the full-detail text delivered ahead of a truncated question
#: picker. One Telegram message holds 4096 chars; staying under it keeps the
#: detail to a single message, so a long command never spams the chat.
MAX_DETAIL_CHARS = 3900


@dataclass
class PendingQuestion:
    identity: str
    question: str
    options: tuple[str, ...]
    created_at: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    answer: str = ""
    cancelled: bool = False
    #: The untruncated question text, set only when ``question`` was cut down
    #: to ``MAX_QUESTION_CHARS``. Renderers deliver this as a code block
    #: *before* the picker so the operator can inspect the full text —
    #: notably the full shell command behind an approval — before deciding.
    #: Empty when nothing was truncated.
    full_text: str = ""


def detail_chunks(text: str) -> list[str]:
    """Split over-long detail text into single-message chunks.

    Normally one chunk: the approval summary already caps its first argument
    at ``MAX_DETAIL_CHARS``. Chunking is the backstop for a model calling
    ``ask_user`` directly with an unbounded question.
    """
    if len(text) <= MAX_DETAIL_CHARS:
        return [text]
    return [text[i : i + MAX_DETAIL_CHARS] for i in range(0, len(text), MAX_DETAIL_CHARS)]


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
    full = str(question or "").strip()
    if not full:
        return "ERROR ask_user: question is empty."
    choices = normalize_options(options)
    if len(full) > MAX_QUESTION_CHARS:
        # Picker shows a summary; the full text rides along on the entry so
        # the renderer can deliver it as a code block BEFORE the picker.
        # The "…" marks the summary as truncated — a silent cut is exactly
        # what makes blind approvals possible.
        text = full[: MAX_QUESTION_CHARS - 1] + "…"
        full_text = full
    else:
        text, full_text = full, ""

    with _LOCK:
        if key in _PENDING:
            return (
                "ERROR ask_user: a question is already awaiting the user's answer. "
                "Wait for it instead of asking again."
            )
        entry = PendingQuestion(
            identity=key, question=text, options=choices, full_text=full_text
        )
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
    """Route a user message to the pending question. True if it was consumed.

    Bila pesannya cuma nomor pilihan ("2", "2.", "opsi 2") dan pertanyaannya
    punya opsi, yang disimpan adalah TEKS opsi itu — bukan angka mentah. User
    yang mengetik nomor harus mendapat hasil yang sama dengan menekan tombolnya,
    sama seperti picker pada umumnya.
    """
    key = identity or "cli:local"
    raw = str(text or "").strip()
    with _LOCK:
        entry = _PENDING.get(key)
        if entry is None or entry.event.is_set():
            return False
        entry.answer = _resolve_choice(entry, raw)[:MAX_ANSWER_CHARS]
    entry.event.set()
    return True


#: "2", "2)", "2.", "#2", "opsi 2", "pilihan 3", "option 1" — semua menandai
#: pilihan baris ke-N. Angka di TENGAH kalimat tidak dianggap pilihan, supaya
#: "tambah 2 file" tetap jadi jawaban bebas, bukan tap implisit.
_CHOICE_NUMBER_RE = re.compile(
    r"^\s*(?:#|no\.?\s*|opsi\s*|pilihan\s*|option\s*)?(\d{1,2})\s*[.)\]]?\s*$",
    re.IGNORECASE,
)


def _resolve_choice(entry: PendingQuestion, text: str) -> str:
    """Terjemahkan balasan teks menjadi opsi bila cocok; jika tidak, apa adanya."""
    options = entry.options
    if not options:
        return text
    match = _CHOICE_NUMBER_RE.match(text)
    if not match:
        return text
    index = int(match.group(1)) - 1
    if 0 <= index < len(options):
        return options[index]
    return text


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


#: Marker suffix for worker sub-session identities: "{parent}::wkr<id>".
#: A worker spawned from a WebChat session asks under
#: "webchat:<chat_id>::wkr<id>", while the fail-fast deny renderer is
#: registered per turn for the parent identity "webchat:<chat_id>" only.
_WORKER_MARKER = "::wkr"

#: Fail-closed verdict returned when a channel renderer raises. A broken
#: channel must never strand the tool on the event wait (the hang the
#: WebChat approval work removed): "deny" is the only safe default.
#: approvals.parse_verdict maps anything outside allow/allow-session to
#: "deny"; the leading "Deny" makes the verdict obvious to the model too.
_RENDERER_FAILURE_DENY = (
    "Deny — the approval channel failed before the question reached the "
    "operator (renderer error), so the request is DENIED rather than left "
    "waiting for an answer that may never arrive. Route the request "
    "through a working channel to get a real answer."
)


def _channel_for(identity: str) -> object | None:
    """Resolve the renderer for an identity, worker-prefix aware.

    Exact identity first. For a WebChat worker identity
    ``webchat:<chat_id>::wkr<...>``, fall back to the parent identity
    ``webchat:<chat_id>`` so the worker gets the same fail-fast renderer
    registered for its parent turn. Unknown identities return None and the
    caller takes the async wait path.
    """
    with _LOCK:
        renderer = _CHANNELS.get(identity)
        if renderer is not None:
            return renderer
        if identity.startswith("webchat:") and _WORKER_MARKER in identity:
            return _CHANNELS.get(identity.split(_WORKER_MARKER, 1)[0])
        return None


def _deliver(entry: PendingQuestion) -> str | None:
    renderer = _channel_for(entry.identity)
    if renderer is None:
        return None
    try:
        return renderer(entry)  # type: ignore[operator]
    except Exception as exc:  # noqa: BLE001 — fail closed, never strand the tool
        # A renderer that raises cannot deliver the question and can no
        # longer be trusted to wake this wait either: falling back to
        # event.wait would hang the session on a dead channel. Deny instead
        # (fail-closed). The identity is logged for debugging; no question
        # content is included (it may hold a full shell command).
        print(
            f"  [interaction] renderer for {entry.identity!r} raised "
            f"{type(exc).__name__}; failing closed (deny)",
            flush=True,
        )
        return _RENDERER_FAILURE_DENY
