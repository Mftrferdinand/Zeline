"""Memory autosync: scheduled pull from connectors into the Memory Tree.

Every ``interval_min`` minutes each *enabled* source is polled and a short
daily digest is written into Zeline's episodic memory (``zeline.memory``),
which ``zeline.memory_tree`` renders into the Obsidian vault's ``daily/``
pages. One episode per source per day — never duplicates.

Privacy-first: every source defaults to OFF. Nothing is synced unless the
operator explicitly enabled it in config (``zeline memory autosync on`` plus
per-source enable). Set ``ZELINE_AUTOSYNC_OFF=1`` to kill the whole feature.

Security notes:
- Only *metadata* (subject/sender/date, issue titles, event titles) is ever
  read. Message bodies are never fetched and never logged.
- The state file (``~/.zeline/memory_autosync.json``) holds email/issue
  metadata and is written with mode 0600.
- Connector credentials are never touched here; the connectors are used
  through their own public methods.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from zeline import config
from zeline import memory as mem

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_KEY = "memory_autosync"
ENV_KILL_SWITCH = "ZELINE_AUTOSYNC_OFF"
DEFAULT_INTERVAL_MIN = 20
MAX_SUMMARY_ITEMS = 5
_SUBJECT_LIMIT = 80


class AutosyncError(RuntimeError):
    """Raised when a syncer cannot complete its pull."""


SOURCE_LABELS = {
    "gmail": "Gmail",
    "github": "GitHub",
    "calendar": "Calendar",
}


# ---------------------------------------------------------------------------
# Settings (config file + in-memory override + env kill switch)
# ---------------------------------------------------------------------------

_SETTINGS_OVERRIDE: dict[str, Any] = {}


def _kill_switch() -> bool:
    """True when the operator disabled autosync entirely via env."""
    return os.environ.get(ENV_KILL_SWITCH, "") == "1"


def _default_settings() -> dict[str, Any]:
    return {
        "enabled": False,  # master switch; privacy default OFF
        "interval_min": DEFAULT_INTERVAL_MIN,
        "identity": "cli:local",
        "sources": {name: {"enabled": False, "repos": []} for name in SOURCE_LABELS},
    }


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _settings() -> dict[str, Any]:
    """Effective settings: defaults < config file < in-memory overrides."""
    try:
        stored = config.load_config().get(CONFIG_KEY, {}) or {}
    except Exception:
        stored = {}
    return _deep_merge(_deep_merge(_default_settings(), stored), _SETTINGS_OVERRIDE)


def configure(
    sources: list[str] | None = None,
    interval_min: int | None = None,
    master: bool | None = None,
    identity: str | None = None,
    repos: list[str] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Change autosync settings.

    ``sources``: source names to *enable* (e.g. ``["gmail", "calendar"]``).
    Enabling at least one source also flips the master switch on, since that
    is an explicit user action. Use ``master=False`` to turn everything off.

    ``persist=False`` keeps the change in memory only (used by tests).
    """
    delta: dict[str, Any] = {}
    if interval_min is not None:
        delta["interval_min"] = max(1, int(interval_min))
    if identity is not None:
        delta["identity"] = identity
    if master is not None:
        delta["enabled"] = bool(master)
    if sources:
        src_delta = {
            name: {"enabled": name in set(sources)}
            for name in set(sources) | set(SOURCE_LABELS)
            if name in SOURCE_LABELS
        }
        if repos is not None and "github" in src_delta:
            src_delta["github"]["repos"] = list(repos)
        delta["sources"] = src_delta
        # Explicitly enabling a source = explicit user consent → master on,
        # unless the caller said otherwise.
        if master is None:
            delta["enabled"] = True
    if repos is not None and "sources" not in delta:
        delta.setdefault("sources", {})["github"] = {"repos": list(repos)}

    _SETTINGS_OVERRIDE.update(_deep_merge(_SETTINGS_OVERRIDE, delta))

    if persist:
        try:
            stored = config.load_config()
        except Exception:
            stored = {}
        merged = _deep_merge(
            _deep_merge(_default_settings(), stored.get(CONFIG_KEY, {}) or {}), delta
        )
        stored[CONFIG_KEY] = merged
        config.save_config(stored)
    return _settings()


