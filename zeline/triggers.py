"""Event triggers: connector events fire notifications, workflows, or memory notes.

A trigger watches one connector event source (Gmail new mail, GitHub new
issues/PRs) on a poll interval. When new matching items appear, the trigger's
action runs:

- ``notify``        -> :func:`notify_owner` hook (default: append to
  ``~/.zeline/trigger_outbox.jsonl``; the coordinator wires Telegram delivery)
- ``run_workflow``  -> :func:`run_workflow` hook (default: log to the outbox;
  the coordinator wires ``zeline.workflows``)
- ``append_memory`` -> append a note to ``~/.zeline/memory/inbox/<date>.md``

This module never imports a gateway (no circular imports); delivery hooks are
plain module-level functions the coordinator can rebind, e.g.::

    import zeline.triggers as triggers
    triggers.notify_owner = my_telegram_sender

First poll of a brand-new trigger only establishes a baseline (marks current
items as seen) and fires nothing, so adding a trigger never spams the owner
with old items.

Security notes
--------------
- Filter values never reach a connector query string: Gmail results are
  fetched with a plain ``newer_than:Nd`` query and ``from``/``subject``
  matching happens client-side in Python. GitHub ``owner``/``repo`` path
  segments are validated against a strict regex before URL interpolation.
- ``triggers.json`` may contain e-mail addresses and is stored ``0600``.
- Notification text templates are rendered from item data that is first
  stripped of control characters/whitespace and truncated, so a hostile
  subject line cannot inject terminal control sequences or megabyte blobs.
- ``interval_min`` is clamped to [1, 1440] to bound API polling pressure.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
import string
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


class TriggerError(RuntimeError):
    """Raised for any trigger configuration, storage, or polling error."""


# --------------------------------------------------------------------------
# Constants & validation
# --------------------------------------------------------------------------

#: trigger ``connector`` -> connector registry id (Gmail lives in the google connector)
CONNECTOR_REGISTRY_IDS = {"gmail": "google", "github": "github"}

#: allowed event types per trigger connector
CONNECTOR_EVENTS = {
    "gmail": {"new_email"},
    "github": {"new_issue", "new_pr"},
}

#: allowed actions
ACTIONS = {"notify", "run_workflow", "append_memory"}

#: allowed filter keys per (connector, event_type)
FILTER_KEYS = {
    ("gmail", "new_email"): {"from_contains", "subject_contains"},
    ("github", "new_issue"): {"repo", "labels"},
    ("github", "new_pr"): {"repo", "labels"},
}

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
GMAIL_ID_RE = re.compile(r"^[0-9a-fA-F]+$")

INTERVAL_MIN, INTERVAL_MAX = 1, 1440
MAX_POLL_ITEMS = 20          # max items fetched per poll per trigger
MAX_FAIL_ATTEMPTS = 3        # action attempts before an item is skipped
SEEN_IDS_CAP = 200           # bound on stored seen ids per trigger
CLEAN_LEN = 300              # max length of one sanitized template value
FILTER_STR_LEN = 200         # max length of a filter string value

TRIGGERS_FILE = "triggers.json"
STATE_FILE = "trigger_state.json"
OUTBOX_FILE = "trigger_outbox.jsonl"


def _data_dir() -> Path:
    """Resolve the zeline data dir at call time (honours $ZELINE_HOME)."""
    home = os.environ.get("ZELINE_HOME")
    if home:
        return Path(home).expanduser()
    from zeline.config import DATA_DIR  # lazy: avoids import cycles

    return Path(DATA_DIR)


def _triggers_path() -> Path:
    return _data_dir() / TRIGGERS_FILE


def _state_path() -> Path:
    return _data_dir() / STATE_FILE


def _outbox_path() -> Path:
    return _data_dir() / OUTBOX_FILE


def _ensure_private(path: Path) -> None:
    """Force 0600 on a file that may hold semi-sensitive data."""
    try:
        if path.exists() and (path.stat().st_mode & 0o777) != 0o600:
            os.chmod(path, 0o600)
    except OSError as exc:  # pragma: no cover - best effort hardening
        log.warning("could not chmod %s: %s", path, exc)


def _atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON atomically (temp file + os.replace) with 0600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TriggerError(f"could not read {path.name}: {exc}") from exc


# --------------------------------------------------------------------------
# Trigger model
# --------------------------------------------------------------------------

@dataclass
class Trigger:
    id: str
    connector: str
    event_type: str
    filter: dict[str, Any] = field(default_factory=dict)
    action: str = "notify"
    action_params: dict[str, Any] = field(default_factory=dict)
    interval_min: int = 5
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trigger":
        return cls(
            id=str(data.get("id", "")),
            connector=str(data.get("connector", "")),
            event_type=str(data.get("event_type", "")),
            filter=dict(data.get("filter") or {}),
            action=str(data.get("action", "notify")),
            action_params=dict(data.get("action_params") or {}),
            interval_min=int(data.get("interval_min", 5)),
            enabled=bool(data.get("enabled", True)),
        )


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _validate_id(value: str, what: str = "trigger id") -> str:
    value = (value or "").strip()
    if not ID_RE.match(value):
        raise TriggerError(
            f"invalid {what} {value!r}: must match ^[a-zA-Z0-9_-]+$ (max 64 chars)"
        )
    return value


def _validate_filter(connector: str, event_type: str, filt: Any) -> dict[str, Any]:
    if not isinstance(filt, dict):
        raise TriggerError("filter must be a JSON object")
    allowed = FILTER_KEYS[(connector, event_type)]
    unknown = set(filt) - allowed
    if unknown:
        raise TriggerError(
            f"unknown filter key(s) {sorted(unknown)} for {connector}/{event_type}; "
            f"allowed: {sorted(allowed)}"
        )
    out: dict[str, Any] = {}
    if connector == "gmail":
        for key in ("from_contains", "subject_contains"):
            if key in filt:
                val = filt[key]
                if not isinstance(val, str) or not val.strip():
                    raise TriggerError(f"filter {key!r} must be a non-empty string")
                if len(val) > FILTER_STR_LEN:
                    raise TriggerError(f"filter {key!r} too long (max {FILTER_STR_LEN})")
                out[key] = val.strip()
    else:  # github
        repo = filt.get("repo", "")
        if not isinstance(repo, str) or not REPO_RE.match(repo.strip()):
            raise TriggerError(
                "filter 'repo' is required and must look like 'owner/name' "
                "(letters, digits, . _ - only)"
            )
        out["repo"] = repo.strip()
        if "labels" in filt:
            labels = filt["labels"]
            if not isinstance(labels, list) or not all(
                isinstance(x, str) and x.strip() for x in labels
            ):
                raise TriggerError("filter 'labels' must be a list of non-empty strings")
            out["labels"] = [x.strip() for x in labels]
    return out


def _validate_action_params(action: str, params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise TriggerError("action_params must be a JSON object")
    out = dict(params)
    if action == "notify":
        template = params.get("text_template", "")
        if not isinstance(template, str) or not template.strip():
            raise TriggerError("notify action needs 'text_template' (non-empty string)")
        if len(template) > 2000:
            raise TriggerError("text_template too long (max 2000 chars)")
        # Render once against a dummy item so template syntax errors surface now,
        # not at 3am when the trigger fires.
        _render_template(template, _sample_item())
        out["text_template"] = template
    elif action == "run_workflow":
        wid = params.get("workflow_id", "")
        _validate_id(wid, "workflow id")
        out["workflow_id"] = wid
    elif action == "append_memory":
        prefix = params.get("note_prefix", "")
        if not isinstance(prefix, str):
            raise TriggerError("append_memory 'note_prefix' must be a string")
        out["note_prefix"] = _clean(prefix, 120)
    return out


def validate_trigger_fields(
    connector: str,
    event_type: str,
    filt: Any,
    action: str,
    action_params: Any,
    interval_min: Any = 5,
) -> tuple[str, str, dict[str, Any], str, dict[str, Any], int]:
    """Validate raw user input; return normalised values. Raises TriggerError."""
    connector = (connector or "").strip().lower()
    if connector not in CONNECTOR_EVENTS:
        raise TriggerError(
            f"unknown connector {connector!r}; allowed: {sorted(CONNECTOR_EVENTS)}"
        )
    event_type = (event_type or "").strip()
    if event_type not in CONNECTOR_EVENTS[connector]:
        raise TriggerError(
            f"unknown event_type {event_type!r} for {connector}; "
            f"allowed: {sorted(CONNECTOR_EVENTS[connector])}"
        )
    action = (action or "").strip()
    if action not in ACTIONS:
        raise TriggerError(f"unknown action {action!r}; allowed: {sorted(ACTIONS)}")
    try:
        interval = int(interval_min)
    except (TypeError, ValueError) as exc:
        raise TriggerError("interval_min must be an integer") from exc
    interval = max(INTERVAL_MIN, min(INTERVAL_MAX, interval))  # clamp: no API spam
    return (
        connector,
        event_type,
        _validate_filter(connector, event_type, filt),
        action,
        _validate_action_params(action, action_params),
        interval,
    )


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def _load_triggers() -> list[Trigger]:
    path = _triggers_path()
    _ensure_private(path)
    raw = _load_json(path, {"version": 1, "triggers": []})
    if not isinstance(raw, dict) or not isinstance(raw.get("triggers"), list):
        raise TriggerError(f"{TRIGGERS_FILE} is corrupt (expected object with 'triggers')")
    triggers = []
    for entry in raw["triggers"]:
        if not isinstance(entry, dict):
            raise TriggerError(f"{TRIGGERS_FILE} is corrupt (bad trigger entry)")
        triggers.append(Trigger.from_dict(entry))
    return triggers


def _save_triggers(triggers: list[Trigger]) -> None:
    _atomic_write_json(
        _triggers_path(), {"version": 1, "triggers": [t.to_dict() for t in triggers]}
    )


def _load_state() -> dict[str, Any]:
    path = _state_path()
    _ensure_private(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # A corrupt state file must not brick the daemon: start fresh. The
        # first poll after a reset only establishes a baseline (fires nothing),
        # so this cannot cause duplicate notifications.
        log.warning("trigger state corrupt (%s); starting fresh", exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    _atomic_write_json(_state_path(), state)


def add_trigger(
    connector: str,
    event_type: str,
    filt: Any,
    action: str,
    action_params: Any,
    interval_min: Any = 5,
    enabled: bool = True,
    trigger_id: str | None = None,
) -> Trigger:
    """Create, validate, and persist a trigger. Returns the stored Trigger."""
    connector, event_type, filt, action, action_params, interval = validate_trigger_fields(
        connector, event_type, filt, action, action_params, interval_min
    )
    tid = (
        _validate_id(trigger_id, "trigger id")
        if trigger_id is not None
        else f"trg_{secrets.token_hex(4)}"
    )
    triggers = _load_triggers()
    if any(t.id == tid for t in triggers):
        raise TriggerError(f"trigger id {tid!r} already exists")
    trigger = Trigger(
        id=tid,
        connector=connector,
        event_type=event_type,
        filter=filt,
        action=action,
        action_params=action_params,
        interval_min=interval,
        enabled=bool(enabled),
    )
    triggers.append(trigger)
    _save_triggers(triggers)
    log.info("trigger added: %s (%s/%s -> %s)", tid, connector, event_type, action)
    return trigger


def list_triggers() -> list[Trigger]:
    return _load_triggers()


def get_trigger(trigger_id: str) -> Trigger:
    tid = _validate_id(trigger_id)
    for trigger in _load_triggers():
        if trigger.id == tid:
            return trigger
    raise TriggerError(f"no such trigger {tid!r}")


def remove_trigger(trigger_id: str) -> bool:
    """Remove a trigger and its state. Returns True if it existed."""
    tid = _validate_id(trigger_id)
    triggers = _load_triggers()
    kept = [t for t in triggers if t.id != tid]
    if len(kept) == len(triggers):
        return False
    _save_triggers(kept)
    state = _load_state()
    state.pop(tid, None)
    _save_state(state)
    log.info("trigger removed: %s", tid)
    return True


# --------------------------------------------------------------------------
# Sanitising + template rendering
# --------------------------------------------------------------------------

def _clean(value: Any, limit: int = CLEAN_LEN) -> str:
    """Collapse whitespace, strip, truncate: safe to embed in notifications."""
    s = "" if value is None else str(value)
    s = re.sub(r"[\s]+", " ", s).strip()
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


def _sample_item() -> dict[str, Any]:
    return {
        "id": "sample",
        "ts": 0.0,
        "kind": "email",
        "from": "alice@example.com",
        "subject": "hello",
        "title": "hello",
        "repo": "owner/name",
        "number": 1,
        "url": "https://example.com",
        "labels": "bug",
        "snippet": "sample snippet",
        "date": "2026-01-01",
    }


class _SafeMap(dict):
    def __missing__(self, key: str) -> str:  # unknown placeholder -> empty
        return ""


_PLACEHOLDER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_template(template: str) -> None:
    """Only plain ``{name}`` placeholders: no attribute/index access, no format specs."""
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise TriggerError(f"bad text_template ({exc})") from exc
    for _lit, field_name, _spec, _conv in parsed:
        if field_name is None:
            continue
        if not _PLACEHOLDER_RE.match(field_name):
            raise TriggerError(
                f"bad text_template: placeholder {{{field_name}}} is not a plain name"
            )


def _render_template(template: str, item: dict[str, Any]) -> str:
    """Render ``{placeholder}`` template from a sanitised item context."""
    _check_template(template)
    ctx = _SafeMap({k: _clean(v) for k, v in _template_context(item).items()})
    try:
        return string.Formatter().vformat(template, (), ctx)
    except Exception as exc:
        raise TriggerError(f"bad text_template ({exc})") from exc


def _template_context(item: dict[str, Any]) -> dict[str, Any]:
    labels = item.get("labels")
    if isinstance(labels, list):
        labels = ", ".join(str(x) for x in labels)
    sender = item.get("from") or ""
    return {
        "id": item.get("id", ""),
        "kind": item.get("kind", ""),
        "from": sender,
        "sender": sender,
        "subject": item.get("subject") or item.get("title", ""),
        "title": item.get("title") or item.get("subject", ""),
        "repo": item.get("repo", ""),
        "number": item.get("number", ""),
        "url": item.get("url", ""),
        "labels": labels or "",
        "snippet": item.get("snippet", ""),
        "date": item.get("date", ""),
    }


# --------------------------------------------------------------------------
# Delivery hooks (rebindable by the coordinator)
# --------------------------------------------------------------------------

def _outbox_write(entry: dict[str, Any]) -> None:
    path = _outbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": time.time(), **entry}, ensure_ascii=False)
    existed = path.exists()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    if not existed:
        os.chmod(path, 0o600)
    else:
        _ensure_private(path)


def notify_owner(text: str) -> None:
    """Default notify hook: record the notification in the trigger outbox.

    The coordinator rebinds this (``triggers.notify_owner = ...``) to deliver
    via Telegram. Never imports a gateway module (avoids circular imports).
    """
    text = _clean(text, 2000)
    _outbox_write({"kind": "notify", "text": text})
    log.info("notify queued (%d chars)", len(text))


def run_workflow(workflow_id: str, context: dict[str, Any]) -> None:
    """Default run_workflow hook: record the request in the trigger outbox.

    The coordinator rebinds this to actually execute via ``zeline.workflows``.
    """
    _validate_id(workflow_id, "workflow id")
    safe_ctx = {k: _clean(v) for k, v in (context or {}).items()}
    _outbox_write({"kind": "run_workflow", "workflow_id": workflow_id, "context": safe_ctx})
    log.info("workflow run queued: %s", workflow_id)


def _append_memory_note(prefix: str, trigger: Trigger, item: dict[str, Any]) -> str:
    """Append a note to the memory inbox (``~/.zeline/memory/inbox/<date>.md``)."""
    inbox = _data_dir() / "memory" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    path = inbox / f"{day}.md"
    title = _clean(item.get("title") or item.get("subject") or "(no title)", 200)
    url = _clean(item.get("url") or "", 300)
    head = f"{prefix}: " if prefix else ""
    line = f"- [{time.strftime('%H:%M')}] {head}{title}"
    if url:
        line += f" ({url})"
    line += f" [trigger {trigger.id}]\n"
    existed = path.exists()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    if not existed:
        os.chmod(path, 0o600)
    log.info("memory note appended for trigger %s", trigger.id)
    return line.strip()


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    """Sanitised item snapshot handed to workflow contexts."""
    return {k: _clean(v, 500) for k, v in item.items() if k != "extra"}


def _fire_action(trigger: Trigger, item: dict[str, Any]) -> None:
    action = trigger.action
    params = trigger.action_params
    if action == "notify":
        # NOTE: call via module globals so coordinator rebinding works.
        globals()["notify_owner"](_render_template(params["text_template"], item))
    elif action == "run_workflow":
        globals()["run_workflow"](
            params["workflow_id"],
            {"trigger_id": trigger.id, "event": _public_item(item)},
        )
    elif action == "append_memory":
        _append_memory_note(params.get("note_prefix", ""), trigger, item)
    else:  # pragma: no cover - validated at creation
        raise TriggerError(f"unknown action {action!r}")


# --------------------------------------------------------------------------
# Event sources
# --------------------------------------------------------------------------

#: test seam: maps trigger connector -> fake connector object
_CONNECTOR_OVERRIDES: dict[str, Any] = {}


def _get_connector(trigger_connector: str) -> Any:
    if trigger_connector in _CONNECTOR_OVERRIDES:
        return _CONNECTOR_OVERRIDES[trigger_connector]
    from zeline.connectors import get as _registry_get  # lazy: avoids import cycles

    try:
        registry_id = CONNECTOR_REGISTRY_IDS[trigger_connector]
    except KeyError:
        raise TriggerError(f"unknown connector {trigger_connector!r}") from None
    conn = _registry_get(registry_id)
    if conn is None:
        raise TriggerError(
            f"connector '{trigger_connector}' is not available "
            f"(registry id '{registry_id}' not registered)"
        )
    return conn


def _fetch_gmail_new_email(
    conn: Any, filt: dict[str, Any], state: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from zeline.connectors import google as _google_mod  # lazy; GMAIL_API constant

    last_ts = float((state or {}).get("last_ts") or 0)
    seen: set[str] = set((state or {}).get("seen_ids") or [])
    pending: set[str] = set(((state or {}).get("failed") or {}).keys())
    now = time.time()
    # Pull recent mail only; from/subject matching is done client-side below
    # so filter text can never alter the Gmail query (no query injection).
    days = max(1, math.ceil((now - last_ts) / 86400) + 1) if last_ts > 0 else 1
    data = conn._api(
        "GET",
        f"{_google_mod.GMAIL_API}/users/me/messages",
        params={"q": f"newer_than:{days}d", "maxResults": MAX_POLL_ITEMS},
    )
    fetched: list[dict[str, Any]] = []
    for entry in (data.get("messages") or [])[:MAX_POLL_ITEMS]:
        mid = str(entry.get("id") or "")
        if not mid or not GMAIL_ID_RE.match(mid) or mid in seen:
            continue  # hex-only: message ids can never break the URL path
        meta = conn._api(
            "GET",
            f"{_google_mod.GMAIL_API}/users/me/messages/{mid}",
            params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
        )
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in (meta.get("payload") or {}).get("headers", [])
        }
        ts = int(meta.get("internalDate") or 0) / 1000.0
        fetched.append(
            {
                "id": mid,
                "ts": ts,
                "kind": "email",
                "from": headers.get("from", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
                "snippet": meta.get("snippet", ""),
                "url": f"https://mail.google.com/mail/u/0/#inbox/{mid}",
            }
        )
    fetched.sort(key=lambda i: i["ts"])
    from_c = (filt.get("from_contains") or "").lower()
    subj_c = (filt.get("subject_contains") or "").lower()
    if not (state or {}).get("baselined"):
        # First successful poll: establish a baseline, fire nothing (no old-mail spam).
        items: list[dict[str, Any]] = []
    else:
        items = [
            i
            for i in fetched
            if i["id"] not in seen
            and (i["ts"] > last_ts or i["id"] in pending)
            and (not from_c or from_c in (i["from"] or "").lower())
            and (not subj_c or subj_c in (i["subject"] or "").lower())
        ]
    proposal = _proposal(state, fetched, items, now)
    return items, proposal


def _parse_github_ts(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _fetch_github(
    conn: Any,
    event_type: str,
    filt: dict[str, Any],
    state: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    owner, repo = filt["repo"].split("/")  # repo validated by REPO_RE at creation
    endpoint = "issues" if event_type == "new_issue" else "pulls"
    last_ts = float((state or {}).get("last_ts") or 0)
    seen: set[str] = set((state or {}).get("seen_ids") or [])
    pending: set[str] = set(((state or {}).get("failed") or {}).keys())
    now = time.time()
    params: dict[str, Any] = {
        "state": "all",
        "sort": "created",
        "direction": "desc",
        "per_page": MAX_POLL_ITEMS,
    }
    if last_ts > 0:
        params["since"] = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    raw = conn._api("GET", f"/repos/{owner}/{repo}/{endpoint}", params=params)
    if not isinstance(raw, list):
        raise TriggerError(f"github {endpoint} returned unexpected data")
    required_labels = set(filt.get("labels") or [])
    fetched: list[dict[str, Any]] = []
    for entry in raw[:MAX_POLL_ITEMS]:
        if not isinstance(entry, dict):
            continue
        if event_type == "new_issue" and "pull_request" in entry:
            continue  # the issues endpoint also lists PRs
        labels = [str(lb.get("name", "")) for lb in entry.get("labels", []) or []]
        if required_labels and not required_labels.issubset(set(labels)):
            continue
        fetched.append(
            {
                "id": str(entry.get("id")),
                "ts": _parse_github_ts(entry.get("created_at")),
                "kind": "issue" if event_type == "new_issue" else "pr",
                "title": entry.get("title", ""),
                "number": entry.get("number", ""),
                "repo": f"{owner}/{repo}",
                "labels": labels,
                "url": entry.get("html_url", ""),
                "snippet": (entry.get("body") or "")[:500],
            }
        )
    fetched.sort(key=lambda i: i["ts"])
    if not (state or {}).get("baselined"):
        items = []  # first successful poll: baseline only
    else:
        items = [
            i
            for i in fetched
            if i["id"] not in seen and (i["ts"] > last_ts or i["id"] in pending)
        ]
    proposal = _proposal(state, fetched, items, now)
    return items, proposal


def _proposal(
    state: dict[str, Any] | None,
    fetched: list[dict[str, Any]],
    items: list[dict[str, Any]],
    now: float,
) -> dict[str, Any]:
    """Build the state proposal: baseline + everything fetched marked seen."""
    prev_seen: set[str] = set((state or {}).get("seen_ids") or [])
    seen = set(prev_seen) | {i["id"] for i in fetched}
    ordered = sorted(seen)
    newest = max(fetched, key=lambda i: i["ts"], default=None)
    prev_ts = float((state or {}).get("last_ts") or 0)
    return {
        "last_id": newest["id"] if newest else (state or {}).get("last_id", ""),
        "last_ts": max(prev_ts, *(i["ts"] for i in fetched), 0.0),
        "checked_at": now,
        "baselined": True,
        # keep insertion order stable-ish, bounded
        "seen_ids": ordered[-SEEN_IDS_CAP:],
        "failed": dict((state or {}).get("failed") or {}),
    }


def fetch_new(
    connector: str,
    event_type: str,
    filt: dict[str, Any],
    since_state: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch new items for a trigger.

    Returns ``(items, new_state)`` where ``items`` are new candidates
    (oldest first, already filtered) and ``new_state`` is the proposed state
    assuming every action succeeds; :func:`poll_once` adjusts it for failures.
    The proposal carries ``baselined=True`` once a fetch succeeded; until
    then no items are returned (first poll only records a baseline).
    """
    conn = _get_connector(connector)
    # Re-validate here too: fetch_new is public and may be called with
    # filters that never went through add_trigger's validation.
    _validate_filter(connector, event_type, filt)
    try:
        if connector == "gmail":
            return _fetch_gmail_new_email(conn, filt, since_state)
        if connector == "github":
            return _fetch_github(conn, event_type, filt, since_state)
    except TriggerError:
        raise
    except Exception as exc:
        raise TriggerError(f"fetch failed for {connector}/{event_type}: {exc}") from exc
    raise TriggerError(f"unsupported connector {connector!r}")


