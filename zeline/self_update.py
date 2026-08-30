"""Self-update and version reporting for chat surfaces (`/update`, `/version`).

The whole point of this module is one hazard: **an update cannot run inside the
process it is updating.** ``zeline update`` drains the gateway and then, if drain
times out, escalates to ``SIGKILL`` on the gateway's *process group*. A gateway
that ran the update in-process would therefore kill its own installer partway
through writing the venv, leaving a half-installed package and no gateway to
report it.

So `/update` spawns a **detached** child (``start_new_session=True``, so it is a
new process-group leader) and returns immediately. The child:

1. posts progress straight to the Bot API -- not through the gateway, because
   the gateway is deliberately down for most of the update;
2. runs the real ``updater.update()``, which drains and relaunches the gateway;
3. verifies the result by asking a *fresh* interpreter for its version. The
   child's own ``zeline.__version__`` is the pre-update value held in memory, so
   reporting that would claim success at the old number.

A lock file makes concurrent updates impossible: two installers mutating the same
venv is the one failure mode that cannot be recovered from inside chat.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from zeline import __version__, config

LOCK_NAME = "self-update.lock"
LOG_NAME = "self-update.log"

# A stalled updater must not block the next attempt forever, but the window has
# to be wider than a slow install on a phone: pip on Termux over mobile data can
# legitimately take minutes.
LOCK_STALE_SECONDS = 1800.0


def lock_path() -> Path:
    return config.DATA_DIR / LOCK_NAME


def log_path() -> Path:
    return config.LOG_DIR / LOG_NAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        from zeline import _winproc

        return _winproc.pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock() -> dict[str, Any] | None:
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def active_update() -> dict[str, Any] | None:
    """The in-flight update, or None. Clears a lock whose owner is gone."""
    data = _read_lock()
    if not data:
        return None
    pid = int(data.get("pid", 0) or 0)
    started = float(data.get("started_at", 0) or 0)
    if _pid_alive(pid) and (time.time() - started) < LOCK_STALE_SECONDS:
        return data
    release_lock()
    return None


def acquire_lock(pid: int, *, notify: str = "") -> bool:
    """Claim the updater slot. False when another update is already running."""
    config.ensure_data_dirs()
    if active_update() is not None:
        return False
    payload = json.dumps({"pid": pid, "started_at": time.time(), "notify": notify})
    try:
        # O_EXCL so two simultaneous /update commands cannot both win the race
        # between active_update() above and the write below.
        handle = os.open(lock_path(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        os.write(handle, payload.encode("utf-8"))
    finally:
        os.close(handle)
    return True


def release_lock() -> None:
    try:
        lock_path().unlink()
    except OSError:
        pass


def _installed_version() -> str:
    """Version reported by a fresh interpreter, not the one held in memory.

    After an install, this process still holds the old ``__version__``. Asking a
    new interpreter is the only way to state the installed version honestly.
    """
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "zeline.cli", "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(config.DATA_DIR),
            env={**os.environ, "PYTHONSAFEPATH": "1"},
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    output = (completed.stdout or completed.stderr or "").strip()
    return output.split()[-1] if output else ""


def version_report() -> dict[str, Any]:
    """Read-only version facts. Never raises; the network part is best-effort."""
    from zeline import updater

    checkout = updater._checkout_root()
    report: dict[str, Any] = {
        "current": __version__,
        "latest": "",
        "up_to_date": None,
        "checkout": str(checkout) if checkout else "",
        "error": "",
        "updating": bool(active_update()),
    }
    try:
        latest = updater._latest_tag()
    except Exception as exc:  # noqa: BLE001 — offline must degrade, not fail
        report["error"] = f"{exc.__class__.__name__}"
        return report
    report["latest"] = latest
    report["up_to_date"] = latest.lstrip("v") == __version__
    return report


def start_background_update(notify: str = "") -> tuple[bool, str]:
    """Spawn the detached updater and return immediately.

    ``notify`` is an opaque destination string (``telegram:<chat_id>``) passed to
    the child, which owns all progress reporting from there on.
    """
    if active_update() is not None:
        return False, "An update is already running."
    config.ensure_data_dirs()
    command = [sys.executable, "-m", "zeline.cli", "_self-update"]
    if notify:
        command += ["--notify", notify]
    try:
        handle = log_path().open("a", encoding="utf-8")
    except OSError as exc:
        return False, f"Could not open the update log: {exc}"
    try:
        # start_new_session makes the child its own process-group leader. This is
        # the load-bearing detail: `zeline update` signals the gateway's process
        # GROUP, so an updater sharing that group would be killed by the very
        # stop it just requested.
        spawn_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            from zeline._winproc import CREATION_FLAGS

            spawn_kwargs["creationflags"] = CREATION_FLAGS
        else:
            spawn_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            cwd=str(config.DATA_DIR),
            env={**os.environ, "PYTHONSAFEPATH": "1"},
            close_fds=True,
            **spawn_kwargs,
        )
    except OSError as exc:
        handle.close()
        return False, f"Could not start the updater: {exc}"
    finally:
        try:
            handle.close()
        except Exception:  # noqa: BLE001
            pass
    return True, f"Update started (PID {process.pid})."


# -- the detached child ------------------------------------------------------


class _Ticker:
    """Progress reporter for the update, or a no-op when nothing to notify.

    Posts directly to the Bot API rather than through the gateway, because the
    gateway is intentionally stopped for most of an update. One bubble, edited on
    stage transitions only -- never a per-line stream.
    """

    def __init__(self, notify: str):
        self.enabled = False
        self.chat_id = 0
        self._api = ""
        self._message_id = 0
        self._lines: list[str] = []
        if not notify.startswith("telegram:"):
            return
        raw = notify.split(":", 1)[1].strip()
        try:
            self.chat_id = int(raw)
        except ValueError:
            return
        token = str((config.GATEWAYS.get("telegram") or {}).get("token", "")).strip()
        if not token:
            return
        self._api = f"https://api.telegram.org/bot{token}"
        self.enabled = True

    def _post(self, method: str, **params: Any) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        import requests

        try:
            response = requests.post(f"{self._api}/{method}", json=params, timeout=20)
            payload = response.json()
        except Exception:  # noqa: BLE001 — a failed notification must not abort an update
            return None
        return payload if isinstance(payload, dict) and payload.get("ok") else None

    def stage(self, line: str) -> None:
        print(f"[ticker] {line}", flush=True)
        if not self.enabled:
            return
        self._lines.append(line)
        text = "Zeline update\n" + "\n".join(self._lines)
        if not self._message_id:
            payload = self._post("sendMessage", chat_id=self.chat_id, text=text)
            if payload:
                self._message_id = int((payload.get("result") or {}).get("message_id") or 0)
            return
        self._post(
            "editMessageText",
            chat_id=self.chat_id,
            message_id=self._message_id,
            text=text,
        )

    def final(self, line: str) -> None:
        """Send the outcome as its own message so it survives as a real result."""
        print(f"[ticker] {line}", flush=True)
        self._post("sendMessage", chat_id=self.chat_id, text=line)


def run_background_update(notify: str = "") -> int:
    """Body of the detached child: update, then report what actually happened."""
    from zeline import updater

    ticker = _Ticker(notify)
    if not acquire_lock(os.getpid(), notify=notify):
        ticker.final("An update is already running — this one was not started.")
        return 1
    before = __version__
    try:
        ticker.stage(f"Current: {before}")
        try:
            latest = updater._latest_tag()
        except Exception as exc:  # noqa: BLE001
            latest = ""
            ticker.stage(f"Could not read the latest release ({exc.__class__.__name__}).")
        if latest:
            ticker.stage(f"Latest: {latest}")
            if latest.lstrip("v") == before:
                ticker.final(f"Already on the latest release ({before}). Nothing to do.")
                return 0
        ticker.stage("Draining the gateway and installing…")
        code = updater.update()
        after = _installed_version()
        if code != 0:
            ticker.final(
                f"Update failed (exit {code}). Still on {after or before}. "
                f"Details: {log_path()}"
            )
            return code
        if after and after != before:
            ticker.final(f"Updated {before} → {after}. Gateway relaunched on the new code.")
        elif after:
            ticker.final(f"Installer finished; still on {after}.")
        else:
            ticker.final(
                "Installer reported success but the version could not be read back. "
                f"Check: {log_path()}"
            )
        return 0
    finally:
        release_lock()
