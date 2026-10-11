"""Meeting agents: join Zoom/Google Meet/Teams, take notes, leave.

MVP approach (deliberately not over-engineered):

- Joining happens through the operator's own headless browser
  (``zeline.browser.BrowserSession``, CDP-based, no new dependency).
  There is no free official API to *join* a Zoom/Meet/Teams call as a
  guest, so browser automation is the honest path.
- The agent clicks the well-known "join from browser" / "join now"
  buttons. Anything requiring a login, CAPTCHA, or waiting-room
  admission is reported plainly instead of faked — a meeting that needs
  a human stays "waiting", never "joined".
- Notes come from two sources: on-page live captions (best effort via
  JS) and an optional audio file transcribed with ``voice.transcribe``
  (local faster-whisper, offline).

Security notes:

- Meeting URLs often carry a password (``?pwd=...``). It is redacted
  from every message, log line, and state file this module produces.
  Use :func:`redact_url` before displaying any URL.
- URLs are operator-supplied, but the tool can be called by the agent
  with a URL from untrusted content, so ``join_meeting`` enforces the
  same SSRF guard as the browser tool (no loopback/private targets,
  https only).
- State lives in ``~/.zeline/meetings/`` (dir 0700, files 0600).
  ``meeting_id`` values are generated, never taken from user input
  verbatim — lookups sanitize them first.
"""
from __future__ import annotations

import json
import errno
import os
import re
import threading
import time
from contextlib import suppress as _suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

_MEETINGS_DIR = Path.home() / ".zeline" / "meetings"

# Host suffixes per platform. Checked with endswith() so regional
# subdomains (us05web.zoom.us, etc.) keep working.
_PLATFORMS: dict[str, tuple[str, ...]] = {
    "zoom": ("zoom.us",),
    "meet": ("meet.google.com",),
    "teams": ("teams.microsoft.com", "teams.live.com"),
}

# Query params that must never appear in logs / state / user-visible text.
_SENSITIVE_PARAMS = {"pwd", "password", "passcode", "token"}

_ID_RE = re.compile(r"[^a-zA-Z0-9_-]")


class MeetingError(RuntimeError):
    """Something went wrong that the model should be told about plainly."""


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def detect_platform(url: str) -> str:
    """Return 'zoom', 'meet', 'teams', or 'unknown' for a meeting URL."""
    host = (urlparse(url or "").hostname or "").lower()
    for platform, suffixes in _PLATFORMS.items():
        if any(host == s or host.endswith("." + s) for s in suffixes):
            return platform
    return "unknown"