# --------------------------------------------------------------------------
# Polling
# --------------------------------------------------------------------------

def _due(trigger: Trigger, state: dict[str, Any], now: float) -> bool:
    return now >= float(state.get("checked_at") or 0) + trigger.interval_min * 60


def _poll_trigger(
    trigger: Trigger, state: dict[str, Any] | None, now: float
) -> tuple[int, dict[str, Any]]:
    """Poll one trigger. Returns (fired_count, new_state)."""
    items, proposal = fetch_new(trigger.connector, trigger.event_type, trigger.filter, state)
    ok: set[str] = set()
    exhausted: set[str] = set()
    failed: dict[str, int] = dict(proposal.get("failed") or {})
    for item in items:
        iid = item["id"]
        try:
            _fire_action(trigger, item)
        except Exception as exc:
            attempts = failed.get(iid, 0) + 1
            if attempts >= MAX_FAIL_ATTEMPTS:
                log.warning(
                    "trigger %s: action for item %s failed %d times, skipping: %s",
                    trigger.id, iid, attempts, exc,
                )
                failed.pop(iid, None)
                exhausted.add(iid)
            else:
                failed[iid] = attempts
                log.warning(
                    "trigger %s: action for item %s failed (attempt %d/%d): %s",
                    trigger.id, iid, attempts, MAX_FAIL_ATTEMPTS, exc,
                )
            continue
        ok.add(iid)
        failed.pop(iid, None)
    # Adjust the proposal: items whose actions are still pending retry must NOT
    # be marked seen, and last_ts must not advance past them.
    retry = {i["id"] for i in items} - ok - exhausted
    seen = set(proposal.get("seen_ids") or []) - retry
    baseline_ts = float((state or {}).get("last_ts") or 0)
    advanced = [i["ts"] for i in items if i["id"] in ok or i["id"] in exhausted]
    new_state = {
        "last_id": proposal.get("last_id", ""),
        "last_ts": max([baseline_ts, *advanced, proposal.get("last_ts", 0.0)]),
        "checked_at": now,
        "baselined": True,
        "seen_ids": sorted(seen)[-SEEN_IDS_CAP:],
        "failed": failed,
    }
    return len(ok), new_state


