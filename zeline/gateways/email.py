"""Email gateway for Zeline: IMAP listener + SMTP sender.

Reads incoming mail via IMAP (IDLE with polling fallback), forwards mail from
allowed senders to the agent loop, and replies via SMTP. The agent can also
send mail at any time through the ``email_send`` tool.

Config file: ``~/.zeline/gateways/email.json`` (written with mode 0600 —
passwords are never logged).

Required keys: ``imap_host``, ``imap_user``, ``imap_pass``,
``smtp_host``, ``smtp_user``, ``smtp_pass``.
Optional keys: ``imap_port`` (default 993), ``imap_ssl`` (default true),
``imap_mailbox`` (default "INBOX"), ``smtp_port`` (default 465),
``smtp_ssl`` (default true; false = STARTTLS on smtp_port, e.g. 587),
``from_addr`` (default smtp_user), ``poll_interval`` (default 60, seconds,
used when the server does not support IDLE), ``idle_timeout`` (default 1740,
seconds; servers usually drop IDLE after ~29 min), ``allowed_senders``
(list of addresses; empty = accept from anyone — NOT recommended),
``auto_reply`` (default true), ``max_fetch_bytes`` (default 25MB —
messages larger than this are marked seen and skipped, never fetched).
Outgoing mail always carries ``Auto-Submitted: auto-replied`` (RFC 3834)
so auto-reply loops between agents are impossible.

SECURITY NOTES (read before enabling):
- The ``From`` header is trivially spoofable by any sender. ``allowed_senders``
  is an allowlist, NOT authentication — treat it as a first filter only.
  Use a dedicated, non-public email address for this gateway (never publish it),
  and rotate it if spam arrives. Consider also filtering on
  ``Authentication-Results``: messages with ``dkim=fail`` or ``spf=fail``
  are rejected by default (see ``_should_process``).
- Incoming mail bodies are untrusted input. They are passed through
  ``injection_filter.filter_tool_result`` (injected instructions get flagged
  for the agent) before reaching the agent loop — but review anything the
  agent acts on from email with extra care.
"""
from __future__ import annotations

import imaplib
import json
import os
import re
import smtplib
import socket
import threading
import time
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parseaddr
from pathlib import Path
from typing import Any

from zeline import injection_filter

_GATEWAY_DIR = Path.home() / ".zeline" / "gateways"
_CONFIG_PATH = _GATEWAY_DIR / "email.json"

# Auto-reply / bulk headers that mark a message as machine-generated.
_AUTO_SUBMITTED_SKIP = True  # any Auto-Submitted value other than "no"
_PRECEDENCE_SKIP = {"bulk", "list", "junk"}
_MAX_BODY_CHARS = 20000

# Max bytes fetched per message before skipping (memory-DoS guard for large
# attachments). Overridable via config key "max_fetch_bytes".
_DEFAULT_MAX_FETCH_BYTES = 25 * 1024 * 1024


def info() -> dict[str, str]:
    return {
        "label": "Email",
        "hint": "IMAP inbox listener + SMTP replies (config in ~/.zeline/gateways/email.json).",
    }


def config_path() -> Path:
    return _CONFIG_PATH


def _load_file_config() -> dict[str, Any]:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict[str, Any]) -> Path:
    """Write gateway config with mode 0600 (passwords must not be world-readable)."""
    _GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
    # Write atomically-ish: temp file then rename, then chmod.
    tmp = _GATEWAY_DIR / "email.json.tmp"
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(_CONFIG_PATH)
    os.chmod(_CONFIG_PATH, 0o600)
    return _CONFIG_PATH


