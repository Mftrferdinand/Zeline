"""In-place updater behind the one-command `zeline update`.

Two modes, auto-detected:
- Checkout mode: if this package runs from a git checkout with an installer,
  update via that installer in `--source` mode (fast path for developers).
- Release mode: download the platform installer plus `SHA256SUMS` from the
  latest GitHub release, verify the installer checksum, then run it.

Both modes are cross-platform: POSIX (Termux, Linux, macOS, iSH) runs
`install.sh` through bash, Windows runs `install.ps1` through PowerShell.
User data under `~/.zeline` is never touched.
"""
from __future__ import annotations

import hashlib
import http.client
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from zeline import __version__

REPO = "Mftrferdinand/Zeline"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_TAG_RE = re.compile(r"^v?\d+\.\d+\.\d+")

POSIX_INSTALLER = "install.sh"
WINDOWS_INSTALLER = "install.ps1"


def _is_windows() -> bool:
    return os.name == "nt"


def _installer_name() -> str:
    return WINDOWS_INSTALLER if _is_windows() else POSIX_INSTALLER


def _powershell_bin() -> str:
    """Prefer PowerShell 7+, fall back to the bundled Windows PowerShell."""
    for candidate in ("pwsh", "powershell"):
        if shutil.which(candidate):
            return candidate
    return "powershell"


def _installer_command(installer: Path, source: Path | None) -> list[str]:
    if _is_windows():
        command = [
            _powershell_bin(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
        ]
        if source is not None:
            command += ["-Source", str(source)]
        return command
    command = ["bash", str(installer)]
    if source is not None:
        command += ["--source", str(source)]
    return command


def _checkout_root() -> Path | None:
    """Return the checkout directory if this package runs from source."""
    root = Path(__file__).resolve().parent.parent
    has_installer = (root / POSIX_INSTALLER).is_file() or (root / WINDOWS_INSTALLER).is_file()
    if has_installer and (root / "pyproject.toml").is_file() and (root / ".git").exists():
        return root
    return None


def _https_get(url: str, *, accept: str = "") -> bytes:
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    headers = {"User-Agent": f"zeline-updater/{__version__}"}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


# A dropped connection mid-download must not fail the whole update. On mobile
# data (Termux is a first-class install target) the connection regularly dies
# halfway through the installer, and `/update` drains the gateway first -- so a
# user who "just tries again" pays a second full drain. Retry the *transfer* a
# few times with backoff instead. Anything that is not a transient network
# failure (checksum mismatch, invalid tag, non-HTTPS URL) still fails fast.
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_RETRY_BASE_DELAY = 2.0  # seconds; waits are 2s, 4s, 8s

# HTTP statuses worth a second chance: rate-limited, overloaded, or a proxy
# hiccup in front of the release assets.
_RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

_TRANSIENT_NETWORK_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.IncompleteRead,
    http.client.BadStatusLine,
    ConnectionError,  # reset / aborted / refused mid-transfer
    TimeoutError,  # includes socket.timeout on 3.10+
)


def _is_transient_download_error(exc: BaseException) -> bool:
    """True when re-issuing the GET has a chance of succeeding."""
    # HTTPError is a URLError subclass, so it must be classified first: a 404
    # means the asset is genuinely missing and retrying is pointless.
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_HTTP_STATUS
    # ValueError (e.g. a non-HTTPS URL) is a programming error: never retry.
    return isinstance(exc, _TRANSIENT_NETWORK_ERRORS + (urllib.error.URLError,))


def _https_get_retry(url: str, *, accept: str = "") -> bytes:
    """GET with retries for transient network failures.

    Every download in the update path goes through this: the release metadata,
    the checksums, and the installer itself are all equally exposed to a
    connection dying halfway.
    """
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            return _https_get(url, accept=accept)
        except Exception as exc:  # noqa: BLE001 — classified by _is_transient_download_error
            if not _is_transient_download_error(exc):
                raise
            last_error = exc
            if attempt < DOWNLOAD_ATTEMPTS:
                delay = DOWNLOAD_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(
                    f"  download interrupted ({exc.__class__.__name__}: {exc}), "
                    f"retrying in {delay:.0f}s (attempt {attempt + 1}/{DOWNLOAD_ATTEMPTS})…"
                )
                time.sleep(delay)
    # Reached only after DOWNLOAD_ATTEMPTS consecutive transient failures.
    assert last_error is not None
    raise last_error


def _latest_tag() -> str:
    import json

    data = json.loads(_https_get_retry(LATEST_API, accept="application/vnd.github+json").decode("utf-8"))
    tag = str(data.get("tag_name", "")).strip()
    if not _TAG_RE.match(tag):
        raise ValueError(f"release tag looks invalid: {tag!r}")
    return tag


def _expected_sha(sums_text: str, filename: str) -> str:
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
            digest = parts[0].lower()
            if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
                return digest
    raise ValueError(f"SHA256SUMS has no valid entry for {filename}")


def _run_installer(installer: Path, source: Path | None = None) -> int:
    env = dict(os.environ)
    # Avoid module shadowing when launched from inside a running Zeline session.
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    completed = subprocess.run(_installer_command(installer, source), env=env, check=False)
    return completed.returncode


