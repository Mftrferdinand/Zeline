"""Event-driven hooks engine for Zeline.

Hooks are automatic triggers that fire when specific agent events occur,
inspired by event-driven automation systems. A hook never crashes the agent:
each hook runs isolated with a timeout, and failures are logged, not raised.

Event types:
- ``on_tool_call``   — a tool is about to run. data: {identity, name, args}
- ``on_tool_result`` — a tool finished. data: {identity, name, success, result_preview}
- ``on_turn_start``  — an agent turn begins. data: {identity, text}
- ``on_turn_end``    — an agent turn ends. data: {identity, tool_calls}
- ``on_error``       — an exception escaped a turn. data: {identity, error, where}
- ``on_skill_learned`` — a skill was saved. data: {name, path}

Two kinds of hooks:
1. In-process callbacks via :func:`register_hook` (used by agent integration
   and tests).
2. Persistent hooks in ``~/.zeline/hooks.json`` — either ``builtin`` (shipped
   with Zeline) or ``command`` (a shell command run with the event JSON on
   stdin and ``HOOK_EVENT``/``HOOK_NAME`` env vars). The JSON file is
   human-editable.

Built-in hooks:
- ``command-logger`` — logs every tool call to ``~/.zeline/hooks-log/tool-calls.jsonl``
- ``session-memory``  — saves an episodic memory entry when a turn used tools
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

ON_TOOL_CALL = "on_tool_call"
ON_TOOL_RESULT = "on_tool_result"
ON_TURN_START = "on_turn_start"
ON_TURN_END = "on_turn_end"
ON_ERROR = "on_error"
ON_SKILL_LEARNED = "on_skill_learned"

EVENTS = frozenset({
    ON_TOOL_CALL,
    ON_TOOL_RESULT,
    ON_TURN_START,
    ON_TURN_END,
    ON_ERROR,
    ON_SKILL_LEARNED,
})

#: Max seconds a single hook may run before it is abandoned.
HOOK_TIMEOUT = 5.0

# Key names whose VALUES are treated as secrets and redacted before any
# hook data is persisted to disk (H5). The event payload still carries the
# raw values in-process (hooks run as the local user, same trust level as
# the agent) — only the on-disk logs are scrubbed.
_SENSITIVE_KEY_RE = re.compile(
    r"api[_-]?key|token|secret|password|passwd|auth|credential|private[_-]?key",
    re.IGNORECASE,
)


def _redact_args(args: Any) -> Any:
    """Redact secret-looking values in a tool-args dict (H5)."""
    if isinstance(args, dict):
        return {
            k: ("[REDACTED]" if _SENSITIVE_KEY_RE.search(str(k)) else v)
            for k, v in args.items()
        }
    return args


def _append_jsonl(path: Path, entry: dict) -> None:
    """Append a JSONL log entry, keeping the file owner-only (H4).

    Hook logs can contain tool args / error text, so they get the same
    0600 treatment as hooks.json itself. Best-effort: never raises.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass

# ---------------------------------------------------------------------------
# In-memory registry
# ---------------------------------------------------------------------------

_registry: dict[str, list[tuple[str, Callable[[dict], Any]]]] = {e: [] for e in EVENTS}
_registry_lock = threading.Lock()

#: Recent hook failures (in-memory, newest last, capped).
_hook_errors: list[dict[str, Any]] = []
_HOOK_ERROR_CAP = 50


def register_hook(event: str, callback: Callable[[dict], Any], name: str) -> None:
    """Register an in-process hook callback for ``event``.

    Raises ValueError for unknown events. Re-registering the same ``name``
    for the same event replaces the old callback.
    """
    if event not in EVENTS:
        raise ValueError(f"Unknown hook event: {event!r}. Valid: {sorted(EVENTS)}")
    if not callable(callback):
        raise ValueError("Hook callback must be callable")
    name = str(name).strip()[:64] or "unnamed"
    with _registry_lock:
        hooks = _registry[event]
        _registry[event] = [(n, cb) for n, cb in hooks if n != name]
        _registry[event].append((name, callback))


def unregister_hook(event: str, name: str) -> bool:
    """Remove an in-process hook. Returns True if one was removed."""
    with _registry_lock:
        hooks = _registry.get(event, [])
        kept = [(n, cb) for n, cb in hooks if n != name]
        removed = len(kept) != len(hooks)
        _registry[event] = kept
        return removed