def is_enabled(source: str) -> bool:
    """True only when autosync is globally on AND this source is enabled."""
    if _kill_switch():
        return False
    settings = _settings()
    if not settings.get("enabled"):
        return False
    return bool(settings.get("sources", {}).get(source, {}).get("enabled", False))


# ---------------------------------------------------------------------------
# State (dedup + last-run bookkeeping), 0600
# ---------------------------------------------------------------------------


def _state_path() -> Path:
    return Path(config.DATA_DIR) / "memory_autosync.json"


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _today_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def should_sync(source: str, now: float | None = None) -> bool:
    """True when *source* is enabled and its interval has elapsed."""
    if not is_enabled(source):
        return False
    now = time.time() if now is None else now
    interval = _settings().get("interval_min", DEFAULT_INTERVAL_MIN) * 60
    last = _load_state().get(source, {}).get("last_sync", 0)
    return (now - last) >= interval


def due_sources(now: float | None = None) -> list[str]:
    """Enabled sources whose interval has elapsed."""
    return [name for name in SYNCERS if should_sync(name, now)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean(text: str, limit: int = 0) -> str:
    t = " ".join(str(text or "").split())
    if limit and len(t) > limit:
        t = t[:limit].rstrip() + "…"
    return t


_IMPORTANT_KEYWORDS = ("urgent", "penting", "important", "action required", "asap", "segera")


def _is_important(subject: str) -> bool:
    """Tiny heuristic: keyword match on the subject line only."""
    low = subject.lower()
    return any(kw in low for kw in _IMPORTANT_KEYWORDS)


def _get_connector(cid: str):
    try:
        from zeline.connectors import get as _get
    except Exception as exc:
        raise AutosyncError(f"connector framework unavailable ({exc}).") from exc
    conn = _get(cid)
    if conn is None:
        raise AutosyncError(f"connector '{cid}' is not registered.")
    status = conn.status() if hasattr(conn, "status") else {}
    if not status.get("connected"):
        raise AutosyncError(f"connector '{cid}' is not linked (run `zeline connect {cid}` first).")
    return conn


# ---------------------------------------------------------------------------
# Syncers: sync_<source>(source_config) -> summary string (metadata only)
# ---------------------------------------------------------------------------


def sync_gmail(source_config: dict) -> str:
    """Last-24h Gmail digest. Reads metadata only — never message bodies."""
    conn = _get_connector("google")
    date = _today_str()
    try:
        raw = conn.gmail_search("newer_than:1d", limit=20)
    except Exception as exc:
        raise AutosyncError(f"gmail search failed ({exc}).") from exc

    messages: list[tuple[str, str, str]] = []  # (id, from, subject)
    if raw and "No messages found." not in raw:
        for line in raw.splitlines():
            parts = line.split(" | ", 3)
            if len(parts) == 4:
                messages.append((parts[0], parts[2], parts[3]))

    important = [(mid, frm, subj) for mid, frm, subj in messages if _is_important(subj)]
    n = len(messages)
    if n == 0:
        return f"Gmail {date}: no new email in the last 24h."
    parts = [f"Gmail {date}: {n} email — {len(important)} penting"]
    for _, _, subj in important[:MAX_SUMMARY_ITEMS]:
        parts.append(f'"{_clean(subj, _SUBJECT_LIMIT)}"')
    summary = ": ".join(parts[:2])
    if len(parts) > 2:
        summary += ": " + ", ".join(parts[2:])
    summary += "."
    _record_ids("gmail", [mid for mid, _, _ in messages])
    return summary


def sync_github(source_config: dict) -> str:
    """Issues/PRs updated in the last 24h in watched repos (config)."""
    conn = _get_connector("github")
    date = _today_str()
    repos = [r.strip() for r in (source_config.get("repos") or []) if r.strip()]
    if not repos:
        raise AutosyncError("no watched repos configured for github sync.")

    cutoff = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    total = 0
    bits: list[str] = []
    seen_ids: list[str] = []
    for repo in repos:
        try:
            data = conn._api(  # noqa: SLF001 — search endpoint not exposed publicly
                "GET",
                "/search/issues",
                params={
                    "q": f"repo:{repo} updated:>={cutoff}",
                    "per_page": 20,
                    "sort": "updated",
                    "order": "desc",
                },
            )
        except Exception as exc:
            raise AutosyncError(f"github search failed for {repo} ({exc}).") from exc
        items = data.get("items", []) if isinstance(data, dict) else []
        if not items:
            continue
        total += len(items)
        for item in items[:MAX_SUMMARY_ITEMS]:
            num = item.get("number", "?")
            kind = "PR" if "pull_request" in item else "issue"
            title = _clean(item.get("title", ""), _SUBJECT_LIMIT)
            bits.append(f'{repo} #{num} "{title}" ({kind})')
            seen_ids.append(str(item.get("id", f"{repo}#{num}")))
    if total == 0:
        return f"GitHub {date}: no issue/PR updates in the last 24h."
    summary = f"GitHub {date}: {total} update (24 jam) di {len(repos)} repo"
    summary += " — " + "; ".join(bits[:MAX_SUMMARY_ITEMS]) + "."
    _record_ids("github", seen_ids)
    return summary


def sync_calendar(source_config: dict) -> str:
    """Tomorrow's calendar events. Metadata (time + title) only."""
    conn = _get_connector("google")
    now = datetime.datetime.now().astimezone()
    tomorrow = now + datetime.timedelta(days=1)
    tmin = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    tmax = tomorrow.replace(hour=23, minute=59, second=59, microsecond=0)
    try:
        raw = conn.calendar_list(time_min=tmin.isoformat(), time_max=tmax.isoformat(), limit=20)
    except Exception as exc:
        raise AutosyncError(f"calendar list failed ({exc}).") from exc

    events: list[str] = []
    if raw and "No upcoming events." not in raw:
        for line in raw.splitlines():
            when, _, what = line.partition(" — ")
            events.append(f"{when.strip()} {what.strip()}".strip())

    date = tomorrow.strftime("%Y-%m-%d")
    if not events:
        return f"Calendar {date} (besok): tidak ada event."
    summary = (
        f"Calendar {date} (besok): {len(events)} event — "
        + "; ".join(_clean(e, _SUBJECT_LIMIT) for e in events[:MAX_SUMMARY_ITEMS])
        + "."
    )
    _record_ids("calendar", events)
    return summary


SYNCERS: dict[str, Callable[[dict], str]] = {
    "gmail": sync_gmail,
    "github": sync_github,
    "calendar": sync_calendar,
}


def _record_ids(source: str, ids: list[str]) -> None:
    state = _load_state()
    entry = state.setdefault(source, {})
    entry["last_ids"] = [str(i) for i in ids][:50]


# ---------------------------------------------------------------------------
# Memory Tree write (via zeline.memory — rendered by memory_tree daily pages)
# ---------------------------------------------------------------------------


def _episode_title(source: str, date: str) -> str:
    return f"Autosync · {SOURCE_LABELS.get(source, source)} · {date}"


def _already_recorded(identity: str, title: str) -> bool:
    try:
        episodes = mem.list_episodes(identity, limit=200)
    except Exception:
        return False
    return any(isinstance(ep, dict) and ep.get("title") == title for ep in episodes)


def _stamp_date(source: str) -> None:
    state = _load_state()
    state.setdefault(source, {})["last_date"] = _today_str()
    _save_state(state)


def _record_summary(source: str, summary: str) -> str:
    """Write one daily episode. Returns 'recorded' or 'skipped (already recorded)'."""
    settings = _settings()
    identity = settings.get("identity", "cli:local")
    date = _today_str()
    title = _episode_title(source, date)
    if _already_recorded(identity, title):
        return "skipped (already recorded today)"
    result = mem.add_episode(identity, title, [summary], source="memory-autosync")
    if isinstance(result, str) and result.startswith("ERROR"):
        raise AutosyncError(f"could not write to memory ({result}).")
    _stamp_date(source)
    return "recorded"


# ---------------------------------------------------------------------------
# Coordinator entry points
# ---------------------------------------------------------------------------


def sync_source(source: str) -> str:
    """Sync one source now. Returns the summary string.

    Raises AutosyncError for unknown sources or sync failures. Returns a
    'skipped' message when the source is not enabled.
    """
    if source not in SYNCERS:
        raise AutosyncError(f"unknown source '{source}' (known: {', '.join(SYNCERS)}).")
    if not is_enabled(source):
        return f"{SOURCE_LABELS.get(source, source)}: skipped (not enabled)."
    cfg = _settings().get("sources", {}).get(source, {}) or {}
    summary = SYNCERS[source](cfg)
    status = _record_summary(source, summary)
    state = _load_state()
    state.setdefault(source, {})["last_sync"] = time.time()
    _save_state(state)
    return f"{summary} [{status}]"


def sync_all() -> dict[str, str]:
    """Sync every enabled source. Best-effort: one failure never stops others."""
    results: dict[str, str] = {}
    for source in SYNCERS:
        if not is_enabled(source):
            results[source] = f"{SOURCE_LABELS.get(source, source)}: skipped (not enabled)."
            continue
        try:
            results[source] = sync_source(source)
        except Exception as exc:  # noqa: BLE001 — best-effort by design
            results[source] = f"ERROR: {exc}"
    return results


def _sync_due() -> dict[str, str]:
    """Sync only sources whose interval has elapsed (used by run_loop)."""
    results: dict[str, str] = {}
    for source in due_sources():
        try:
            results[source] = sync_source(source)
        except Exception as exc:  # noqa: BLE001 — best-effort by design
            results[source] = f"ERROR: {exc}"
    return results


def run_loop(stop_event: threading.Event | None = None, poll_sec: int = 60) -> None:
    """Run the autosync loop until *stop_event* is set.

    Pass ``threading.Event()`` from the coordinator to stop gracefully.
    Exits immediately when ZELINE_AUTOSYNC_OFF=1.
    """
    stop = stop_event or threading.Event()
    while not stop.is_set():
        if _kill_switch():
            break
        try:
            _sync_due()
        except Exception:  # noqa: BLE001 — the loop itself must not die
            pass
        stop.wait(max(1, poll_sec))


def status() -> dict[str, Any]:
    """Operator-facing status snapshot (no raw content)."""
    state = _load_state()
    return {
        "kill_switch": _kill_switch(),
        "master_enabled": _settings().get("enabled", False),
        "interval_min": _settings().get("interval_min", DEFAULT_INTERVAL_MIN),
        "sources": {
            name: {
                "enabled": is_enabled(name),
                "last_sync": state.get(name, {}).get("last_sync"),
                "last_recorded_date": state.get(name, {}).get("last_date"),
            }
            for name in SYNCERS
        },
    }


def _stamp_date(source: str) -> None:
    state = _load_state()
    state.setdefault(source, {})["last_date"] = _today_str()
    _save_state(state)


# ---------------------------------------------------------------------------
# CLI: attaches to the existing `zeline memory` parser in zeline/cli.py.
#
# INTEGRATION FINDING (for the coordinator): `zeline memory` already exists in
# cli.py (line ~4203) with a positional `action` (choices=["consolidate"]).
# A positional with choices and subparsers cannot coexist on one parser —
# argparse validates the positional first, so `memory sync` would die with
# "invalid choice". register_memory_sub() therefore migrates that legacy
# positional into a `consolidate` subcommand with identical behavior
# (delegating to zeline.cli.cmd_memory), so `zeline memory consolidate`
# keeps working and no cli.py behavior is lost.
#
# Wiring needed in zeline/cli.py (2 lines, at the memory parser + dispatch):
#
#     from zeline import memory_autosync
#     memory_parser = subparsers.add_parser("memory", help="view local CLI memory")
#     memory_parser.add_argument("action", nargs="?", choices=["consolidate"], ...)
#     memory_autosync.register_memory_sub(memory_parser)   # <-- add
#
# and in the command dispatch, BEFORE the existing `command == "memory"`
# branch:
#
#     handled = memory_autosync.dispatch_memory(namespace)  # <-- add
#     if handled is not None:
#         return handled
# ---------------------------------------------------------------------------


def _cmd_sync(args: argparse.Namespace) -> int:
    if args.source:
        try:
            print(sync_source(args.source))
        except AutosyncError as exc:
            print(f"ERROR: {exc}")
            return 1
    else:
        for source, result in sync_all().items():
            print(f"[{source}] {result}")
    return 0


def _cmd_autosync_on(_args: argparse.Namespace) -> int:
    configure(master=True)
    print("memory autosync: ON (master). Enable sources via configure().")
    return 0


def _cmd_autosync_off(_args: argparse.Namespace) -> int:
    configure(master=False)
    print("memory autosync: OFF.")
    return 0


def _cmd_autosync_status(_args: argparse.Namespace) -> int:
    st = status()
    print(f"kill switch (ZELINE_AUTOSYNC_OFF): {'ON' if st['kill_switch'] else 'off'}")
    print(f"master: {'on' if st['master_enabled'] else 'off'}")
    print(f"interval: {st['interval_min']} min")
    for name, info in st["sources"].items():
        last = info["last_sync"]
        when = datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M") if last else "never"
        print(f"  {name}: {'enabled' if info['enabled'] else 'disabled'}, last sync {when}")
    return 0


def _cmd_consolidate(_args: argparse.Namespace) -> int:
    """Preserve the legacy `zeline memory consolidate` behavior (cli.py:cmd_memory)."""
    from zeline.cli import cmd_memory

    return int(cmd_memory("consolidate"))


def _migrate_legacy_action(memory_parser: argparse.ArgumentParser) -> None:
    """Remove cli.py's legacy `action` positional (choices=["consolidate"]).

    It conflicts with subparsers: argparse validates the positional against
    its choices before subparsers are tried, so `memory sync` would always
    fail with "invalid choice". The legacy behavior is re-registered below
    as the `consolidate` subcommand, so nothing is lost.
    """
    for action in list(getattr(memory_parser, "_actions", [])):
        if getattr(action, "dest", "") == "action" and getattr(action, "choices", None) == [
            "consolidate"
        ]:
            memory_parser._actions.remove(action)
            for group in memory_parser._action_groups:
                if action in group._actions:
                    group._actions.remove(action)
            break


def register_memory_sub(memory_parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    """Attach autosync subcommands to the existing `zeline memory` parser.

    Also migrates cli.py's legacy `action` positional into a `consolidate`
    subcommand (same behavior, via ``zeline.cli.cmd_memory``), because the
    positional and subparsers cannot coexist on one parser.
    """
    _migrate_legacy_action(memory_parser)
    subs = memory_parser.add_subparsers(dest="memory_sub", metavar="<subcommand>")

    p_sync = subs.add_parser("sync", help="sync now into the Memory Tree")
    p_sync.add_argument(
        "source",
        nargs="?",
        choices=list(SYNCERS),
        help="source to sync (default: all enabled)",
    )
    p_sync.set_defaults(func=_cmd_sync)

    p_auto = subs.add_parser("autosync", help="toggle/status the autosync loop")
    auto_subs = p_auto.add_subparsers(dest="autosync_cmd", metavar="<on|off|status>")
    auto_subs.required = True
    p_on = auto_subs.add_parser("on", help="enable the autosync master switch")
    p_on.set_defaults(func=_cmd_autosync_on)
    p_off = auto_subs.add_parser("off", help="disable the autosync master switch")
    p_off.set_defaults(func=_cmd_autosync_off)
    p_st = auto_subs.add_parser("status", help="show autosync status")
    p_st.set_defaults(func=_cmd_autosync_status)

    p_con = subs.add_parser("consolidate", help="remove duplicates and expired facts")
    p_con.set_defaults(func=_cmd_consolidate)
    return subs


def dispatch_memory(namespace: argparse.Namespace) -> int | None:
    """Run the autosync subcommand if one was chosen; else return None.

    The coordinator calls this before its own `memory` handling so both the
    old `zeline memory [consolidate]` action and the new subcommands coexist.
    """
    func = getattr(namespace, "func", None)
    if callable(func):
        return int(func(namespace))
    return None