def _pause_gateway_for_update() -> list[str] | None:
    """Drain a running gateway before the venv is mutated.

    Replacing the installed package under a live gateway means the running
    process keeps old code in memory while new code lands on disk — and any
    in-flight agent turn can be cut off mid-build. Draining first lets active
    turns finish, then the gateway exits cleanly and we relaunch it after.

    Returns the gateway selection to restore (``[]`` meaning "all enabled"), or
    ``None`` when nothing was running. The selection matters: an operator who
    started only Telegram must get only Telegram back, not every enabled
    gateway. ``status()`` is the only place that knowledge survives the stop,
    because ``drain_then_stop`` deletes the state file.
    """
    try:
        from zeline import gateway_service
    except Exception:  # noqa: BLE001 — updater must work even if the service module is broken
        return None
    try:
        active, _message, state = gateway_service.status()
        if not active:
            return None
        only = list(state.get("only", [])) if isinstance(state, dict) else []
        print("  Gateway is running — finishing in-flight work before updating…")
        _ok, message = gateway_service.drain_then_stop()
        print(f"  {message}")
        return only
    except Exception as exc:  # noqa: BLE001 — never let lifecycle handling abort an update
        print(f"  WARNING: could not pause the gateway ({exc.__class__.__name__}); continuing.")
        return None


def _resume_gateway_after_update(only: list[str] | None) -> None:
    """Restart the gateway with the same selection it was running before.

    Retry with backoff: if the first start fails (port still held, venv not
    fully written, etc.), wait and try again. A gateway that does not come
    back is a critical failure — the user thinks the update worked but the
    bot is dead. We also health-check after start to confirm it is really up.
    """
    import time as _time
    try:
        from zeline import gateway_service
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR: gateway_service unavailable ({exc.__class__.__name__}).")
        print("  Start it manually: zeline gateway start")
        return

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            started, message = gateway_service.start(only or None)
            print(f"  {message}")
            if started:
                # Health check: confirm the gateway actually answers.
                _time.sleep(2)
                active, status_msg, _state = gateway_service.status()
                if active:
                    print("  Gateway relaunched on the updated code.")
                    return
                print(f"  WARNING: gateway started but status check failed ({status_msg}).")
            else:
                print(f"  WARNING: gateway start returned not-started (attempt {attempt}/{max_attempts}).")
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: restart attempt {attempt} failed ({exc.__class__.__name__}).")
        if attempt < max_attempts:
            backoff = attempt * 3
            print(f"  Retrying in {backoff}s…")
            _time.sleep(backoff)

    print("  ERROR: gateway did not restart after update.")
    print("  Start it manually: zeline gateway start")


def update() -> int:
    installer_name = _installer_name()
    print(f"Zeline updater · current version {__version__}")
    # ``None`` = nothing was running; ``[]`` = all enabled gateways; a list =
    # exactly those. Kept as-is so the restart mirrors the operator's choice.
    resume_only = _pause_gateway_for_update()
    checkout = _checkout_root()
    if checkout is not None:
        local_installer = checkout / installer_name
        if local_installer.is_file():
            print(f"  Source checkout detected: {checkout}")
            print(f"  Updating from local source via {installer_name} (--source)")
            code = _run_installer(local_installer, checkout)
            if resume_only is not None:
                _resume_gateway_after_update(resume_only)
            return code
        print(f"  Source checkout detected but {installer_name} is missing; using the release installer.")

    try:
        tag = _latest_tag()
    except Exception as exc:  # noqa: BLE001 — updater must report, never crash
        print(f"  ERROR: could not read latest release ({exc.__class__.__name__}: {exc}).")
        print("  Check your connection, or re-run the install command from the docs.")
        if resume_only is not None:
            _resume_gateway_after_update(resume_only)
        return 1

    print(f"  Latest release: {tag}")
    base = f"https://github.com/{REPO}/releases/download/{tag}"
    with tempfile.TemporaryDirectory(prefix="zeline-update.") as raw:
        tmp = Path(raw)
        installer = tmp / installer_name
        try:
            sums_text = _https_get_retry(f"{base}/SHA256SUMS").decode("utf-8")
            installer_bytes = _https_get_retry(f"{base}/{installer_name}")
        except Exception as exc:  # noqa: BLE001 — updater must report, never crash
            print(
                f"  ERROR: download failed after {DOWNLOAD_ATTEMPTS} attempts "
                f"({exc.__class__.__name__}: {exc})."
            )
            if resume_only is not None:
                _resume_gateway_after_update(resume_only)
            return 1
        expected = _expected_sha(sums_text, installer_name)
        actual = hashlib.sha256(installer_bytes).hexdigest()
        if actual != expected:
            print(f"  ERROR: {installer_name} checksum mismatch — refusing to run.")
            if resume_only is not None:
                _resume_gateway_after_update(resume_only)
            return 1
        print(f"  {installer_name} SHA-256 verified.")
        installer.write_bytes(installer_bytes)
        code = _run_installer(installer)
    if resume_only is not None:
        _resume_gateway_after_update(resume_only)
    elif code == 0:
        print("\nZeline updated. Restart the gateway to load it: zeline gateway restart")
    return code