def list_registered(event: str | None = None) -> list[dict[str, Any]]:
    """List in-process registered hooks."""
    with _registry_lock:
        events = [event] if event else sorted(EVENTS)
        out = []
        for ev in events:
            for name, _cb in _registry.get(ev, []):
                out.append({"name": name, "event": ev, "kind": "callback"})
        return out


def _record_error(name: str, event: str, error: str) -> None:
    entry = {
        "ts": time.time(),
        "hook": name,
        "event": event,
        "error": str(error)[:300],
    }
    _hook_errors.append(entry)
    if len(_hook_errors) > _HOOK_ERROR_CAP:
        del _hook_errors[: len(_hook_errors) - _HOOK_ERROR_CAP]
    # Also persist to disk (best-effort, owner-only perms — H4)
    _append_jsonl(log_dir() / "hook-errors.jsonl", entry)


def hook_errors() -> list[dict[str, Any]]:
    """Recent hook failures (in-memory)."""
    return list(_hook_errors)


# ---------------------------------------------------------------------------
# Paths / persistent config
# ---------------------------------------------------------------------------

def _zeline_dir() -> Path:
    return Path(os.path.expanduser("~/.zeline"))


def config_path() -> Path:
    return _zeline_dir() / "hooks.json"


def log_dir() -> Path:
    return _zeline_dir() / "hooks-log"


_DEFAULT_CONFIG = {
    "enabled": True,
    "hooks": [
        {
            "name": "command-logger",
            "event": ON_TOOL_CALL,
            "enabled": True,
            "type": "builtin",
        },
        {
            "name": "session-memory",
            "event": ON_TURN_END,
            "enabled": True,
            "type": "builtin",
        },
    ],
}


# A2-L4: in-memory config cache keyed by file mtime. trigger() fires on
# every tool call / turn, and re-reading + re-parsing hooks.json each time
# is wasteful. If mtime is unchanged, reuse the cached config.
_config_cache_mtime: float | None = None
_config_cache_data: dict[str, Any] | None = None


def load_config() -> dict[str, Any]:
    """Load ``~/.zeline/hooks.json`` (cached by mtime), creating defaults on first run."""
    global _config_cache_mtime, _config_cache_data
    path = config_path()
    try:
        mtime: float | None = path.stat().st_mtime if path.is_file() else None
    except OSError:
        mtime = None
    if _config_cache_data is not None and _config_cache_mtime == mtime:
        return _config_cache_data
    data = _load_config_uncached()
    _config_cache_mtime = mtime
    _config_cache_data = data
    return data


def _load_config_uncached() -> dict[str, Any]:
    """Load ``~/.zeline/hooks.json``, creating defaults on first run."""
    path = config_path()
    if not path.is_file():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_DEFAULT_CONFIG, indent=2), encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            return json.loads(json.dumps(_DEFAULT_CONFIG))
        return json.loads(json.dumps(_DEFAULT_CONFIG))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(_DEFAULT_CONFIG))
    if not isinstance(data, dict):
        return json.loads(json.dumps(_DEFAULT_CONFIG))
    data.setdefault("enabled", True)
    if not isinstance(data.get("hooks"), list):
        data["hooks"] = []
    return data