def poll_once(trigger_id: str | None = None, force: bool = False) -> list[dict[str, Any]]:
    """Poll due triggers once.

    For each enabled trigger whose interval elapsed since ``checked_at``: fetch
    new items, run the action for each, update state. A failing trigger never
    breaks the others. Returns ``[{"trigger_id": ..., "fired": n, ...}]``.
    """
    now = time.time()
    targets = [get_trigger(trigger_id)] if trigger_id else list_triggers()
    states = _load_state()
    results: list[dict[str, Any]] = []
    for trigger in targets:
        if not trigger.enabled:
            continue
        state = states.get(trigger.id)
        if not force and not _due(trigger, state or {}, now):
            continue
        try:
            fired, new_state = _poll_trigger(trigger, state, now)
        except Exception as exc:
            # Never let one trigger's fetch failure wedge the loop: back off
            # until the next interval instead of hot-retrying.
            log.warning("trigger %s poll failed: %s", trigger.id, exc)
            states[trigger.id] = {**(state or {}), "checked_at": now}
            _save_state(states)
            results.append({"trigger_id": trigger.id, "fired": 0, "error": str(exc)})
            continue
        states[trigger.id] = new_state
        _save_state(states)
        results.append({"trigger_id": trigger.id, "fired": fired})
    return results


