"""Lifecycle process untuk gateway Aesora.

`aesora gateway run` cocok untuk foreground / systemd / tmux.
Modul ini memberi gateway lifecycle commands:

    aesora gateway start [--only telegram]
    aesora gateway stop
    aesora gateway status
    aesora gateway log

State hanya menyimpan PID milik child Python yang Aesora spawn sendiri, di
``~/.aesora/gateway.pid``. Log dan state memiliki permission privat.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aesora import config
from aesora.gateways import validate_gateway

LOG_FILE = config.LOG_DIR / "gateway.log"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_state() -> dict[str, Any] | None:
    try:
        state = json.loads(config.PID_FILE.read_text(encoding="utf-8"))
        pid = int(state.get("pid", 0))
        if pid <= 0:
            return None
        state["pid"] = pid
        only = state.get("only", [])
        state["only"] = [str(item) for item in only] if isinstance(only, list) else []
        return state
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _remove_state() -> None:
    try:
        config.PID_FILE.unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but current owner cannot signal it.
        return True
    return True


def _process_start_ticks(pid: int) -> str | None:
    """Baca Linux/Android process-start tick dari ``/proc/<pid>/stat``.

    PID sendiri bisa dipakai ulang sistem. Field 22 (starttime) berubah untuk
    setiap process baru, jadi pair ``pid + start_ticks`` aman dipakai sebagai
    identitas child. Parsing memakai rsplit karena nama process dapat memuat
    tanda kurung.
    """
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        raw = stat_path.read_text(encoding="utf-8")
        remainder = raw.rsplit(")", 1)[1].split()
        # remainder dimulai pada field 3 (state); field 22 berada di index 19.
        return remainder[19] if len(remainder) > 19 else None
    except (OSError, IndexError):
        return None


def _process_matches_state(state: dict[str, Any]) -> bool:
    """True hanya bila PID masih child Aesora yang persis sama.

    State versi lama tanpa start_ticks dianggap tidak terverifikasi dan tidak
    pernah boleh di-signal. Ini fail-closed: lebih baik user start ulang daripada
    service manager menghentikan process lain yang kebetulan memakai PID lama.
    """
    pid = int(state["pid"])
    expected_ticks = str(state.get("start_ticks", ""))
    if not expected_ticks or not _pid_alive(pid):
        return False
    actual_ticks = _process_start_ticks(pid)
    return actual_ticks is not None and actual_ticks == expected_ticks


def _command(only: list[str] | None) -> list[str]:
    command = [sys.executable, "-m", "aesora.cli", "gateway", "run"]
    for name in only or []:
        command.extend(["--only", name])
    return command


def start(only: list[str] | None = None) -> tuple[bool, str]:
    """Spawn gateway child in background; return success + human message."""
    config.ensure_data_dirs()
    active, _message, _state = status()
    if active:
        current = _load_state() or {}
        return False, f"Gateway sudah berjalan (PID {current.get('pid', '?')})."

    only = list(dict.fromkeys(only or []))  # dedupe while preserving user order
    for name in only:
        if name not in config.GATEWAYS:
            return False, f"Gateway tidak dikenal: {name}"
    enabled = [
        name for name, gateway_cfg in config.GATEWAYS.items()
        if gateway_cfg.get("enabled", False) and (not only or name in only)
    ]
    if not enabled:
        return False, "Tidak ada gateway aktif. Jalankan `aesora gateway setup`."
    for name in enabled:
        errors = validate_gateway(name, config.GATEWAYS[name])
        if errors:
            return False, f"Gateway {name} belum valid: {'; '.join(errors)}"

    try:
        # `a+` means users can inspect logs after a crash, while source code
        # never has to invoke a shell or interpolate paths.
        log_handle = LOG_FILE.open("a", encoding="utf-8")
        try:
            os.chmod(LOG_FILE, 0o600)
        except OSError:
            pass
        process = subprocess.Popen(
            _command(only),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(Path.cwd()),
            start_new_session=True,
            close_fds=True,
        )
        # Parent no longer needs descriptor; child inherited it.
        log_handle.close()
    except OSError as exc:
        try:
            log_handle.close()  # type: ignore[name-defined]
        except Exception:
            pass
        return False, f"Gagal start gateway: {exc}"

    start_ticks = _process_start_ticks(process.pid)
    if start_ticks is None:
        # Jangan menyimpan PID yang tidak dapat diikat ke identity process.
        # Child tetap dihentikan agar tidak meninggalkan gateway tak-terkelola.
        try:
            os.kill(process.pid, signal.SIGTERM)
        except OSError:
            pass
        return False, "Gateway start gagal: tidak dapat memverifikasi identity process child."
    state = {
        "pid": process.pid,
        "start_ticks": start_ticks,
        "only": only,
        "started_at": time.time(),
        "command": _command(only),
    }
    _write_private(config.PID_FILE, json.dumps(state, ensure_ascii=False) + "\n")
    target = ", ".join(only) if only else "semua gateway aktif"
    return True, f"Gateway dijalankan (PID {process.pid}; {target}). Log: {LOG_FILE}"


def status() -> tuple[bool, str, dict[str, Any] | None]:
    """Return active, human message, and state. Stale state dibersihkan."""
    state = _load_state()
    if not state:
        _remove_state()
        return False, "Gateway tidak berjalan.", None
    pid = int(state["pid"])
    if not _process_matches_state(state):
        _remove_state()
        return False, "Gateway tidak berjalan (state PID lama/tidak cocok dibersihkan).", None
    only = state.get("only", [])
    target = ", ".join(only) if only else "semua gateway aktif"
    return True, f"Gateway berjalan (PID {pid}; {target}).", state


def stop(wait_seconds: float = 8.0) -> tuple[bool, str]:
    """Minta gateway berhenti secara anggun, lalu bersihkan state bila mati."""
    state = _load_state()
    if not state:
        _remove_state()
        return False, "Gateway tidak berjalan."
    pid = int(state["pid"])
    if not _process_matches_state(state):
        _remove_state()
        return False, "Gateway tidak berjalan (state PID bukan process Aesora yang cocok dibersihkan)."
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _remove_state()
        return False, "Gateway sudah berhenti."
    except PermissionError:
        return False, f"Tidak punya izin menghentikan PID {pid}."

    deadline = time.monotonic() + max(0.0, wait_seconds)
    while time.monotonic() < deadline:
        # Jika child mati ATAU PID dipakai ulang, state lama tidak lagi cocok.
        if not _process_matches_state(state):
            _remove_state()
            return True, "Gateway dihentikan."
        time.sleep(0.1)
    if not _process_matches_state(state):
        _remove_state()
        return True, "Gateway dihentikan."
    return False, f"SIGTERM sudah dikirim ke PID {pid}, tetapi proses belum berhenti. Cek log: {LOG_FILE}"


def tail_log(lines: int = 80) -> str:
    """Baca tail log tanpa mengeksekusi `tail` shell."""
    if not LOG_FILE.exists():
        return "(belum ada log gateway)"
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-max(1, lines):]) or "(log kosong)"
    except OSError as exc:
        return f"(gagal baca log: {exc})"


def clear_log() -> None:
    config.ensure_data_dirs()
    _write_private(LOG_FILE, "")
