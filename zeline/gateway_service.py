"""Lifecycle process untuk gateway Zeline.

`zeline gateway run` cocok untuk foreground / systemd / tmux.
Modul ini memberi gateway lifecycle commands:

    zeline gateway start [--only telegram]
    zeline gateway stop
    zeline gateway status
    zeline gateway log

State hanya menyimpan PID milik child Python yang Zeline spawn sendiri, di
``~/.zeline/gateway.pid``. Log dan state memiliki permission privat.
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

from zeline import config
from zeline.gateways import validate_gateway

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


def _ps_field(pid: int, fmt: str) -> str | None:
    """Baca satu kolom ``ps`` untuk PID (fallback lintas-OS, mis. macOS/BSD).

    ``/proc`` tidak ada di macOS, jadi identitas process diambil dari ``ps``.
    ``lstart`` (waktu mulai absolut) stabil sepanjang umur process dan berubah
    untuk process baru — cocok sebagai identitas seperti starttime di Linux.
    """
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", f"{fmt}="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _process_start_token(pid: int) -> str | None:
    """Identitas start process yang stabil & lintas-OS.

    Urutan: ``/proc/<pid>/stat`` (Linux/Android) → ``ps -o lstart`` (macOS/BSD).
    Prefix (``ticks:``/``lstart:``) dipertahankan supaya token dari mesin yang
    sama selalu konsisten antara ``start`` dan ``stop``.
    """
    ticks = _process_start_ticks(pid)
    if ticks is not None:
        return f"ticks:{ticks}"
    lstart = _ps_field(pid, "lstart")
    if lstart is not None:
        return f"lstart:{lstart}"
    return None


def _process_looks_like_zeline(pid: int) -> bool:
    """Verifikasi ringan bahwa PID adalah process gateway Zeline (bukan PID daur-ulang).

    Baca command line dari ``/proc/<pid>/cmdline`` (Linux/Android) atau ``ps -o
    args`` (macOS/BSD). Dipakai sebagai lapis keamanan tambahan sebelum SIGKILL.
    """
    cmdline = ""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_text(encoding="utf-8", errors="replace")
    except OSError:
        cmdline = ""
    if not cmdline:
        cmdline = _ps_field(pid, "args") or _ps_field(pid, "command") or ""
    cmdline = cmdline.replace("\x00", " ").lower()
    return "zeline" in cmdline


def _process_matches_state(state: dict[str, Any]) -> bool:
    """True hanya bila PID masih child Zeline yang persis sama.

    State versi lama tanpa start_ticks dianggap tidak terverifikasi dan tidak
    pernah boleh di-signal. Ini fail-closed: lebih baik user start ulang daripada
    service manager menghentikan process lain yang kebetulan memakai PID lama.
    """
    pid = int(state["pid"])
    if not _pid_alive(pid):
        return False
    # Token baru (lintas-OS) diutamakan; state lama menyimpan ``start_ticks`` saja.
    expected_token = str(state.get("start_token", ""))
    if expected_token:
        actual_token = _process_start_token(pid)
        return actual_token is not None and actual_token == expected_token
    expected_ticks = str(state.get("start_ticks", ""))
    if expected_ticks:
        actual_ticks = _process_start_ticks(pid)
        return actual_ticks is not None and actual_ticks == expected_ticks
    # State tanpa identitas apa pun: verifikasi via command line agar tetap bisa
    # dihentikan (fail-closed hanya bila process jelas bukan Zeline).
    return _process_looks_like_zeline(pid)


def _command(only: list[str] | None) -> list[str]:
    command = [sys.executable, "-m", "zeline.cli", "gateway", "run"]
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
        return False, "Tidak ada gateway aktif. Jalankan `zeline gateway setup`."
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
        # Spawn dari direktori NETRAL (~/.zeline), bukan cwd user. Kalau cwd
        # kebetulan berisi folder bernama `zeline` (mis. checkout repo di
        # ~/zeline saat user berada di home), `python -m zeline.cli` akan
        # menambahkan cwd ke sys.path dan folder itu MENIMPA paket terpasang
        # sebagai namespace package tanpa __init__ → ImportError __version__.
        # PYTHONSAFEPATH=1 (Py3.11+) juga mencegah cwd diprepend ke sys.path.
        child_env = {**os.environ, "PYTHONSAFEPATH": "1"}
        process = subprocess.Popen(
            _command(only),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(config.DATA_DIR),
            env=child_env,
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

    # Identitas process lintas-OS: Linux/Android pakai /proc starttime, macOS/BSD
    # pakai `ps -o lstart`. Bila keduanya gagal (jarang), simpan tanpa token —
    # stop() masih bisa menghentikan lewat verifikasi command line, jadi tidak
    # perlu membunuh child yang baru sukses dijalankan.
    start_token = _process_start_token(process.pid)
    start_ticks = _process_start_ticks(process.pid)
    state = {
        "pid": process.pid,
        "start_token": start_token or "",
        "start_ticks": start_ticks or "",
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


def _signal_process(pid: int, sig: int) -> bool:
    """Kirim signal ke SELURUH process group child bila memungkinkan.

    ``start()`` memakai ``start_new_session=True`` sehingga child menjadi
    process-group leader. Mengirim ke grup (``killpg``) menjangkau thread poller
    dan subprocess yang di-spawn gateway — inilah yang bikin SIGTERM tunggal
    sering gagal di Termux. Fallback ke ``os.kill`` bila pgid tak terbaca.
    """
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def stop(wait_seconds: float = 8.0, grace_seconds: float = 4.0) -> tuple[bool, str]:
    """Hentikan gateway: SIGTERM → (grace) → SIGKILL otomatis, lalu bersihkan state.

    Di Termux ``runtime.stop()`` kerap menggantung sehingga SIGTERM saja tidak
    mematikan proses. Kita naikkan ke SIGKILL setelah ``grace_seconds`` dan kirim
    ke seluruh process group, sehingga ``gateway stop`` tidak lagi meninggalkan
    proses zombie.
    """
    state = _load_state()
    if not state:
        _remove_state()
        return False, "Gateway tidak berjalan."
    pid = int(state["pid"])
    if not _process_matches_state(state):
        _remove_state()
        return False, "Gateway tidak berjalan (state PID bukan process Zeline yang cocok dibersihkan)."

    if not _signal_process(pid, signal.SIGTERM):
        # Proses sudah hilang atau tak bisa di-signal.
        if not _process_matches_state(state):
            _remove_state()
            return True, "Gateway sudah berhenti."
        return False, f"Tidak punya izin menghentikan PID {pid}."

    # Fase 1: tunggu shutdown anggun setelah SIGTERM.
    grace_deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < grace_deadline:
        if not _process_matches_state(state):
            _remove_state()
            return True, "Gateway dihentikan."
        time.sleep(0.1)

    # Fase 2: eskalasi ke SIGKILL (seluruh process group).
    escalated = _signal_process(pid, signal.SIGKILL)
    kill_deadline = time.monotonic() + max(0.5, wait_seconds - grace_seconds)
    while time.monotonic() < kill_deadline:
        if not _process_matches_state(state):
            _remove_state()
            suffix = " (perlu SIGKILL)" if escalated else ""
            return True, f"Gateway dihentikan{suffix}."
        time.sleep(0.1)

    if not _process_matches_state(state):
        _remove_state()
        return True, "Gateway dihentikan (perlu SIGKILL)."
    return False, f"PID {pid} tidak mati bahkan setelah SIGKILL. Cek manual: ps | grep zeline. Log: {LOG_FILE}"


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