def run_loop(stop_event: Any = None, once: bool = False) -> list[dict[str, Any]] | None:
    """Daemon loop: poll every 60s (per-trigger intervals gate actual fetches).

    ``stop_event`` is an optional ``threading.Event``; ``once=True`` performs a
    single :func:`poll_once` and returns its result (handy for cron/systemd).
    """
    if once:
        return poll_once()
    log.info("trigger loop started")
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                poll_once()
            except Exception as exc:  # never kill the daemon on a storage bug
                log.warning("poll_once failed: %s", exc)
            if stop_event is not None:
                if stop_event.wait(60):
                    break
            else:
                time.sleep(60)
    except KeyboardInterrupt:
        pass
    log.info("trigger loop stopped")
    return None


# --------------------------------------------------------------------------
# Agent tools
# --------------------------------------------------------------------------

def get_tool_defs() -> list[Any]:
    """Tool definitions for the agent (lazy import: avoids import cycles)."""
    from zeline.tools import ToolDef, ToolRisk

    _p = frozenset({"safe", "workspace", "full"})
    return [
        ToolDef(
            name="trigger_add",
            description=(
                "Add an event trigger: watch a connector for new events "
                "(gmail/new_email, github/new_issue, github/new_pr) and fire an "
                "action (notify owner, run a workflow, append a memory note) "
                "when a matching item appears."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "connector": {"type": "string", "enum": ["gmail", "github"]},
                    "event_type": {
                        "type": "string",
                        "enum": ["new_email", "new_issue", "new_pr"],
                    },
                    "filter_json": {
                        "type": "string",
                        "description": (
                            'JSON filter, e.g. {"from_contains": "boss@corp.com"} for '
                            'gmail or {"repo": "owner/name", "labels": ["bug"]} for github'
                        ),
                    },
                    "action": {
                        "type": "string",
                        "enum": ["notify", "run_workflow", "append_memory"],
                    },
                    "action_params_json": {
                        "type": "string",
                        "description": (
                            'JSON params: notify -> {"text_template": "New mail: {subject}"}; '
                            'run_workflow -> {"workflow_id": "..."}; '
                            'append_memory -> {"note_prefix": "..."}'
                        ),
                    },
                    "interval_min": {
                        "type": "integer",
                        "description": "poll interval in minutes (1-1440, default 5)",
                    },
                    "trigger_id": {
                        "type": "string",
                        "description": "optional custom id (^[a-zA-Z0-9_-]+$)",
                    },
                },
                "required": [
                    "connector", "event_type", "filter_json",
                    "action", "action_params_json",
                ],
            },
            profiles=_p,
            risk=ToolRisk.WRITE,
        ),
        ToolDef(
            name="trigger_list",
            description="List all event triggers with their connector, event, action, and interval.",
            parameters={"type": "object", "properties": {}},
            profiles=_p,
            risk=ToolRisk.READ,
        ),
        ToolDef(
            name="trigger_remove",
            description="Remove an event trigger by id.",
            parameters={
                "type": "object",
                "properties": {
                    "trigger_id": {"type": "string", "description": "trigger id to remove"},
                },
                "required": ["trigger_id"],
            },
            profiles=_p,
            risk=ToolRisk.WRITE,
        ),
    ]