def save_config(cfg: dict[str, Any]) -> None:
    """Persist hook config."""
    global _config_cache_mtime, _config_cache_data
    _config_cache_mtime = None  # A2-L4: invalidate cache on explicit save
    _config_cache_data = None
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def add_hook_def(name: str, event: str, command: str) -> dict[str, Any]:
    """Add a persistent ``command`` hook. Returns the stored definition.

    SECURITY — REMOTE CODE EXECUTION CAPABILITY (by design):
    The hook ``command`` runs as a shell command with YOUR full user
    privileges — no sandbox, no approval prompt, no timeout beyond 5s per
    event. It runs every time ``event`` fires, as long as the hook is
    enabled. A malicious or careless command (``rm -rf ~``, a curl-pipe
    to a remote script, credential exfiltration) will execute silently in
    the background.

    Rules:
    - Only add commands you wrote yourself and fully understand.
    - Never paste hook commands from webpages, chats, emails, or skills.
    - Review ``zeline hooks list`` output periodically; remove anything
      you did not add.
    - Hooks are a local-operator feature. The agent itself has no tool to
      create hooks — only a human at the CLI can.
    """
    if event not in EVENTS:
        raise ValueError(f"Unknown hook event: {event!r}. Valid: {sorted(EVENTS)}")
    name = re.sub(r"[^a-zA-Z0-9_-]", "", str(name).strip())[:64]
    if not name:
        raise ValueError("Hook name must not be empty")
    if not str(command).strip():
        raise ValueError("Hook command must not be empty")
    cfg = load_config()
    hooks = cfg["hooks"]
    # Replace same-name hook
    hooks[:] = [h for h in hooks if h.get("name") != name]
    entry = {
        "name": name,
        "event": event,
        "enabled": True,
        "type": "command",
        "command": str(command)[:2000],
    }
    hooks.append(entry)
    save_config(cfg)
    return entry


def remove_hook_def(name: str) -> bool:
    """Remove a persistent hook by name. Returns True if one was removed."""
    cfg = load_config()
    hooks = cfg["hooks"]
    kept = [h for h in hooks if h.get("name") != name]
    removed = len(kept) != len(hooks)
    if removed:
        cfg["hooks"] = kept
        save_config(cfg)
    return removed


def set_hook_enabled(name: str, enabled: bool) -> bool:
    """Enable/disable a persistent hook. Returns True if found."""
    cfg = load_config()
    found = False
    for h in cfg["hooks"]:
        if h.get("name") == name:
            h["enabled"] = bool(enabled)
            found = True
    if found:
        save_config(cfg)
    return found


# ---------------------------------------------------------------------------
# Built-in hooks
# ---------------------------------------------------------------------------

def _builtin_command_logger(data: dict[str, Any]) -> None:
    """Log every tool call as JSONL (owner-only file, secrets redacted)."""
    entry = {
        "ts": time.time(),
        "event": ON_TOOL_CALL,
        "identity": str(data.get("identity", ""))[:128],
        "tool": str(data.get("name", ""))[:128],
        # H5: scrub secret-looking arg values before persisting to disk.
        "args": str(_redact_args(data.get("args", "")))[:500],
    }
    _append_jsonl(log_dir() / "tool-calls.jsonl", entry)


def _builtin_session_memory(data: dict[str, Any]) -> None:
    """Save an episodic memory entry when a turn actually did work."""
    tool_calls = int(data.get("tool_calls") or 0)
    if tool_calls <= 0:
        return  # quiet turns are not worth remembering
    try:
        from zeline import memory as _mem

        identity = str(data.get("identity") or "cli:local")
        _mem.add_episode(
            identity,
            f"Agent turn ({tool_calls} tool calls)",
            [f"Turn used {tool_calls} tool calls."],
            source="hooks",
        )
    except Exception:
        # Memory backend missing/misconfigured: fall back to a local log
        entry = {
            "ts": time.time(),
            "event": ON_TURN_END,
            "identity": str(data.get("identity", ""))[:128],
            "tool_calls": tool_calls,
        }
        _append_jsonl(log_dir() / "turns.jsonl", entry)


_BUILTINS: dict[str, Callable[[dict], Any]] = {
    "command-logger": _builtin_command_logger,
    "session-memory": _builtin_session_memory,
}


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------

def _run_with_timeout(
    func: Callable[[], Any], timeout: float
) -> tuple[bool, Any]:
    """Run ``func`` with a timeout. Returns (ok, result-or-error-string).

    The worker runs in a *daemon* thread: on timeout the thread is abandoned
    (it keeps running detached, like before) but a daemon thread never
    blocks interpreter shutdown. This fixes the non-daemon
    ThreadPoolExecutor workers that could hang the whole process on exit.
    """
    outcome: list[Any] = [None]
    error: list[BaseException | None] = [None]

    def _target() -> None:
        try:
            outcome[0] = func()
        except BaseException as exc:  # noqa: BLE001 - capture, don't propagate
            error[0] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout=timeout)
    if worker.is_alive():
        return False, f"timed out after {timeout}s"
    if error[0] is not None:
        # Hooks must never crash the agent: surface as a failed result.
        return False, str(error[0])[:300]
    return True, outcome[0]


