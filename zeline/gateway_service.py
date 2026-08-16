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
from zeline._winproc import CREATION_FLAGS
from zeline import _winproc
from zeline.gateways import validate_gateway

IS_WINDOWS = os.name == "nt"

# ``signal.SIGKILL`` does not exist on Windows — merely referencing it raises
# AttributeError, which crashed ``stop()`` at the escalation step. Windows never
# uses the value (``_signal_process`` routes force-kills to ``taskkill /T /F``),
# so any signal number distinct from SIGTERM works as a marker. SIGBREAK (21) is
# Windows-only and unambiguous; the POSIX path keeps real SIGKILL.
_SIGKILL = getattr(signal, "SIGKILL", None) or getattr(signal, "SIGBREAK", signal.SIGTERM)

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
    # CRITICAL on Windows: os.kill(pid, 0) does not probe, it calls
    # TerminateProcess and KILLS the target. Use the kernel32 handle check.
    if IS_WINDOWS:
        return _winproc.pid_alive(pid)
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

    Urutan: kernel32 ``GetProcessTimes`` (Windows) → ``/proc/<pid>/stat``
    (Linux/Android) → ``ps -o lstart`` (macOS/BSD). Prefix
    (``wincreate:``/``ticks:``/``lstart:``) dipertahankan supaya token dari mesin
    yang sama selalu konsisten antara ``start`` dan ``stop``.
    """
    if IS_WINDOWS:
        return _winproc.creation_token(pid)
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
    if IS_WINDOWS:
        cmdline = _winproc.command_line(pid)
    else:
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
        return False, f"Gateway is already running (PID {current.get('pid', '?')})."

    only = list(dict.fromkeys(only or []))  # dedupe while preserving user order
    for name in only:
        if name not in config.GATEWAYS:
            return False, f"Unknown gateway: {name}"
    enabled = [
        name for name, gateway_cfg in config.GATEWAYS.items()
        if gateway_cfg.get("enabled", False) and (not only or name in only)
    ]
    if not enabled:
        return False, "No enabled gateway. Run `zeline gateway setup`."
    for name in enabled:
        errors = validate_gateway(name, config.GATEWAYS[name])
        if errors:
            return False, f"Gateway {name} is not valid yet: {'; '.join(errors)}"

    try:
        # `a+` means users can inspect logs after a crash, while source code
        # never has to invoke a shell or interpolate paths.
        log_handle = LOG_FILE.open("a", encoding="utf-8")
        # Catat posisi log SEBELUM child menulis apa pun. wait_until_connected
        # hanya boleh membaca dari titik ini ke depan; kalau membaca seluruh
        # file, satu baris fatal dari percobaan start yang LAMA ("token could
        # not be verified") akan dibaca ulang dan dilaporkan sebagai kegagalan
        # padahal proses baru terhubung normal.
        try:
            log_offset = LOG_FILE.stat().st_size
        except OSError:
            log_offset = 0
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
        # ``start_new_session`` is POSIX-only; Windows needs creationflags to
        # get the same detached-group behaviour (and to avoid a console flash).
        spawn_kwargs: dict[str, Any] = (
            {"creationflags": CREATION_FLAGS} if IS_WINDOWS else {"start_new_session": True}
        )
        process = subprocess.Popen(
            _command(only),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(config.DATA_DIR),
            env=child_env,
            close_fds=True,
            **spawn_kwargs,
        )
        # Parent no longer needs descriptor; child inherited it.
        log_handle.close()
    except OSError as exc:
        try:
            log_handle.close()  # type: ignore[name-defined]
        except Exception:
            pass
        return False, f"Failed to start gateway: {exc}"

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
        # Dibaca wait_until_connected supaya baris log dari start SEBELUMNYA
        # tidak ikut dinilai.
        "log_offset": log_offset,
    }
    _write_private(config.PID_FILE, json.dumps(state, ensure_ascii=False) + "\n")
    target = ", ".join(only) if only else "all enabled gateways"
    return True, f"Gateway started (PID {process.pid}; {target}). Log: {LOG_FILE}"


def status() -> tuple[bool, str, dict[str, Any] | None]:
    """Return active, human message, and state. Stale state dibersihkan."""
    state = _load_state()
    if not state:
        _remove_state()
        return False, "Gateway is not running.", None
    pid = int(state["pid"])
    if not _process_matches_state(state):
        _remove_state()
        return False, "Gateway is not running (stale/mismatched PID state cleared).", None
    only = state.get("only", [])
    target = ", ".join(only) if only else "all enabled gateways"
    return True, f"Gateway running (PID {pid}; {target}).", state


def _signal_process(pid: int, sig: int) -> bool:
    """Kirim signal ke SELURUH process group child bila memungkinkan.

    ``start()`` memakai ``start_new_session=True`` sehingga child menjadi
    process-group leader. Mengirim ke grup (``killpg``) menjangkau thread poller
    dan subprocess yang di-spawn gateway — inilah yang bikin SIGTERM tunggal
    sering gagal di Termux. Fallback ke ``os.kill`` bila pgid tak terbaca.

    Di Windows tidak ada process group POSIX: SIGTERM dipetakan ke CTRL_BREAK
    (shutdown anggun untuk process group yang di-spawn dengan
    CREATE_NEW_PROCESS_GROUP) dan SIGKILL ke ``taskkill /T /F`` yang membunuh
    seluruh pohon child — padanan terdekat dari ``killpg``.
    """
    if IS_WINDOWS:
        if sig == signal.SIGTERM:
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                return True
            except (OSError, ValueError, AttributeError):
                # CTRL_BREAK can fail when the child is not a group leader;
                # the caller escalates to SIGKILL, so report failure honestly.
                return False
        return _winproc.terminate_tree(pid)

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
        return False, "Gateway is not running."
    pid = int(state["pid"])
    if not _process_matches_state(state):
        _remove_state()
        return False, "Gateway is not running (PID state was not a matching Zeline process; cleared)."

    if not _signal_process(pid, signal.SIGTERM):
        # Proses sudah hilang atau tak bisa di-signal.
        if not _process_matches_state(state):
            _remove_state()
            return True, "Gateway already stopped."
        # Di Windows CTRL_BREAK bisa ditolak walau process masih hidup (mis.
        # child bukan group leader). Jangan menyerah: lanjut ke fase SIGKILL
        # (taskkill /T /F) di bawah, sama seperti jalur POSIX.
        if not IS_WINDOWS:
            return False, f"No permission to stop PID {pid}."

    # Fase 1: tunggu shutdown anggun setelah SIGTERM.
    grace_deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < grace_deadline:
        if not _process_matches_state(state):
            _remove_state()
            return True, "Gateway stopped."
        time.sleep(0.1)

    # Fase 2: eskalasi ke SIGKILL (seluruh process group).
    escalated = _signal_process(pid, _SIGKILL)
    kill_deadline = time.monotonic() + max(0.5, wait_seconds - grace_seconds)
    while time.monotonic() < kill_deadline:
        if not _process_matches_state(state):
            _remove_state()
            suffix = " (required SIGKILL)" if escalated else ""
            return True, f"Gateway stopped{suffix}."
        time.sleep(0.1)

    if not _process_matches_state(state):
        _remove_state()
        return True, "Gateway stopped (required SIGKILL)."
    return False, f"PID {pid} did not die even after SIGKILL. Check manually: ps | grep zeline. Log: {LOG_FILE}"


def wait_until_connected(timeout: float = 90.0) -> tuple[bool, list[str]]:
    """Watch the gateway log until each enabled platform reports 'connected',
    or a fatal error / timeout. Returns (all_ready, status_lines).

    The child process prints '[telegram] @<bot> connected via polling' once it
    finishes getMe + setMyCommands. We tail the log for those markers so
    `zeline gateway start` can confirm real readiness instead of just 'spawned'.

    Only reads log written by THIS child (from `log_offset` recorded at spawn).
    Reading the whole file made an old fatal line from a previous start get
    re-detected, so a healthy gateway was reported as failed. The default
    timeout is generous because token verification now retries: on a slow
    Termux connection a legitimate startup was measured at ~43s.
    """
    state = _load_state()
    if not state:
        return False, ["gateway process not running"]
    only = state.get("only", [])
    log_offset = int(state.get("log_offset", 0) or 0)
    expected = [
        name for name, gw in config.GATEWAYS.items()
        if gw.get("enabled", False) and (not only or name in only)
    ]
    connected: dict[str, bool] = {}
    fatal: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Process died before connecting → surface it, don't hang.
        if not _process_matches_state(_load_state() or {}):
            return False, ["gateway process exited before connecting — check `zeline gateway log`"]
        try:
            with LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(min(log_offset, LOG_FILE.stat().st_size))
                text = handle.read()
        except OSError:
            text = ""
        for line in text.splitlines():
            for name in expected:
                if f"[{name}]" in line and "connected via polling" in line:
                    connected[name] = True
                if f"[{name}]" in line and (("could not be verified" in line) or ("not started" in line)):
                    fatal.append(line.strip())
        # Terhubung menang atas baris fatal: kalau child akhirnya connect,
        # kegagalan verifikasi sebelumnya hanyalah percobaan yang sudah pulih.
        if all(connected.get(name) for name in expected):
            return True, [f"{name}: connected" for name in expected]
        if fatal:
            return False, fatal
        time.sleep(0.4)
    # Timed out — report which platforms are still pending.
    pending = [name for name in expected if not connected.get(name)]
    return False, [f"{name}: still connecting (timeout {int(timeout)}s)" for name in pending]


def tail_log(lines: int = 80) -> str:
    """Baca tail log tanpa mengeksekusi `tail` shell."""
    if not LOG_FILE.exists():
        return "(no gateway log yet)"
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-max(1, lines):]) or "(empty log)"
    except OSError as exc:
        return f"(gagal baca log: {exc})"


def clear_log() -> None:
    config.ensure_data_dirs()
    _write_private(LOG_FILE, "")