def _parse_json_arg(raw: str, what: str) -> dict[str, Any]:
    try:
        val = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise TriggerError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(val, dict):
        raise TriggerError(f"{what} must be a JSON object")
    return val


def _handle_trigger_add(
    connector: str,
    event_type: str,
    filter_json: str = "{}",
    action: str = "notify",
    action_params_json: str = "{}",
    interval_min: int = 5,
    trigger_id: str | None = None,
) -> str:
    try:
        trigger = add_trigger(
            connector,
            event_type,
            _parse_json_arg(filter_json, "filter_json"),
            action,
            _parse_json_arg(action_params_json, "action_params_json"),
            interval_min=interval_min,
            trigger_id=trigger_id,
        )
    except TriggerError as exc:
        return f"ERROR: {exc}"
    return (
        f"Trigger {trigger.id} added: {trigger.connector}/{trigger.event_type} "
        f"-> {trigger.action} every {trigger.interval_min} min."
    )


def _handle_trigger_list() -> str:
    triggers = list_triggers()
    if not triggers:
        return "No triggers defined."
    lines = []
    for t in triggers:
        state = "enabled" if t.enabled else "disabled"
        lines.append(
            f"- {t.id} [{state}] {t.connector}/{t.event_type} "
            f"filter={json.dumps(t.filter, ensure_ascii=False)} "
            f"-> {t.action} every {t.interval_min} min"
        )
    return "\n".join(lines)