def _run_command_hook(entry: dict[str, Any], event: str, data: dict[str, Any]) -> None:
    """Run a ``command``-type persistent hook."""
    cmd = str(entry.get("command", ""))
    if not cmd:
        return
    payload = json.dumps({"event": event, "hook": entry.get("name"), "data": data})
    env = dict(os.environ)
    env["HOOK_EVENT"] = event
    env["HOOK_NAME"] = str(entry.get("name", ""))
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            input=payload,
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
            env=env,
        )
        if proc.returncode != 0:
            _record_error(
                str(entry.get("name")), event,
                f"command exited {proc.returncode}: {proc.stderr[:200]}",
            )
    except subprocess.TimeoutExpired:
        _record_error(str(entry.get("name")), event, f"timed out after {HOOK_TIMEOUT}s")
    except Exception as exc:  # noqa: BLE001
        _record_error(str(entry.get("name")), event, str(exc)[:300])


def _snapshot_registered(event: str) -> list[tuple[str, Callable[[dict], Any]]]:
    with _registry_lock:
        return list(_registry.get(event, []))


def trigger(event: str, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Fire all hooks for ``event``.

    Every hook runs isolated with a per-hook timeout (:data:`HOOK_TIMEOUT`).
    A failing hook is recorded via :func:`hook_errors` and never propagates.
    This function itself never raises.

    Returns a list of ``{"hook": name, "ok": bool, "detail": ...}``.
    """
    results: list[dict[str, Any]] = []
    try:
        if event not in EVENTS:
            return results
        # Deep-copy so hooks can't mutate the caller's dict (or share
        # nested mutable state with each other via the same object).
        data = copy.deepcopy(data or {})

        cfg = load_config()
        if not cfg.get("enabled", True):
            return results

        # 1) In-process callbacks
        for name, cb in _snapshot_registered(event):
            ok, detail = _run_with_timeout(lambda cb=cb: cb(data), HOOK_TIMEOUT)
            if not ok:
                _record_error(name, event, detail)
            results.append({"hook": name, "ok": ok, "detail": detail})

        # 2) Persistent hooks from ~/.zeline/hooks.json
        for entry in cfg.get("hooks", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("event") != event or not entry.get("enabled", True):
                continue
            hname = str(entry.get("name", "unnamed"))[:64]
            htype = entry.get("type", "builtin")
            try:
                if htype == "builtin":
                    func = _BUILTINS.get(hname)
                    if func is None:
                        _record_error(hname, event, "unknown builtin hook")
                        results.append({"hook": hname, "ok": False, "detail": "unknown builtin"})
                        continue
                    ok, detail = _run_with_timeout(lambda f=func: f(data), HOOK_TIMEOUT)
                    if not ok:
                        _record_error(hname, event, detail)
                    results.append({"hook": hname, "ok": ok, "detail": detail})
                elif htype == "command":
                    ok, detail = _run_with_timeout(
                        lambda e=entry: _run_command_hook(e, event, data), HOOK_TIMEOUT
                    )
                    if not ok:
                        _record_error(hname, event, detail)
                    results.append({"hook": hname, "ok": ok, "detail": detail})
                else:
                    _record_error(hname, event, f"unknown hook type: {htype!r}")
                    results.append({"hook": hname, "ok": False, "detail": "unknown type"})
            except Exception as exc:  # noqa: BLE001 - belt and suspenders
                _record_error(hname, event, str(exc)[:300])
                results.append({"hook": hname, "ok": False, "detail": str(exc)[:200]})
    except Exception as exc:  # noqa: BLE001 - trigger itself must never raise
        _record_error("trigger", event, str(exc)[:300])
    return results


def describe() -> dict[str, Any]:
    """Summary for CLI/debugging."""
    cfg = load_config()
    return {
        "enabled": cfg.get("enabled", True),
        "config": str(config_path()),
        "registered_callbacks": list_registered(),
        "persistent_hooks": cfg.get("hooks", []),
        "recent_errors": hook_errors()[-10:],
        "builtin_hooks": sorted(_BUILTINS),
        "events": sorted(EVENTS),
    }