def redact_url(url: str) -> str:
    """Redact password-like query params (e.g. Zoom ?pwd=...) for display."""
    try:
        parsed = urlparse(url or "")
        if not parsed.query:
            return url
        params = [
            (k, "REDACTED" if k.lower() in _SENSITIVE_PARAMS else v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunparse(parsed._replace(query=urlencode(params)))
    except Exception:
        return "[unparseable url]"


def _validate_url(url: str) -> str:
    """Fail closed on non-https / internal / unknown-platform URLs."""
    from zeline import browser as browser_module

    url = (url or "").strip()
    if not url:
        raise MeetingError("URL meeting kosong.")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise MeetingError(
            f"hanya URL https yang didukung, dapat: {parsed.scheme or '(tanpa skema)'}"
        )
    if browser_module._is_blocked_host(url):
        raise MeetingError(
            "URL menunjuk ke host internal/loopback — ditolak demi keamanan (SSRF)."
        )
    if detect_platform(url) == "unknown":
        raise MeetingError(
            "platform tidak dikenal. Dukung: Zoom (zoom.us), "
            "Google Meet (meet.google.com), Teams (teams.microsoft.com)."
        )
    return url


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _meetings_dir() -> Path:
    d = _MEETINGS_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MeetingError(f"gagal menyiapkan direktori meetings: {exc}")
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _sanitize_id(meeting_id: str) -> str:
    cleaned = _ID_RE.sub("", meeting_id or "")
    if not cleaned or len(cleaned) > 64:
        raise MeetingError(f"meeting_id tidak valid: {meeting_id!r}")
    return cleaned


def _state_path(meeting_id: str) -> Path:
    return _meetings_dir() / f"{_sanitize_id(meeting_id)}.json"


def _notes_path(meeting_id: str) -> Path:
    return _meetings_dir() / f"{_sanitize_id(meeting_id)}.md"


def _append_notes(meeting_id: str, entry: str) -> None:
    """Append ke notes file dengan O_NOFOLLOW — tolak symlink secara atomik."""
    notes = _notes_path(meeting_id)
    try:
        fd = os.open(notes, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    except FileNotFoundError:
        # Seharusnya tidak terjadi (O_CREAT), tapi tangani dengan jelas.
        raise MeetingError(f"gagal menulis catatan meeting '{meeting_id}'.")
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ELOOP:
            raise MeetingError("notes meeting adalah symlink — ditolak demi keamanan.")
        raise MeetingError(f"gagal menulis catatan meeting: {exc}")
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError as exc:
        raise MeetingError(f"gagal menulis catatan meeting: {exc}")


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _load_state(meeting_id: str) -> dict[str, Any]:
    path = _state_path(meeting_id)
    # O_NOFOLLOW: tolak symlink secara atomik — tidak ada race window
    # antara cek dan baca.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise MeetingError(
            f"meeting '{_sanitize_id(meeting_id)}' tidak ditemukan. "
            "Gunakan meeting_join dulu atau cek meeting list."
        )
    except OSError as exc:
        # ELOOP = symlink (O_NOFOLLOW), EACCES dsb.
        if getattr(exc, "errno", None) == errno.ELOOP:
            raise MeetingError("state meeting adalah symlink — ditolak demi keamanan.")
        raise MeetingError(f"gagal membaca state meeting: {exc}")
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise MeetingError(f"state meeting rusak: {exc}")


def _save_state(state: dict[str, Any]) -> None:
    _write_json_atomic(_state_path(state["id"]), state)


def list_meetings(limit: int = 100) -> list[dict[str, Any]]:
    """List known meetings (newest first, at most *limit*). Passwords stay redacted."""
    d = _meetings_dir()
    out: list[dict[str, Any]] = []
    try:
        limit = max(0, int(limit or 0))
    except (TypeError, ValueError):
        raise MeetingError(f"limit harus angka, bukan {limit!r}.")
    for path in sorted(d.glob("mtg_*.json"), reverse=True):
        if len(out) >= limit:
            break
        if path.is_symlink():
            continue
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "id": st.get("id", path.stem),
                "platform": st.get("platform", "?"),
                "status": st.get("status", "?"),
                "joined_at": st.get("joined_at", ""),
                "url": redact_url(st.get("url", "")),
                "name": st.get("name", ""),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Browser sessions (in-memory; a restart orphans them on purpose)
# ---------------------------------------------------------------------------

_SESSIONS: dict[str, Any] = {}
_SESSIONS_LOCK = threading.Lock()


def _new_id() -> str:
    import secrets

    return f"mtg_{time.strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(3)}"


# Per-platform join flows. Each step is best-effort: (kind, arg) where kind
# is "click" or "type". Steps that find nothing are skipped, not fatal.
_JOIN_FLOWS: dict[str, list[tuple[str, str]]] = {
    "zoom": [
        # "Join from Your Browser" link on the launch page
        ("click", "a:has-text('Join from Your Browser')"),
        ("click", "a[id='zoom-ui-frame'] a"),
        # name field then join button on the web client
        ("type", "input#input-for-name"),
        ("click", "button:has-text('Join')"),
    ],
    "meet": [
        ("click", "button:has-text('Ask to join')"),
        ("click", "button:has-text('Join now')"),
    ],
    "teams": [
        ("click", "button:has-text('Continue on this browser')"),
        ("click", "button:has-text('Join now')"),
    ],
}


def _click_text(session: Any, text: str) -> bool:
    """Click an element by visible text via JS. Returns True if clicked."""
    js = (
        "(t => {"
        " const els = [...document.querySelectorAll('button,a,[role=\"button\"]')];"
        " const el = els.find(e => (e.innerText||'').trim().toLowerCase().includes(t.toLowerCase()));"
        " if (!el) return false;"
        " el.click(); return true;"
        "})(%s)" % json.dumps(text)
    )
    try:
        return bool(session.evaluate(js))
    except Exception:
        return False


def _type_first_input(session: Any, text: str) -> bool:
    js = (
        "(t => {"
        " const el = document.querySelector('input[type=\"text\"], input:not([type])');"
        " if (!el) return false;"
        " el.focus(); document.execCommand('selectAll', false, null);"
        " document.execCommand('insertText', false, t);"
        " el.dispatchEvent(new Event('input', {bubbles:true}));"
        " return true;"
        "})(%s)" % json.dumps(text)
    )
    try:
        return bool(session.evaluate(js))
    except Exception:
        return False


_CAPTIONS_JS = """(() => {
  const sels = [
    '[data-captions]', '.captions-text', '.a4cQT', '.Tz6S6d',
    '[aria-label*="Captions" i]', '[aria-label*="Caption" i]',
    '.caption-window', '#caption-window'
  ];
  const out = [];
  document.querySelectorAll(sels.join(',')).forEach(e => {
    const t = (e.innerText || '').trim();
    if (t) out.push(t);
  });
  return [...new Set(out)].join('\\n');
})()"""


def _capture_captions(session: Any) -> str:
    try:
        text = session.evaluate(_CAPTIONS_JS)
        return str(text or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def join_meeting(url: str, name: str = "") -> str:
    """Join a meeting via the operator's headless browser.

    Returns the meeting_id. Never raises for join-flow hiccups — instead
    the meeting status is 'waiting' with an honest note about what still
    needs a human (login, CAPTCHA, waiting-room admission).
    Raises MeetingError only for invalid URLs / missing browser.
    """
    from zeline import browser as browser_module

    url = _validate_url(url)
    platform = detect_platform(url)
    name = (name or "").strip()[:100]

    if not browser_module.enabled():
        raise MeetingError("browser tool dimatikan (tools.browser = false).")
    if not browser_module.find_browser():
        raise MeetingError(
            "tidak ada browser Chromium/Chrome terpasang. "
            "Pasang dulu atau set tools.browser_binary di config."
        )

    meeting_id = _new_id()
    try:
        session = browser_module.BrowserSession()
    except Exception as exc:
        raise MeetingError(f"gagal membuat sesi browser: {exc.__class__.__name__}: {exc}")
    steps: list[str] = []
    try:
        session.start()
        steps.append("browser started")
        session.open(url, wait=4.0)
        steps.append(f"opened {redact_url(url)}")
        time.sleep(2.0)

        joined_click = False
        for kind, arg in _JOIN_FLOWS.get(platform, []):
            try:
                if kind == "click":
                    # arg like "button:has-text('Join now')" -> extract text
                    m = re.search(r"has-text\('([^']+)'\)", arg)
                    label = m.group(1) if m else arg
                    if _click_text(session, label):
                        steps.append(f"clicked '{label}'")
                        if "join" in label.lower():
                            joined_click = True
                        time.sleep(2.5)
                elif kind == "type" and name:
                    if _type_first_input(session, name):
                        steps.append("entered display name")
                        time.sleep(1.0)
            except Exception as exc:  # one bad step must not kill the flow
                steps.append(f"step skipped ({exc.__class__.__name__})")

        # Honest status: we clicked something join-like, but the call may
        # still sit in a lobby / login wall. Never claim more than we know.
        page_text = ""
        try:
            page_text = session.text(limit=2000).lower()
        except Exception:
            pass
        waiting_markers = ("waiting room", "waiting for the host", "sign in",
                           "please wait", "admit", "lobby")
        looks_waiting = any(m in page_text for m in waiting_markers)
        status = "waiting" if (looks_waiting or not joined_click) else "joined"
    except Exception as exc:
        with _suppress():
            session.stop()
        raise MeetingError(f"gagal membuka meeting: {exc}")

    state = {
        "id": meeting_id,
        "platform": platform,
        "url": redact_url(url),  # never persist the real pwd
        "name": name,
        "status": status,
        "joined_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "steps": steps,
    }
    try:
        _save_state(state)
        # Session masuk _SESSIONS SETELAH save sukses — kalau save gagal,
        # session tidak orphan (cleanup di except).
        with _SESSIONS_LOCK:
            _SESSIONS[meeting_id] = session
        # Seed the notes file.
        notes = _notes_path(meeting_id)
        notes.write_text(
            f"# Notulen meeting {meeting_id}\n\n"
            f"- Platform: {platform}\n"
            f"- URL: {redact_url(url)}\n"
            f"- Nama: {name or '-'}\n"
            f"- Mulai: {state['joined_at']}\n"
            f"- Status: {status}\n\n---\n\n",
            encoding="utf-8",
        )
        try:
            os.chmod(notes, 0o600)
        except OSError:
            pass
    except Exception as exc:
        # Persistensi gagal: cabut session dari _SESSIONS (kalau sempat masuk)
        # dan stop browser agar tidak jadi proses yatim.
        with _SESSIONS_LOCK:
            _SESSIONS.pop(meeting_id, None)
        with _suppress():
            session.stop()
        if isinstance(exc, MeetingError):
            raise
        raise MeetingError(f"gagal menyimpan state meeting: {exc}")
    return meeting_id


def take_notes(meeting_id: str, audio_path: str | None = None) -> str:
    """Capture notes for a meeting.

    Pulls on-page live captions (best effort) and, if ``audio_path`` is
    given, transcribes it with local faster-whisper. Appends a timestamped
    entry to the meeting's notes file and returns the new entry text.
    """
    state = _load_state(meeting_id)
    if state.get("status") == "left":
        raise MeetingError(f"meeting '{meeting_id}' sudah ditinggalkan.")

    parts: list[str] = []
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(_sanitize_id(meeting_id))
    if session is not None:
        captions = _capture_captions(session)
        if captions:
            parts.append(f"[live captions]\n{captions}")
        else:
            parts.append("[live captions] tidak ada teks caption terdeteksi di halaman.")
    else:
        parts.append(
            "[live captions] sesi browser tidak aktif "
            "(proses di-restart atau meeting belum di-join di sesi ini)."
        )

    if audio_path:
        from zeline import voice as voice_mod

        # Trusted-caller contract: the tool wrapper confines this path to
        # the workspace before we ever get here.
        try:
            text = voice_mod.transcribe(audio_path)
        except voice_mod.VoiceError as exc:
            raise MeetingError(f"transkripsi gagal: {exc}")
        except Exception as exc:
            raise MeetingError(
                f"transkripsi gagal: {exc.__class__.__name__}: {exc}"
            )
        parts.append(f"[transkrip audio: {audio_path}]\n{text or '(tidak ada ucapan terdeteksi)'}")

    entry = (
        f"\n## {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        + "\n\n".join(parts)
        + "\n"
    )
    _append_notes(meeting_id, entry)
    return entry.strip()


def leave_meeting(meeting_id: str) -> str:
    """Leave a meeting: stop its browser session, mark status 'left'."""
    state = _load_state(meeting_id)
    mid = _sanitize_id(meeting_id)
    with _SESSIONS_LOCK:
        session = _SESSIONS.pop(mid, None)
    if session is not None:
        with _suppress():
            session.stop()
    state["status"] = "left"
    state["left_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        _save_state(state)
    except OSError as exc:
        raise MeetingError(f"gagal menyimpan state meeting: {exc}")
    _append_notes(meeting_id, f"\n---\nDitinggalkan: {state['left_at']}\n")
    return f"OK, meninggalkan meeting {mid}."
