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
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

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


def is_termux() -> bool:
    """True only inside the real Termux Android runtime."""
    prefix = str(os.environ.get("PREFIX", ""))
    return prefix.startswith("/data/data/com.termux/") or bool(os.environ.get("TERMUX_VERSION"))


def ensure_termux_wake_lock() -> tuple[bool, str]:
    """Request the app-wide Termux wake lock before gateway polling.

    Android/OEM power management suspends or kills all processes owned by the
    Termux app together. If Zeline and another agent both run under that UID,
    losing Termux makes both appear to die at once. ``termux-wake-lock`` asks
    TermuxService to hold a CPU wake lock. It is best-effort: desktop/server
    installs never call it, and a missing Termux API must not block startup.
    """
    if not is_termux():
        return False, ""
    command = shutil.which("termux-wake-lock")
    if not command:
        return False, (
            "Termux wake lock unavailable — Android may suspend Zeline and other "
            "Termux agents together. Disable battery optimization for Termux."
        )
    try:
        result = subprocess.run(
            [command], capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, (
            f"Termux wake lock failed ({exc.__class__.__name__}) — Android may suspend "
            "Zeline. Disable battery optimization for Termux."
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "request rejected").strip()[:160]
        return False, (
            f"Termux wake lock failed ({detail}) — Android may suspend Zeline. "
            "Disable battery optimization for Termux."
        )
    return True, "Termux wake lock active — Android CPU sleep protection enabled."


# Kunci eksklusif proses gateway. PID file saja TIDAK cukup mencegah gateway
# ganda: hanya `gateway start` (spawn background) yang menulis PID file,
# sedangkan `gateway run` foreground tidak. Akibatnya satu `gateway run` di
# terminal jadi tak terlihat, `gateway start` menyimpulkan "tidak jalan", lalu
# spawn poller KEDUA untuk token yang sama → tiap pesan dijawab dua kali.
#
# Kunci ini dipegang kernel pada file descriptor selama proses hidup, jadi:
#   - proses kedua langsung ditolak, siapa pun yang menjalankannya,
#   - kunci lepas OTOMATIS saat proses mati (termasuk SIGKILL/crash), jadi tidak
#     ada masalah "stale lock" seperti pada file penanda biasa.
LOCK_FILE = config.DATA_DIR / "gateway.lock"

# Byte 0 adalah region yang DIKUNCI; PID pemegang ditulis mulai byte 8.
# Alasannya Windows: ``msvcrt.locking`` mengunci rentang byte secara MANDATORY,
# sehingga membaca byte yang terkunci dari handle lain gagal dengan WinError 33
# dan pemegangnya jadi tak teridentifikasi. Dengan memisahkan region kunci dari
# region data, PID tetap bisa dibaca proses lain di semua OS.
_LOCK_PID_OFFSET = 8
# Lebar tetap region PID supaya penulisan berikutnya tidak menyisakan digit lama.
_LOCK_PID_WIDTH = 24


class GatewayLock:
    """Kunci eksklusif OS: hanya satu proses gateway boleh polling.

    Dipakai dua lapis. ``gateway run`` MEMEGANG kunci selama hidupnya (penjaga
    sebenarnya), dan ``start()`` MENGINTIP kunci sebelum spawn supaya penolakan
    terlihat langsung di terminal user, bukan cuma terkubur di log child.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or LOCK_FILE
        self._fd: int | None = None

    def _lock_fd(self, fd: int) -> bool:
        """Ambil kunci non-blocking pada fd. False = dipegang proses lain."""
        try:
            if IS_WINDOWS:
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)  # kunci HARUS byte 0, bukan posisi acak
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError):
            return False
        return True

    def acquire(self) -> bool:
        """True bila kunci didapat. Simpan PID pemegang untuk pesan error."""
        config.ensure_data_dirs()
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            # Tidak bisa membuat file kunci (mis. FS read-only): jangan menahan
            # gateway yang sah — jatuh ke penjagaan PID file seperti dulu.
            return True
        if not self._lock_fd(fd):
            os.close(fd)
            return False
        self._fd = fd
        try:
            # Tulis PID di luar byte terkunci supaya tetap terbaca proses lain.
            # Dipad lebar tetap: tanpa itu PID pendek yang menimpa PID panjang
            # (999 menimpa 123456) menyisakan digit lama di belakangnya.
            os.lseek(fd, _LOCK_PID_OFFSET, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}".ljust(_LOCK_PID_WIDTH).encode())
            os.fsync(fd)
        except OSError:
            pass
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            if IS_WINDOWS:
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def __enter__(self) -> "GatewayLock":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


def lock_holder_pid() -> int:
    """PID proses yang sedang memegang kunci gateway, 0 bila kunci bebas.

    Diuji dengan mencoba mengambil kunci lalu melepasnya lagi. Bila gagal,
    kunci sedang dipegang dan PID dibaca dari region data (byte 8+). Bila PID
    tidak terbaca, kembalikan -1 = "dipegang, PID tak diketahui" — pemanggil
    tetap tahu ada yang polling walau tidak tahu siapa.
    """
    probe = GatewayLock()
    if probe.acquire():
        probe.release()
        return 0
    fd = None
    try:
        fd = os.open(LOCK_FILE, os.O_RDONLY)
        os.lseek(fd, _LOCK_PID_OFFSET, os.SEEK_SET)
        raw = os.read(fd, _LOCK_PID_WIDTH).decode("utf-8", errors="replace").strip()
        return int(raw.split()[0])
    except (OSError, ValueError, IndexError):
        return -1
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass



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

    # Penjaga kedua: proses gateway yang TIDAK kita spawn (mis. `zeline gateway
    # run` di tab Termux lain, atau child lama yang PID file-nya sudah terhapus)
    # tetap memegang kunci. Tanpa cek ini, spawn di bawah menghasilkan DUA
    # poller pada token yang sama dan setiap pesan dijawab dua kali.
    holder = lock_holder_pid()
    if holder != 0:
        where = f"PID {holder}" if holder > 0 else "another process"
        return False, (
            f"A gateway is already polling ({where}) — it holds {LOCK_FILE.name}. "
            "Stop it first with `zeline gateway stop`; duplicate process refused."
        )

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
        # Capability marker: this child was spawned by a build that installs a
        # SIGUSR1 drain handler. Without a handler, SIGUSR1's DEFAULT POSIX
        # disposition is to TERMINATE the process instantly — so signalling an
        # older gateway would hard-kill the in-flight work we are trying to
        # protect. `drain_then_stop` refuses to signal unless it sees this flag.
        "drain": True,
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
        # Managed children are spawned with start_new_session=True, therefore
        # their PGID equals their PID and it is safe to signal the whole tree.
        # A direct foreground `gateway run` can share its shell's process group;
        # killpg there could terminate the Termux shell and unrelated agents.
        if pgid is not None and pgid == pid:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def _stop_pid(
    pid: int,
    label: str,
    wait_seconds: float,
    grace_seconds: float,
    alive: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """SIGTERM → grace → SIGKILL pada satu PID, lalu laporkan hasilnya.

    ``alive`` adalah predikat "proses masih ada?" agar pemanggil dapat memakai
    verifikasi ketat (pid + start-token dari state) atau cek sederhana untuk
    poller tak terkelola yang tidak punya state.
    """
    still_alive: Callable[[], bool] = alive or (lambda: _pid_alive(pid))

    if not _signal_process(pid, signal.SIGTERM):
        if not still_alive():
            return True, f"Gateway already stopped ({label})."
        if not IS_WINDOWS:
            return False, f"No permission to stop PID {pid} ({label})."

    grace_deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < grace_deadline:
        if not still_alive():
            return True, f"Gateway stopped ({label}, PID {pid})."
        time.sleep(0.1)

    escalated = _signal_process(pid, _SIGKILL)
    kill_deadline = time.monotonic() + max(0.5, wait_seconds - grace_seconds)
    while time.monotonic() < kill_deadline:
        if not still_alive():
            suffix = " (required SIGKILL)" if escalated else ""
            return True, f"Gateway stopped ({label}, PID {pid}){suffix}."
        time.sleep(0.1)
    return False, f"PID {pid} ({label}) did not die even after SIGKILL. Check manually: ps | grep zeline"


def drain_then_stop(
    drain_timeout: float | None = None,
    wait_seconds: float = 8.0,
    grace_seconds: float = 4.0,
) -> tuple[bool, str]:
    """Minta gateway MENYELESAIKAN kerjanya dulu, baru berhenti.

    Jalur lama (``stop``) langsung SIGTERM → SIGKILL, jadi ``gateway restart``
    dan update memotong build/install/analisis yang sedang berjalan di tengah
    jalan. Di sini kita kirim SIGUSR1 lebih dulu: proses gateway menolak turn
    baru, menunggu turn aktif selesai, lalu keluar sendiri.

    SIGUSR1 tidak ada di Windows, dan sinyal ke process group bukan padanan
    yang aman di sana, jadi Windows langsung memakai ``stop`` seperti dulu.
    Kalau drain tidak selesai dalam waktu yang diberikan, kita eskalasi ke
    ``stop`` dan MELAPORKAN bahwa ada kerja yang terpotong — bukan diam.
    """
    if drain_timeout is None:
        drain_timeout = float(getattr(config, "RESTART_DRAIN_TIMEOUT", 30.0))
    sigusr1 = getattr(signal, "SIGUSR1", None)
    if IS_WINDOWS or sigusr1 is None or drain_timeout <= 0:
        return stop(wait_seconds=wait_seconds, grace_seconds=grace_seconds)

    state = _load_state()
    if not state or not _process_matches_state(state):
        return stop(wait_seconds=wait_seconds, grace_seconds=grace_seconds)

    # A gateway spawned by an OLDER build has no SIGUSR1 handler, and the
    # default POSIX disposition for SIGUSR1 is to terminate the process
    # immediately. Signalling it would hard-kill the very work we are trying to
    # let finish — worse than the old behaviour, because it skips the graceful
    # SIGTERM phase entirely. Only drain when the child advertised the handler.
    if not state.get("drain"):
        ok, message = stop(wait_seconds=wait_seconds, grace_seconds=grace_seconds)
        return ok, (
            "Gateway predates drain support (started by an older build) — "
            "used the standard stop. " + message
        )

    pid = int(state["pid"])
    if not _signal_process(pid, sigusr1):
        return stop(wait_seconds=wait_seconds, grace_seconds=grace_seconds)

    # Beri waktu drain plus sedikit kelonggaran untuk shutdown adapter.
    deadline = time.monotonic() + drain_timeout + 5.0
    while time.monotonic() < deadline:
        if not _process_matches_state(state):
            _remove_state()
            return True, f"Gateway drained and stopped (PID {pid}); in-flight work finished."
        time.sleep(0.25)

    ok, message = stop(wait_seconds=wait_seconds, grace_seconds=grace_seconds)
    prefix = (
        f"Drain did not finish within {int(drain_timeout)}s — forced stop; "
        "in-flight work may have been cut off. "
    )
    return ok, prefix + message


def stop(wait_seconds: float = 8.0, grace_seconds: float = 4.0) -> tuple[bool, str]:
    """Hentikan gateway: SIGTERM → (grace) → SIGKILL otomatis, lalu bersihkan state.

    Di Termux ``runtime.stop()`` kerap menggantung sehingga SIGTERM saja tidak
    mematikan proses. Kita naikkan ke SIGKILL setelah ``grace_seconds`` dan kirim
    ke seluruh process group, sehingga ``gateway stop`` tidak lagi meninggalkan
    proses zombie.

    Juga menghentikan poller TAK TERKELOLA: `zeline gateway run` foreground tidak
    menulis PID file, jadi dulu `stop` menjawab "not running" padahal ada proses
    yang masih menjawab pesan — lalu `start` menambah poller kedua dan setiap
    balasan jadi dobel. Kunci proses (gateway.lock) membuat poller itu terlihat.
    """
    state = _load_state()
    if not state:
        _remove_state()
        holder = lock_holder_pid()
        if holder > 0 and _process_looks_like_zeline(holder):
            return _stop_pid(holder, "unmanaged `gateway run`", wait_seconds, grace_seconds)
        if holder != 0:
            return False, (
                f"Something holds {LOCK_FILE.name} but it does not look like a Zeline gateway. "
                "Check manually: ps | grep zeline"
            )
        return False, "Gateway is not running."
    pid = int(state["pid"])
    if not _process_matches_state(state):
        _remove_state()
        # State basi bukan jaminan bersih: bisa jadi child lama mati tapi ada
        # `gateway run` lain yang masih polling. Sapu juga lewat kunci.
        holder = lock_holder_pid()
        if holder > 0 and holder != pid and _process_looks_like_zeline(holder):
            return _stop_pid(holder, "unmanaged `gateway run`", wait_seconds, grace_seconds)
        return False, "Gateway is not running (PID state was not a matching Zeline process; cleared)."

    ok, message = _stop_pid(
        pid, "managed", wait_seconds, grace_seconds,
        alive=lambda: _process_matches_state(state),
    )
    if ok:
        _remove_state()
        # Sapu poller kedua yang mungkin ikut hidup (proses lama dari sebelum
        # kunci ada). Tanpa ini `stop` melapor sukses padahal masih ada yang
        # menjawab pesan, dan `start` berikutnya bikin dobel lagi.
        holder = lock_holder_pid()
        if holder > 0 and holder != pid and _process_looks_like_zeline(holder):
            swept_ok, swept = _stop_pid(holder, "second poller", wait_seconds, grace_seconds)
            message = f"{message} {swept}" if swept_ok else f"{message} WARNING: {swept}"
        return True, message
    return False, f"{message} Log: {LOG_FILE}"


READY_MARKERS = ("connected via polling", "listening http://")


def _log_tags(name: str) -> tuple[str, ...]:
    """Log tags an adapter may print for gateway ``name``.

    Adapters are free to prefer a hyphen in their human-facing tag
    (``[zeline-app]``) over the config key (``zeline_app``), so match both or a
    readiness line is missed and start waits out the whole timeout.
    """
    variants = {name, name.replace("_", "-"), name.replace("-", "_")}
    return tuple(f"[{variant}]" for variant in sorted(variants))


def wait_until_connected(timeout: float = 90.0) -> tuple[bool, list[str]]:
    """Watch the gateway log until each enabled platform reports readiness,
    or a fatal error / timeout. Returns (all_ready, status_lines).

    Readiness looks different per transport: a poller prints
    '[telegram] @<bot> connected via polling' after getMe + setMyCommands, while
    an HTTP adapter prints '[webhook] listening http://…' once its socket is
    bound. Both count, so `zeline gateway start` can confirm real readiness
    instead of just 'spawned' — and an HTTP-only gateway does not sit out the
    full timeout and get reported as failed while it is already serving.

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
            # Baca BINER lalu decode: seek() pada file mode teks hanya sah untuk
            # cookie dari tell(), bukan offset byte sembarang (dan CRLF di
            # Windows bikin posisi teks ≠ posisi byte).
            with LOG_FILE.open("rb") as handle:
                handle.seek(min(log_offset, LOG_FILE.stat().st_size))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            for name in expected:
                if not any(tag in line for tag in _log_tags(name)):
                    continue
                if any(marker in line for marker in READY_MARKERS):
                    connected[name] = True
                if ("could not be verified" in line) or ("not started" in line):
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