def _merged_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay the on-disk credential file over the passed gateway cfg."""
    merged = dict(_load_file_config())
    merged.update({k: v for k, v in (cfg or {}).items() if v is not None})
    return merged


def merged_cfg_max_fetch(cfg: dict[str, Any]) -> int:
    """Per-message fetch cap in bytes (config ``max_fetch_bytes``, default 25MB)."""
    try:
        value = int(cfg.get("max_fetch_bytes", _DEFAULT_MAX_FETCH_BYTES))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_FETCH_BYTES
    return max(1 * 1024 * 1024, min(value, 500 * 1024 * 1024))


def validate_config(cfg: dict[str, Any]) -> list[str]:
    merged = _merged_config(cfg)
    errors = []
    for key in ("imap_host", "imap_user", "imap_pass", "smtp_host", "smtp_user", "smtp_pass"):
        if not str(merged.get(key, "")).strip():
            errors.append(f"{key} is required (set it in ~/.zeline/gateways/email.json)")
    # A2-M1: empty allowlist = accept mail from anyone. Refuse to run.
    allowed = merged.get("allowed_senders", [])
    if not isinstance(allowed, list) or not [a for a in allowed if str(a).strip()]:
        errors.append(
            "allowed_senders tidak boleh kosong — isi dengan daftar email yang diizinkan "
            "(set it in ~/.zeline/gateways/email.json)"
        )
    return errors


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------

def _addr(header_value: str) -> str:
    """Extract the bare address from a From/To header, lowercased."""
    return parseaddr(header_value)[1].strip().lower()


def _decode(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def _is_valid_email(addr: str) -> bool:
    return bool(re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", addr or ""))


# ---------------------------------------------------------------------------
# SMTP sending
# ---------------------------------------------------------------------------

def send_email(
    cfg: dict[str, Any],
    to: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    """Send one email via SMTP. Returns the sent Message-ID.

    Raises ValueError/RuntimeError on config or transport errors.
    Never logs the password.
    """
    merged = _merged_config(cfg)
    errors = validate_config(merged)
    # Only SMTP-side keys matter here.
    smtp_errors = [e for e in errors if e.startswith("smtp_")]
    if smtp_errors:
        raise ValueError("; ".join(smtp_errors))

    to_addrs = [a for _, a in getaddresses([to]) if _is_valid_email(a)]
    if not to_addrs:
        raise ValueError(f"Invalid recipient address: {to!r}")

    from_addr = str(merged.get("from_addr") or merged["smtp_user"]).strip()
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = str(subject)[:998]
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to.strip()
    if references:
        msg["References"] = references.strip()
    msg["Message-ID"] = make_msgid()
    # RFC 3834 §3.1.8: mark our mail as auto-generated so other agents —
    # including another Zeline instance — refuse to auto-reply to it.
    # Without this, two instances with each other in allowed_senders
    # ping-pong forever, each reply triggering the other's agent loop.
    msg["Auto-Submitted"] = "auto-replied"
    msg.set_content(str(body))

    host = str(merged["smtp_host"]).strip()
    port = int(merged.get("smtp_port", 465))
    use_ssl = bool(merged.get("smtp_ssl", True))
    user = str(merged["smtp_user"])
    password = str(merged["smtp_pass"])

    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            smtp = smtplib.SMTP(host, port, timeout=30)
            smtp.starttls()
        with smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise RuntimeError(f"SMTP send failed: {exc}") from exc
    return str(msg["Message-ID"] or "")


def tool_send(to: str, subject: str, body: str) -> str:
    """Entry point for the ``email_send`` agent tool (reads on-disk config)."""
    try:
        mid = send_email({}, to, subject, body)
    except (ValueError, RuntimeError) as exc:
        return f"ERROR: {exc}"
    return f"Email sent (Message-ID {mid})."


# ---------------------------------------------------------------------------
# IMAP receiving
# ---------------------------------------------------------------------------

def _connect_imap(cfg: dict[str, Any]) -> imaplib.IMAP4:
    host = str(cfg["imap_host"]).strip()
    port = int(cfg.get("imap_port", 993))
    use_ssl = bool(cfg.get("imap_ssl", True))
    try:
        if use_ssl:
            imap = imaplib.IMAP4_SSL(host, port, timeout=30)
        else:
            imap = imaplib.IMAP4(host, port, timeout=30)
        imap.login(str(cfg["imap_user"]), str(cfg["imap_pass"]))
        return imap
    except (imaplib.IMAP4.error, OSError) as exc:
        raise RuntimeError(f"IMAP connect/login failed: {exc}") from exc


def _idle_wait(imap: imaplib.IMAP4, timeout_secs: float) -> bool:
    """Wait up to ``timeout_secs`` for new mail using IMAP IDLE.

    Returns True when the server reported EXISTS/RECENT (mail may have
    arrived), False on timeout / IDLE unsupported / connection problem.
    """
    try:
        tag = imap._new_tag().decode("ascii")  # noqa: SLF001 — stable imaplib private API
    except Exception:
        return False
    try:
        imap.send(f"{tag} IDLE\r\n".encode("ascii"))
        cont = imap.readline().decode("utf-8", "replace")
    except Exception:
        return False
    if not cont.startswith("+"):
        # IDLE not accepted; drain the tagged response and report fallback.
        try:
            imap._get_tagged_response(tag)  # noqa: SLF001
        except Exception:
            pass
        return False

    sock = imap.sock
    if sock is None:
        # Connection dropped between readline and here — treat as no mail.
        return False
    old_timeout = sock.gettimeout()
    got_mail = False
    try:
        sock.settimeout(timeout_secs)
        while True:
            try:
                line = imap.readline().decode("utf-8", "replace")
            except socket.timeout:
                break
            if not line:
                break
            upper = line.upper()
            if "EXISTS" in upper or "RECENT" in upper:
                got_mail = True
                break
    finally:
        try:
            sock.settimeout(old_timeout)
        except Exception:
            pass
        try:
            imap.send(b"DONE\r\n")
            imap._get_tagged_response(tag)  # noqa: SLF001
        except Exception:
            pass
    return got_mail


def _fetch_unseen_uids(imap: imaplib.IMAP4) -> list[bytes]:
    typ, data = imap.uid("search", None, "UNSEEN")
    if typ != "OK":
        return []
    return (data[0] or b"").split()


def _fetch_rfc822_size(imap: imaplib.IMAP4, uid: bytes) -> int | None:
    """Return the message's RFC822.SIZE without fetching the body.

    Returns None when the server does not answer (caller must decide:
    we fail closed and skip the message).
    """
    try:
        typ, data = imap.uid("fetch", uid, "(RFC822.SIZE)")
        if typ != "OK" or not data or not data[0]:
            return None
        first = data[0]
        raw = first[0] if isinstance(first, tuple) else first
        m = re.search(rb"RFC822\.SIZE\s+(\d+)", bytes(raw))
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _parse_message(raw: bytes) -> dict[str, Any]:
    """Parse raw RFC822 bytes into a plain dict the agent loop can consume."""
    from email import message_from_bytes

    m = message_from_bytes(raw, policy=policy.default)
    from_addr = _addr(_decode(m.get("From", "")))
    subject = _decode(m.get("Subject", "")).strip()
    date = _decode(m.get("Date", "")).strip()
    msg_id = str(m.get("Message-ID", "")).strip()
    auto_submitted = str(m.get("Auto-Submitted", "")).strip().lower()
    precedence = str(m.get("Precedence", "")).strip().lower()
    xars = str(m.get("X-Auto-Response-Suppress", "")).strip()
    # Authentication-Results (RFC 8601): used to detect DKIM/SPF failures.
    # NOTE: not all MTAs add this header; absence means "no data", not "clean".
    auth_results = str(m.get("Authentication-Results", "") or "")

    # Extract text body, prefer text/plain.
    body_parts: list[str] = []
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_disposition() in ("attachment", "inline"):
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    body_parts.append(part.get_content())
                except Exception:
                    pass
        if not body_parts:
            for part in m.walk():
                if part.get_content_type() == "text/html":
                    continue  # plain preferred; skip html fallback noise
    else:
        try:
            if m.get_content_type().startswith("text/"):
                body_parts.append(m.get_content())
        except Exception:
            pass
    body = "\n".join(p for p in body_parts if isinstance(p, str)).strip()
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS] + "\n[...truncated...]"
    return {
        "from": from_addr,
        "from_header": _decode(m.get("From", "")),
        "subject": subject,
        "date": date,
        "message_id": msg_id,
        "auto_submitted": auto_submitted,
        "precedence": precedence,
        "x_auto_response_suppress": xars,
        "auth_results": auth_results,
        "body": body,
    }


# Cap on remembered reply Message-IDs: bounds the dedup set so a long-lived
# listener cannot grow memory without limit. When the cap is exceeded, an
# arbitrary half is evicted (sets are unordered, so "oldest" has no meaning —
# eviction only risks re-replying to a very old message once).
_MAX_REPLIED_MSGIDS = 10_000


def _remember_replied(replied_msgids: set[str], msgid: str) -> None:
    replied_msgids.add(msgid)
    if len(replied_msgids) > _MAX_REPLIED_MSGIDS:
        for old in list(replied_msgids)[: len(replied_msgids) // 2]:
            replied_msgids.discard(old)


def _should_process(parsed: dict[str, Any], cfg: dict[str, Any], own_addr: str) -> tuple[bool, str]:
    """Decide whether an incoming message goes to the agent.

    Returns (process, reason). ``reason`` is for debug logs only.
    """
    sender = parsed["from"]
    if not sender:
        return False, "no-from"
    if sender == own_addr:
        return False, "own-address"
    # Never reply to machine-generated mail (loop / backscatter protection).
    if parsed.get("auto_submitted", "") and parsed.get("auto_submitted", "") != "no":
        return False, "auto-submitted"
    if parsed.get("precedence", "") in _PRECEDENCE_SKIP:
        return False, f"precedence-{parsed.get('precedence', '')}"
    suppress = parsed.get("x_auto_response_suppress", "").lower()
    if "autoreply" in suppress or suppress.strip() == "all":
        return False, "x-auto-response-suppress"
    # Reject mail that provably failed DKIM/SPF validation (spoofed sender).
    # Missing header = no data (not all MTAs add it) — pass through to the
    # allowed_senders check below.
    auth = str(parsed.get("auth_results", "") or "")
    if re.search(r"\b(dkim|spf)\s*=\s*fail\b", auth, re.I):
        return False, "auth-fail"
    allowed = {str(a).strip().lower() for a in cfg.get("allowed_senders", []) if str(a).strip()}
    if allowed and sender not in allowed:
        return False, "sender-not-allowed"
    return True, "ok"


def _format_for_agent(parsed: dict[str, Any]) -> str:
    return (
        f"[Email from {parsed['from_header']}]\n"
        f"Subject: {parsed['subject']}\n"
        f"Date: {parsed['date']}\n\n"
        f"{parsed['body']}"
    ).strip()


def _process_unseen(
    imap: imaplib.IMAP4,
    cfg: dict[str, Any],
    sessions: Any,
    profile: str,
    workspace: Any,
    replied_msgids: set[str],
    own_addr: str,
) -> int:
    """Fetch UNSEEN, dispatch allowed mail to the agent, reply via SMTP.

    Returns the number of messages dispatched to the agent.
    """
    max_fetch = int(merged_cfg_max_fetch(cfg))
    # A2-M1: cap dispatches per cycle — a flooded mailbox must not
    # trigger unbounded agent turns in one pass.
    max_dispatch_per_cycle = 20
    dispatched = 0
    for uid in _fetch_unseen_uids(imap):
        if dispatched >= max_dispatch_per_cycle:
            try:
                print(
                    f"  [email] dispatch cap reached ({max_dispatch_per_cycle}/cycle); "
                    "remaining mail left for next cycle",
                    flush=True,
                )
            except Exception:
                pass
            break
        try:
            # Size guard first: a 100MB attachment amplifies ~7.6x in RAM
            # during base64 decode + parse. Never pull the full body blind.
            size = _fetch_rfc822_size(imap, uid)
            if size is None:
                # Fail closed: mark seen (avoid re-fetch loops) and skip.
                try:
                    imap.uid("store", uid, "+FLAGS", "(\\Seen)")
                except Exception:
                    pass
                continue
            if size > max_fetch:
                try:
                    imap.uid("store", uid, "+FLAGS", "(\\Seen)")
                except Exception:
                    pass
                continue  # oversized: ignored, not re-fetched

            typ, data = imap.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not data or not data[0]:
                continue
            raw = data[0][1]
            if not isinstance(raw, bytes):
                continue
            parsed = _parse_message(raw)
            # Mark seen regardless: "ignored" mail must not be re-fetched forever.
            try:
                imap.uid("store", uid, "+FLAGS", "(\\Seen)")
            except Exception:
                pass

            ok, reason = _should_process(parsed, cfg, own_addr)
            if not ok:
                continue
            if parsed["message_id"] and parsed["message_id"] in replied_msgids:
                continue  # already handled this message earlier

            identity = f"email:{parsed['from']}"
            try:
                # Email body is untrusted input: flag injected instructions
                # for the agent before dispatching (EMAIL-1).
                agent_text = injection_filter.filter_tool_result(_format_for_agent(parsed))
                reply = sessions.send(identity, agent_text, profile, workspace)
            except Exception as exc:
                reply = f"Error: {exc}"
            dispatched += 1

            if cfg.get("auto_reply", True) and reply and reply.strip():
                try:
                    send_email(
                        cfg,
                        to=parsed["from"],
                        subject=f"Re: {parsed['subject']}" if not parsed["subject"].lower().startswith("re:") else parsed["subject"],
                        body=reply,
                        in_reply_to=parsed["message_id"] or None,
                    )
                    if parsed["message_id"]:
                        _remember_replied(replied_msgids, parsed["message_id"])
                except Exception:
                    pass  # reply failure must not kill the listener
        except Exception:
            continue
    return dispatched


# ---------------------------------------------------------------------------
# Gateway entry point
# ---------------------------------------------------------------------------

def start(sessions: Any, cfg: dict[str, Any], stop_event: threading.Event, ready: Any = None) -> None:
    errors = validate_config(cfg)
    if errors:
        raise RuntimeError("; ".join(errors))
    merged = _merged_config(cfg)
    profile = str(merged.get("tool_profile", "safe"))
    workspace = merged.get("workspace")
    mailbox = str(merged.get("imap_mailbox", "INBOX") or "INBOX")
    poll_interval = max(10, int(merged.get("poll_interval", 60)))
    idle_timeout = max(60, min(int(merged.get("idle_timeout", 1740)), 1740))
    own_addr = str(merged.get("from_addr") or merged["imap_user"]).strip().lower()
    replied_msgids: set[str] = set()
    backoff = 10  # A2-L3: seconds, doubles per failure, resets on success
    if ready:
        try:
            ready.set()
        except Exception:
            pass

    while not stop_event.is_set():
        imap = None
        try:
            imap = _connect_imap(merged)
            typ, _ = imap.select(mailbox, readonly=False)
            if typ != "OK":
                raise RuntimeError(f"IMAP SELECT {mailbox!r} failed")
            # Process anything already waiting before entering IDLE.
            _process_unseen(imap, merged, sessions, profile, workspace, replied_msgids, own_addr)
            while not stop_event.is_set():
                # Prefer IDLE; fall back to periodic NOOP+poll when unsupported.
                new_mail = _idle_wait(imap, idle_timeout)
                if stop_event.is_set():
                    break
                if not new_mail:
                    # Timeout or no IDLE support: poll explicitly.
                    try:
                        imap.noop()
                    except Exception:
                        break  # reconnect
                    _process_unseen(imap, merged, sessions, profile, workspace, replied_msgids, own_addr)
                    if stop_event.wait(poll_interval):
                        break
                    continue
                _process_unseen(imap, merged, sessions, profile, workspace, replied_msgids, own_addr)
            backoff = 10  # A2-L3: connection healthy — reset backoff
        except Exception:
            if stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, 300)  # A2-L3: exponential, cap 300s
        finally:
            if imap is not None:
                try:
                    imap.close()
                except Exception:
                    pass
                try:
                    imap.logout()
                except Exception:
                    pass
