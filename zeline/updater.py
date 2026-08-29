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
import os
import re
import shutil
import subprocess
import tempfile
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


def _latest_tag() -> str:
    import json

    data = json.loads(_https_get(LATEST_API, accept="application/vnd.github+json").decode("utf-8"))
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


def _pause_gateway_for_update() -> bool:
    """Drain a running gateway before the venv is mutated.

    Replacing the installed package under a live gateway means the running
    process keeps old code in memory while new code lands on disk — and any
    in-flight agent turn can be cut off mid-build. Draining first lets active
    turns finish, then the gateway exits cleanly and we relaunch it after.

    Returns True when a gateway was running and should be restarted afterwards.
    """
    try:
        from zeline import gateway_service
    except Exception:  # noqa: BLE001 — updater must work even if the service module is broken
        return False
    try:
        active, _message, _state = gateway_service.status()
        if not active:
            return False
        print("  Gateway is running — finishing in-flight work before updating…")
        _ok, message = gateway_service.drain_then_stop()
        print(f"  {message}")
        return True
    except Exception as exc:  # noqa: BLE001 — never let lifecycle handling abort an update
        print(f"  WARNING: could not pause the gateway ({exc.__class__.__name__}); continuing.")
        return False


def _resume_gateway_after_update() -> None:
    try:
        from zeline import gateway_service

        started, message = gateway_service.start()
        print(f"  {message}")
        if started:
            print("  Gateway relaunched on the updated code.")
    except Exception as exc:  # noqa: BLE001 — report, don't crash a successful update
        print(f"  WARNING: could not restart the gateway ({exc.__class__.__name__}).")
        print("  Start it manually: zeline gateway start")


def update() -> int:
    installer_name = _installer_name()
    print(f"Zeline updater · current version {__version__}")
    resume_gateway = _pause_gateway_for_update()
    checkout = _checkout_root()
    if checkout is not None:
        local_installer = checkout / installer_name
        if local_installer.is_file():
            print(f"  Source checkout detected: {checkout}")
            print(f"  Updating from local source via {installer_name} (--source)")
            code = _run_installer(local_installer, checkout)
            if resume_gateway:
                _resume_gateway_after_update()
            return code
        print(f"  Source checkout detected but {installer_name} is missing; using the release installer.")

    try:
        tag = _latest_tag()
    except Exception as exc:  # noqa: BLE001 — updater must report, never crash
        print(f"  ERROR: could not read latest release ({exc.__class__.__name__}: {exc}).")
        print("  Check your connection, or re-run the install command from the docs.")
        if resume_gateway:
            _resume_gateway_after_update()
        return 1

    print(f"  Latest release: {tag}")
    base = f"https://github.com/{REPO}/releases/download/{tag}"
    with tempfile.TemporaryDirectory(prefix="zeline-update.") as raw:
        tmp = Path(raw)
        installer = tmp / installer_name
        try:
            sums_text = _https_get(f"{base}/SHA256SUMS").decode("utf-8")
            installer_bytes = _https_get(f"{base}/{installer_name}")
        except Exception as exc:  # noqa: BLE001 — updater must report, never crash
            print(f"  ERROR: download failed ({exc.__class__.__name__}: {exc}).")
            if resume_gateway:
                _resume_gateway_after_update()
            return 1
        expected = _expected_sha(sums_text, installer_name)
        actual = hashlib.sha256(installer_bytes).hexdigest()
        if actual != expected:
            print(f"  ERROR: {installer_name} checksum mismatch — refusing to run.")
            if resume_gateway:
                _resume_gateway_after_update()
            return 1
        print(f"  {installer_name} SHA-256 verified.")
        installer.write_bytes(installer_bytes)
        code = _run_installer(installer)
    if resume_gateway:
        _resume_gateway_after_update()
    elif code == 0:
        print("\nZeline updated. Restart the gateway to load it: zeline gateway restart")
    return code
