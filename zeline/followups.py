"""Read-only, task-scoped follow-ups with quiet, deduplicated Telegram alerts.

A watch is explicit: the agent must have a concrete source it can read. There
is deliberately no keyword inference from task descriptions and no scraping of
unrelated accounts. The scheduler runs this module as a script, without an LLM.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from zeline import config, scheduler, tasks

MAX_WATCHES = 20
MAX_BYTES = 65536
MAX_SENDS_PER_TICK = 2
SEND_TIMEOUT_SECONDS = 8
TTL_SECONDS = 30 * 86400
SCHEDULE = "10m"
_EMAIL_TICK_CACHE: ContextVar[dict | None] = ContextVar("zeline_followup_email_tick", default=None)


def registry_path() -> Path:
    return config.DATA_DIR / "followups" / "owner.json"


_REGISTRY_THREAD_LOCK = threading.RLock()


def _serialized(operation):
    """Keep registry and scheduler mutations in one cross-process critical section.

    The permanent SQLite database is a mutex only; JSON remains the source of
    truth. Never unlink it: concurrent processes must lock the same database.
    Acquire the thread mutex before the SQLite lease because SQLite connections
    in separate threads of one process do not serialize Python callbacks.
    """
    @wraps(operation)
    def locked(*args, **kwargs):
        if operation.__name__ != "poll":
            _owner(args[0] if args else kwargs["identity"])  # Reject non-owner calls before creating lock state.
        with _REGISTRY_THREAD_LOCK:
            directory = registry_path().parent
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            path = directory / "owner-lock.sqlite3"
            # Retry on the next tick rather than spend the whole scheduler deadline
            # waiting behind another process's potentially slow source/delivery I/O.
            with closing(sqlite3.connect(path, timeout=5, isolation_level=None)) as connection:
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
                connection.execute("BEGIN IMMEDIATE")
                cache_token = _EMAIL_TICK_CACHE.set({}) if operation.__name__ == "poll" else None
                try:
                    try:
                        result = operation(*args, **kwargs)
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                    else:
                        connection.execute("COMMIT")
                        return result
                finally:
                    if cache_token is not None:
                        _EMAIL_TICK_CACHE.reset(cache_token)

    return locked


def _owner(identity: str) -> str:
    owner = str((config.GATEWAYS.get("telegram", {}) or {}).get("owner_identity", "") or "").strip()
    if not owner or identity != f"telegram:{owner}" or not owner.isdigit():
        raise ValueError("follow-up watches are only available in the configured owner DM")
    return identity


def _load() -> dict:
    try:
        value = json.loads(registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"watches": []}
    return value if isinstance(value, dict) and isinstance(value.get("watches"), list) else {"watches": []}


def _save(value: dict) -> None:
    dest = registry_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    fd, path = tempfile.mkstemp(prefix="owner-", suffix=".tmp", dir=dest.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            else:
                os.chmod(path, 0o600)
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, dest)
    finally:
        Path(path).unlink(missing_ok=True)


def _task(identity: str, name: str) -> str:
    title = " ".join(name.split())
    if not title or len(title) > 200 or any(c in title for c in "\r\n<>"):
        raise ValueError("task must be a short, plain description")
    for item in tasks.load(identity):
        if item["task"].casefold() == title.casefold() and item["status"] != "cancelled":
            return item["task"]
    raise ValueError("task is not on this chat's board; add it with update_task first")


def _read_numeric(source: str, field: str) -> float | int:
    target = Path(source).expanduser().resolve()
    if not target.is_relative_to(config.DATA_DIR.resolve()) or not target.is_file():
        raise ValueError("JSON snapshot must be a file inside Zeline's private data directory")
    if not field or not field.isidentifier() or len(field) > 64:
        raise ValueError("field must be one top-level JSON numeric key")
    if target.stat().st_size > MAX_BYTES:
        raise ValueError("snapshot exceeds 64 KiB")
    value = json.loads(target.read_text(encoding="utf-8"))
    number = value.get(field) if isinstance(value, dict) else None
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
        raise ValueError("snapshot field must be a finite JSON number")
    return number


def _email_filter(field: str) -> tuple[str, str]:
    sender, separator, subject = field.partition("|")
    sender, subject = sender.strip().casefold(), subject.strip().casefold()
    if (not separator or len(sender) > 254 or len(subject) > 160
            or not re.fullmatch(r"[^\s@|]+@[^\s@|]+\.[^\s@|]+", sender)
            or len(subject) < 3 or "|" in subject):
        raise ValueError("email watch requires exact sender and subject: field='sender@example.com|Subject'")
    return sender, subject


def _email_subject(value: str) -> str:
    subject = value.strip().casefold()
    while re.match(r"^(re|fw|fwd):\s*", subject):
        subject = re.sub(r"^(re|fw|fwd):\s*", "", subject, count=1)
    return subject


def _inbox_envelopes() -> list:
    """Fetch once per poll; filtering stays independent for each watched task."""
    cache = _EMAIL_TICK_CACHE.get()
    if cache is not None:
        if "error" in cache:
            raise cache["error"]
        if "envelopes" in cache:
            return cache["envelopes"]
    try:
        proc = subprocess.run(["himalaya", "envelope", "list", "--page-size", "100", "--json"],
                              capture_output=True, text=True, timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        error = ValueError("email inbox unavailable (Himalaya command failed)")
        if cache is not None:
            cache["error"] = error
        raise error from exc
    if proc.returncode:
        # Himalaya may return credential paths in JSON errors. Never surface that output.
        error = ValueError("email inbox unavailable (check Himalaya authentication)")
        if cache is not None:
            cache["error"] = error
        raise error
    try:
        payload = json.loads(proc.stdout)
        envelopes = payload.get("envelopes", []) if isinstance(payload, dict) else payload
        if not isinstance(envelopes, list):
            error = ValueError("invalid inbox response")
            if cache is not None:
                cache["error"] = error
            raise error
    except (TypeError, json.JSONDecodeError) as exc:
        error = ValueError("email inbox returned invalid JSON")
        if cache is not None:
            cache["error"] = error
        raise error from exc
    if cache is not None:
        cache["envelopes"] = envelopes
    return envelopes


def _read_email(field: str) -> set[str]:
    sender, subject = _email_filter(field)
    try:
        envelopes = _inbox_envelopes()
        ids: set[str] = set()
        for entry in envelopes:
            if not isinstance(entry, dict) or "id" not in entry or not isinstance(entry.get("subject"), str):
                raise ValueError("inbox has incomplete message metadata")
            origin = entry.get("from")
            if isinstance(origin, dict):
                origin = origin.get("addr", origin.get("address", ""))
            elif isinstance(origin, list) and origin:
                origin = origin[0].get("addr", "") if isinstance(origin[0], dict) else origin[0]
            if not isinstance(origin, str):
                raise ValueError("inbox has incomplete sender metadata")
            from email.utils import parseaddr
            address = parseaddr(origin)[1].casefold()
            if address == sender and _email_subject(entry["subject"]) == _email_subject(subject):
                identifier = str(entry["id"])
                if identifier in ids:
                    raise ValueError("inbox has duplicate message IDs")
                ids.add(identifier)
        return ids
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("email inbox returned invalid JSON") from exc


def _read(kind: str, source: str, field: str):
    if kind == "json_file":
        return _read_numeric(source, field)
    if kind == "email" and source == "inbox":
        return sorted(_read_email(field))
    raise ValueError("supported sources: json_file (numeric field), email (inbox with exact sender|subject filter)")


def _ensure_job(identity: str) -> tuple[str, bool]:
    script = str(Path(__file__).resolve())
    for job in scheduler.list_jobs():
        if job.script == script and job.deliver == identity and job.no_agent and job.enabled:
            return job.id, False
    job = scheduler.add_job(SCHEDULE, deliver=identity, script=script, no_agent=True,
                            timeout=60)
    return job.id, True


@_serialized
def register(identity: str, task: str, kind: str, source: str, field: str = "") -> str:
    _owner(identity)
    if not config.CRON:
        raise ValueError("follow-up scheduler is disabled")
    title = _task(identity, task)
    kind, source, field = (str(kind).strip(), str(source).strip(), str(field).strip())
    # Validate and baseline before scheduling: inaccessible sources never become watches.
    baseline = _read(kind, source, field)
    value = _load()
    watches = value["watches"]
    key = (title.casefold(), kind, source, field)
    for watch in watches:
        if (watch["task"].casefold(), watch["kind"], watch["source"], watch["field"]) == key:
            return f"Already monitoring {title}; no duplicate watch created."
    if len(watches) >= MAX_WATCHES:
        raise ValueError("too many watches (20); stop an old watch first")
    job_id, created = _ensure_job(identity)
    watches.append({"task": title, "kind": kind, "source": source, "field": field,
                    "baseline": baseline, "expires_at": time.time() + TTL_SECONDS})
    try:
        _save(value)
    except BaseException:
        if created:
            scheduler.remove_job(job_id)
        raise
    return f"Monitoring {title} every {SCHEDULE} for up to 30 days (job {job_id}); baseline set, owner DM only."


@_serialized
def list_watches(identity: str) -> str:
    _owner(identity)
    watches = _load()["watches"]
    if not watches:
        return "No active task follow-up watches."
    return "\n".join(f"- {item['task']} [{item['kind']}]" for item in watches)


def _remove_job(identity: str) -> None:
    for job in scheduler.list_jobs():
        if job.script == str(Path(__file__).resolve()) and job.deliver == identity:
            scheduler.remove_job(job.id)


@_serialized
def stop(identity: str, task: str) -> str:
    _owner(identity)
    value = _load()
    previous = {"watches": list(value["watches"])}
    before = len(value["watches"])
    value["watches"] = [w for w in value["watches"] if w["task"].casefold() != task.strip().casefold()]
    if len(value["watches"]) == before:
        return "No matching watch."
    _save(value)
    if not value["watches"]:
        try:
            _remove_job(identity)
        except BaseException:
            # The watcher must remain visible/retryable when scheduler removal fails.
            _save(previous)
            raise
    return f"Stopped monitoring {task}."


@_serialized
def poll(*, deliver: bool = False) -> str:
    """Check registered watches; commit each baseline only after delivery succeeds.

    In production deliver=True: sender failure retains the old baseline so the
    next tick retries. The script emits no stdout; delivery goes to the owner DM
    directly to avoid double sends from the scheduler's own delivery path.
    """
    value = _load()
    if not value["watches"]:
        # A previous tick may have persisted an empty registry before job removal
        # failed; retry cleanup rather than leaving an idle cron job forever.
        owner = str((config.GATEWAYS.get("telegram", {}) or {}).get("owner_identity", "") or "").strip()
        if owner:
            _remove_job(f"telegram:{owner}")
        return ""
    now = time.time()
    changed = False
    notices = []
    retained = []
    send_attempts = 0
    watches = value["watches"][:MAX_WATCHES]
    if deliver and len(watches) > MAX_SENDS_PER_TICK:
        # Rotate even after failed sends; an unavailable early watch cannot starve
        # later tasks while the per-tick network budget is exhausted.
        watches = watches[MAX_SENDS_PER_TICK:] + watches[:MAX_SENDS_PER_TICK]
        changed = True
    for watch in watches:
        if watch.get("expires_at", 0) <= now:
            changed = True
            continue
        identity = f"telegram:{(config.GATEWAYS.get('telegram', {}) or {}).get('owner_identity', '')}"
        if not any(item['task'] == watch['task'] and item['status'] != 'cancelled'
                   for item in tasks.load(identity)):
            changed = True
            continue  # A cancelled or removed task is no longer authorized to monitor.
        try:
            current = _read(watch["kind"], watch["source"], watch["field"])
        except (OSError, ValueError, TypeError):
            if deliver and not watch.get("degraded_alerted") and send_attempts < MAX_SENDS_PER_TICK:
                owner = str((config.GATEWAYS.get("telegram", {}) or {}).get("owner_identity", "") or "").strip()
                message = f"⚠️ Zeline follow-up: check unavailable for {watch['task']}. Monitoring is degraded; baseline unchanged."
                if owner:
                    send_attempts += 1
                    if scheduler._deliver_telegram(owner, message, timeout=SEND_TIMEOUT_SECONDS, attempts=1)[0]:
                        watch["degraded_alerted"] = True
                        changed = True
            retained.append(watch)  # A failed check must not advance baseline.
            continue
        if watch.pop("degraded_alerted", None):
            changed = True
        old = watch["baseline"]
        if watch["kind"] == "email":
            newest = set(current) - set(old)
            notice = f"📬 Follow-up: {watch['task']} — {len(newest)} new email(s) in inbox." if newest else ""
        elif current > old:
            notice = f"💰 Follow-up: {watch['task']} — {watch['field']} {old} → {current}."
        else:
            notice = ""
        if current != old:
            if notice and deliver:
                if send_attempts >= MAX_SENDS_PER_TICK:
                    retained.append(watch)  # Retry when the next tick has delivery capacity.
                    continue
                owner = str((config.GATEWAYS.get("telegram", {}) or {}).get("owner_identity", "") or "").strip()
                if not owner:
                    retained.append(watch)
                    continue
                send_attempts += 1
                if not scheduler._deliver_telegram(owner, notice, timeout=SEND_TIMEOUT_SECONDS, attempts=1)[0]:
                    retained.append(watch)
                    continue
            watch["baseline"] = current
            changed = True
        if notice:
            notices.append(notice)
        retained.append(watch)
    if len(retained) != len(value["watches"]):
        changed = True
    if changed:
        value["watches"] = retained
        _save(value)
        if not retained:
            owner = str((config.GATEWAYS.get("telegram", {}) or {}).get("owner_identity", "") or "").strip()
            if owner:
                _remove_job(f"telegram:{owner}")
    return "\n".join(notices)


if __name__ == "__main__":
    # Scheduler no_agent sends non-empty stdout; delivery is already handled
    # inside poll after success, so stdout remains silent even on a real event.
    poll(deliver=True)