def _handle_trigger_remove(trigger_id: str) -> str:
    try:
        removed = remove_trigger(trigger_id)
    except TriggerError as exc:
        return f"ERROR: {exc}"
    return f"Trigger {trigger_id} removed." if removed else f"No such trigger {trigger_id!r}."


TOOL_HANDLERS: dict[str, Callable[..., str]] = {
    "trigger_add": _handle_trigger_add,
    "trigger_list": _handle_trigger_list,
    "trigger_remove": _handle_trigger_remove,
}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cli_add(args: Any) -> int:
    try:
        trigger = add_trigger(
            args.connector,
            args.event,
            _parse_json_arg(args.filter, "--filter"),
            args.action,
            _parse_json_arg(args.action_params, "--action-params"),
            interval_min=args.interval,
            enabled=not args.disabled,
            trigger_id=args.id,
        )
    except TriggerError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Added trigger {trigger.id}")
    return 0


def _cli_list(args: Any) -> int:
    print(_handle_trigger_list())
    return 0


def _cli_remove(args: Any) -> int:
    try:
        removed = remove_trigger(args.trigger_id)
    except TriggerError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Removed trigger {args.trigger_id}." if removed else f"No such trigger {args.trigger_id!r}.")
    return 0 if removed else 1


def _cli_test(args: Any) -> int:
    """Dry-run: show what would fire, without firing actions or saving state."""
    try:
        trigger = get_trigger(args.trigger_id)
    except TriggerError as exc:
        print(f"ERROR: {exc}")
        return 1
    if args.commit:
        results = poll_once(args.trigger_id, force=True)
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    try:
        conn = _get_connector(trigger.connector)
        state = _load_state().get(trigger.id)
        items, _proposal = fetch_new(trigger.connector, trigger.event_type, trigger.filter, state)
    except TriggerError as exc:
        print(f"ERROR: {exc}")
        return 1
    if state is None:
        print("(no baseline yet — first real poll only records current items, fires nothing)")
    if not items:
        print("No new items would fire.")
        return 0
    print(f"{len(items)} item(s) would fire action '{trigger.action}':")
    for item in items:
        if trigger.action == "notify":
            preview = _render_template(trigger.action_params["text_template"], item)
        elif trigger.action == "run_workflow":
            preview = f"run workflow {trigger.action_params['workflow_id']}"
        else:
            preview = f"append memory note ({trigger.action_params.get('note_prefix', '')})"
        print(f"  - [{item['kind']}] {item['id']}: {preview[:160]}")
    return 0


