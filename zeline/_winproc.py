"""Windows process helpers for Zeline's gateway lifecycle.

The gateway supervisor was written against POSIX semantics: ``os.kill(pid, 0)``
to probe liveness, ``os.killpg`` to signal a whole process group, and
``/proc/<pid>/stat`` for a start-time token that makes PID reuse detectable.
None of that works on Windows, and one of them is actively dangerous:

    os.kill(pid, 0) on Windows calls TerminateProcess(handle, 0)

That KILLS the process instead of probing it. A liveness check would have shut
the gateway down on every ``zeline gateway status``.

This module provides Windows equivalents using ctypes against kernel32, so no
subprocess or WMI call is needed on the hot paths:

- :func:`pid_alive`      -> OpenProcess + GetExitCodeProcess
- :func:`creation_token` -> GetProcessTimes (stable per-process, like starttime)
- :func:`terminate_tree` -> taskkill /T /F (whole tree, mirrors killpg)
- :func:`command_line`   -> PowerShell CIM query (slow fallback only)

Every function degrades to a safe default (``False`` / ``None`` / ``""``) when
kernel32 is unavailable, so importing this module on POSIX is harmless.
"""

from __future__ import annotations

import ctypes
import subprocess
from typing import Any

# ``ctypes.windll`` only exists on Windows; resolve it dynamically so this
# module imports cleanly (and type-checks) on POSIX. Typed as ``Any`` because
# the kernel32 surface is not introspectable off-Windows.
_kernel32: Any = None
_wintypes: Any = None
if hasattr(ctypes, "windll"):  # pragma: no cover - Windows only
    try:
        from ctypes import wintypes as _wintypes_mod

        _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        _wintypes = _wintypes_mod
    except (ImportError, ValueError, AttributeError):
        _kernel32 = None
        _wintypes = None

# QUERY_LIMITED_INFORMATION works without elevation and is enough for both
# liveness and timing queries.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

#: Flags for ``subprocess.Popen`` that replace POSIX ``start_new_session``.
#: NEW_PROCESS_GROUP makes the child a group leader so a signal can reach the
#: whole tree; NO_WINDOW keeps a console window from flashing on spawn.
CREATION_FLAGS = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW


def available() -> bool:
    """True when the kernel32 bindings needed by this module are usable."""
    return _kernel32 is not None and _wintypes is not None


def _open(pid: int):  # pragma: no cover - Windows only
    if not available():
        return None
    handle = _kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    return handle or None


def pid_alive(pid: int) -> bool:  # pragma: no cover - Windows only
    """True when the PID belongs to a running process.

    Never uses ``os.kill``, which would terminate the target on Windows.
    """
    handle = _open(pid)
    if not handle:
        return False
    try:
        code = _wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def creation_token(pid: int) -> str | None:  # pragma: no cover - Windows only
    """Return a stable per-process identity token from its creation time.

    Windows reuses PIDs, so the raw PID alone cannot prove the process we
    stored is the process we are about to kill. Creation time changes for every
    new process, giving the same guarantee as ``starttime`` on Linux.
    """
    handle = _open(pid)
    if not handle:
        return None
    try:
        created = _wintypes.FILETIME()
        exited = _wintypes.FILETIME()
        kernel = _wintypes.FILETIME()
        user = _wintypes.FILETIME()
        ok = _kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return f"wincreate:{stamp}"
    finally:
        _kernel32.CloseHandle(handle)


def terminate_tree(pid: int) -> bool:  # pragma: no cover - Windows only
    """Force-kill a process and everything it spawned.

    Windows has no ``killpg``. ``taskkill /T`` walks the child tree, which is
    what the POSIX code achieves by signalling the process group.
    """
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def command_line(pid: int) -> str:  # pragma: no cover - Windows only
    """Best-effort command line for a PID (slow; fallback verification only).

    ``wmic`` is deprecated on Windows 11, so this uses a CIM query instead.
    Returns an empty string when the lookup fails.
    """
    query = (
        "(Get-CimInstance Win32_Process -Filter "
        f"'ProcessId={int(pid)}').CommandLine"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()