def register_cli(subparsers: Any) -> None:
    """Register the ``zeline trigger`` command tree on an argparse subparsers."""
    parser = subparsers.add_parser(
        "trigger", help="event triggers: fire actions on connector events"
    )
    sub = parser.add_subparsers(dest="trigger_command", required=True)

    add_p = sub.add_parser("add", help="add an event trigger")
    add_p.add_argument("--connector", required=True, choices=sorted(CONNECTOR_EVENTS))
    add_p.add_argument(
        "--event",
        required=True,
        choices=["new_email", "new_issue", "new_pr"],
        help="event type (must match the connector)",
    )
    add_p.add_argument("--filter", default="{}", help="JSON filter object")
    add_p.add_argument("--action", required=True, choices=sorted(ACTIONS))
    add_p.add_argument("--action-params", default="{}", help="JSON action params")
    add_p.add_argument("--interval", type=int, default=5, help="poll minutes (1-1440)")
    add_p.add_argument("--id", default=None, help="custom trigger id")
    add_p.add_argument("--disabled", action="store_true", help="create disabled")
    add_p.set_defaults(func=_cli_add)

    list_p = sub.add_parser("list", help="list event triggers")
    list_p.set_defaults(func=_cli_list)

    rm_p = sub.add_parser("remove", help="remove an event trigger")
    rm_p.add_argument("trigger_id", help="trigger id")
    rm_p.set_defaults(func=_cli_remove)

    test_p = sub.add_parser("test", help="dry-run a trigger (shows what would fire)")
    test_p.add_argument("trigger_id", help="trigger id")
    test_p.add_argument(
        "--commit",
        action="store_true",
        help="actually fire actions and update state",
    )
    test_p.set_defaults(func=_cli_test)


__all__ = [
    "Trigger",
    "TriggerError",
    "add_trigger",
    "list_triggers",
    "get_trigger",
    "remove_trigger",
    "fetch_new",
    "poll_once",
    "run_loop",
    "notify_owner",
    "run_workflow",
    "get_tool_defs",
    "TOOL_HANDLERS",
    "register_cli",
    "CONNECTOR_EVENTS",
    "ACTIONS",
]
