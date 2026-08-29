"""Tool registry Zeline.

Prinsip penting untuk instalasi publik:

- ``safe``: memory percakapan + baca skill. Cocok untuk Telegram/WA/webhook.
- ``workspace``: safe + file read/write terbatas di workspace pemilik.
- ``full``: workspace + shell. Hanya default untuk CLI lokal pemilik.

Gateway publik *tidak pernah* mendapat shell/file tools tanpa owner secara
sengaja mengubah ``tool_profile`` di config. Ini mencegah orang yang chat bot
memakai LLM sebagai remote shell di device/VPS pemilik.
"""
from __future__ import annotations

import html as _html
import base64
import ipaddress
import itertools
import json
import mimetypes
import os
import re
import signal
import socket
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from zeline import config
from zeline import memory
from zeline import skills
from zeline import network_routes
from zeline import interaction
from zeline import formatters
from zeline import mcp as mcp_module
from zeline import _winproc

ToolFunction = Callable[..., str]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]
    profiles: frozenset[str]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


SAFE_PROFILES = {"safe", "workspace", "full"}


def _resolve_workspace_path(raw_path: str, workspace: Path) -> Path:
    """Resolve a relative/absolute user path and keep it inside workspace."""
    requested = Path(raw_path).expanduser()
    candidate = requested if requested.is_absolute() else workspace / requested
    # strict=False still resolves existing symlinks, so a symlink escape is blocked.
    resolved = candidate.resolve(strict=False)
    root = workspace.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path must stay inside the workspace: {root}") from exc
    return resolved


def _read_file(path: str, workspace: Path) -> str:
    try:
        target = _resolve_workspace_path(path, workspace)
        if not target.is_file():
            return f"ERROR read file: not a file or not found: {target}"
        content = target.read_text(encoding="utf-8", errors="replace")
        if len(content) > 20_000:
            content = content[:20_000] + "\n... [truncated, file too long]"
        return content
    except Exception as exc:
        return f"ERROR read file: {exc}"


def _format_note(target: Path) -> str:
    """Formatter note for a file that was ALREADY written successfully.

    Isolated so a failure inside the formatting layer can never be reported as
    a failed write — the model would retry an operation that already landed.
    """
    try:
        return formatters.format_file(target)
    except Exception:  # noqa: BLE001 — the write already succeeded; never undo that
        return ""


def _write_file(path: str, content: str, workspace: Path) -> str:
    try:
        if len(content) > 200_000:
            return "ERROR write file: content too large (maximum 200,000 characters)."
        target = _resolve_workspace_path(path, workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        # Format AFTER the write is durable, and in its OWN try/except: a bug in
        # the formatting layer must not be reported as a failed write, or the
        # model retries an operation that already succeeded.
        return f"OK, wrote {len(content)} characters to {target}{_format_note(target)}"
    except Exception as exc:
        return f"ERROR write file: {exc}"


def _edit_file(path: str, old_text: str, new_text: str, workspace: Path) -> str:
    try:
        target = _resolve_workspace_path(path, workspace)
        content = target.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count != 1:
            hint = ""
            # Format-on-write may have rewritten the file after the model wrote
            # it (ruff normalizes 'x' to "x", prettier re-indents, gofmt aligns).
            # An old_text composed from what the model *thinks* it wrote then no
            # longer matches. Say so, or the model retries the same failing edit.
            if count == 0 and formatters.enabled() and formatters.candidates_for(target):
                hint = (
                    " The file may have been reformatted after it was written, so quoting,"
                    " indentation, or spacing can differ from what you wrote —"
                    " read_file it again and copy old_text from the current content."
                )
            return f"ERROR edit file: old_text must be unique (found {count}).{hint}"
        target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"OK, {target} edited.{_format_note(target)}"
    except Exception as exc:
        return f"ERROR edit file: {exc}"


def _patch_file(path: str, old_text: str, new_text: str, workspace: Path) -> str:
    result = _edit_file(path, old_text, new_text, workspace)
    return result.replace("edited", "patched")


def _update_task(task: str, status: str) -> str:
    allowed = {"pending", "in_progress", "completed", "cancelled"}
    clean_status = status.strip().lower()
    if clean_status not in allowed:
        return f"ERROR task: status must be one of {', '.join(sorted(allowed))}."
    clean_task = task.strip()[:500]
    if not clean_task:
        return "ERROR task: empty description."
    return json.dumps({"task": clean_task, "status": clean_status}, ensure_ascii=False)


def _search_files(query: str, workspace: Path, pattern: str = "*") -> str:
    try:
        matches = []
        for target in workspace.rglob(pattern or "*"):
            if not target.is_file() or len(matches) >= 100:
                continue
            try:
                for line_number, line in enumerate(target.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if query.lower() in line.lower():
                        matches.append(f"{target.relative_to(workspace)}:{line_number}: {line[:300]}")
                        if len(matches) >= 100:
                            break
            except OSError:
                continue
        return "\n".join(matches) or "(no results)"
    except Exception as exc:
        return f"ERROR search file: {exc}"


def _clamp_timeout(timeout: Any) -> int:
    """Normalize an agent-supplied timeout into the allowed foreground range."""
    try:
        seconds = int(float(timeout))
    except (TypeError, ValueError):
        return config.DEFAULT_SHELL_TIMEOUT_SECONDS
    if seconds <= 0:
        return config.DEFAULT_SHELL_TIMEOUT_SECONDS
    return min(seconds, config.SHELL_MAX_TIMEOUT_SECONDS)


def _truncate_output(text: str, limit: int = 12_000) -> str:
    text = (text or "").strip()
    if not text:
        return "(no output)"
    if len(text) > limit:
        return text[:limit] + "\n... [output truncated]"
    return text


# ---------------------------------------------------------------- background jobs

@dataclass
class _BackgroundJob:
    job_id: str
    command: str
    process: subprocess.Popen
    log_path: Path
    started_at: float
    log_handle: Any = None
    read_offset: int = 0
    finished_at: float | None = None

    def close_log(self) -> None:
        handle, self.log_handle = self.log_handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


_BG_JOBS: dict[str, _BackgroundJob] = {}
_BG_COUNTER = itertools.count(1)

# --------------------------------------------------------- foreground tracking
# Perintah foreground (run_shell/execute_code tanpa background) dulu dijalankan
# lewat subprocess.run, sehingga TIDAK ada handle proses yang bisa dibunuh saat
# user menekan /stop: pembatalan baru terasa setelah perintah selesai sendiri
# (mis. build 10 menit) — itu sebabnya stop terasa "tidak bisa dipaksa" dan
# gateway harus dimatikan manual. Registry ini menyimpan proses hidup per
# identity supaya cancel_identity() bisa mematikan seluruh grup prosesnya.
_FG_PROCS: dict[str, set[subprocess.Popen]] = {}
_FG_LOCK = threading.Lock()

# POSIX memakai process group (``start_new_session`` + ``killpg``) supaya seluruh
# keturunan sebuah perintah ikut mati. Windows tidak punya killpg, jadi child
# dibuat sebagai group leader lewat creationflags dan dibunuh dengan
# ``taskkill /T /F`` (lihat zeline._winproc).
IS_WINDOWS = os.name == "nt"
DETACH_KWARGS: dict[str, Any] = (
    {"creationflags": _winproc.CREATION_FLAGS} if IS_WINDOWS else {"start_new_session": True}
)


def _fg_track(identity: str, process: subprocess.Popen) -> None:
    with _FG_LOCK:
        _FG_PROCS.setdefault(identity or "cli:local", set()).add(process)


def _fg_untrack(identity: str, process: subprocess.Popen) -> None:
    with _FG_LOCK:
        bucket = _FG_PROCS.get(identity or "cli:local")
        if bucket is None:
            return
        bucket.discard(process)
        if not bucket:
            _FG_PROCS.pop(identity or "cli:local", None)


def _terminate_group(process: subprocess.Popen) -> None:
    """Bunuh proses beserta seluruh anaknya, lalu paksa bila masih bertahan."""
    if IS_WINDOWS:
        if not _winproc.terminate_tree(process.pid):
            try:
                process.kill()
            except Exception:
                pass
        try:
            process.wait(timeout=5)
        except Exception:
            pass
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except Exception:
        try:
            process.terminate()
        except Exception:
            return
    try:
        process.wait(timeout=3)
        return
    except Exception:
        pass
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def cancel_identity(identity: str) -> int:
    """Bunuh semua perintah foreground milik satu sesi. Return jumlah yang dibunuh.

    Dipanggil dari SessionStore.stop() supaya /stop benar-benar memaksa berhenti:
    tanpa ini, `pytest`/`npm install`/build yang sedang jalan tetap menahan turn
    sampai selesai walaupun user sudah membatalkan.
    """
    with _FG_LOCK:
        processes = list(_FG_PROCS.get(identity or "cli:local", ()))
    killed = 0
    for process in processes:
        if process.poll() is None:
            _terminate_group(process)
            killed += 1
    return killed


def _run_tracked(
    command: Any,
    *,
    shell: bool,
    cwd: str,
    seconds: int,
    identity: str,
) -> tuple[int, str, bool]:
    """Jalankan perintah foreground yang BISA dibunuh oleh /stop.

    Mengembalikan ``(exit_code, output, timed_out)``. Prosesnya dijalankan di
    session/grup sendiri (``start_new_session``) supaya seluruh keturunannya
    ikut mati saat dibatalkan.
    """
    process = subprocess.Popen(
        command,
        shell=shell,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ},
        **DETACH_KWARGS,
    )
    _fg_track(identity, process)
    try:
        try:
            output, _ = process.communicate(timeout=seconds)
            return int(process.returncode or 0), output or "", False
        except subprocess.TimeoutExpired:
            _terminate_group(process)
            try:
                output, _ = process.communicate(timeout=5)
            except Exception:
                output = ""
            return -1, output or "", True
    finally:
        _fg_untrack(identity, process)


def _bg_log_dir() -> Path:
    path = config.STATE_DIR / "processes"
    path.mkdir(parents=True, exist_ok=True)
    return path

def _bg_reap() -> None:
    """Close logs of exited jobs and forget them once their TTL has passed.

    Finished jobs are kept for BACKGROUND_FINISHED_TTL_SECONDS so the agent can
    still read the final output of a build/test that already exited.
    """
    now = time.time()
    for job_id, job in list(_BG_JOBS.items()):
        if job.process.poll() is None:
            continue
        job.close_log()
        if job.finished_at is None:
            job.finished_at = now
            continue
        if now - job.finished_at > config.BACKGROUND_FINISHED_TTL_SECONDS:
            _BG_JOBS.pop(job_id, None)


def _bg_prune_finished() -> None:
    """Drop the oldest finished jobs to make room for a new one (LRU pruning)."""
    finished = sorted(
        (job for job in _BG_JOBS.values() if job.process.poll() is not None),
        key=lambda job: job.finished_at or job.started_at,
    )
    for job in finished:
        if len(_BG_JOBS) < config.MAX_BACKGROUND_PROCESSES:
            return
        job.close_log()
        _BG_JOBS.pop(job.job_id, None)


def _bg_new_output(job: _BackgroundJob) -> str:
    """Return log bytes written since the last poll and advance the cursor."""
    try:
        with job.log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(job.read_offset)
            chunk = handle.read()
            job.read_offset = handle.tell()
    except OSError as exc:
        return f"(cannot read log: {exc})"
    return _truncate_output(chunk)


def _bg_status(job: _BackgroundJob) -> str:
    code = job.process.poll()
    if code is None:
        return "running"
    return f"exited (exit={code})"


def _run_shell(command: str, workspace: Path, timeout: Any = None, background: Any = False, identity: str = "cli:local") -> str:
    """Owner-only shell. Gateways do not receive this profile by default.

    ``timeout`` lets the agent raise the limit for genuinely long work such as
    ``pip install``/``npm install``/builds instead of failing at a hard 60s.
    ``background`` starts a long-lived process (server, watcher, big build) and
    returns a job id immediately; use ``process_control`` to poll/stop it.
    """
    command = (command or "").strip()
    if not command:
        return "ERROR: command is empty."
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"ERROR: cannot prepare workspace: {exc}"

    if background:
        _bg_reap()
        if len(_BG_JOBS) >= config.MAX_BACKGROUND_PROCESSES:
            _bg_prune_finished()
        live = sum(1 for job in _BG_JOBS.values() if job.process.poll() is None)
        if live >= config.MAX_BACKGROUND_PROCESSES:
            return (
                f"ERROR: too many live background processes ({live}, limit "
                f"{config.MAX_BACKGROUND_PROCESSES}). Stop one with "
                "process_control(action='kill') first."
            )
        job_id = f"bg{next(_BG_COUNTER)}"
        log_path = _bg_log_dir() / f"{job_id}.log"
        try:
            handle = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=str(workspace),
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                env={**os.environ},
                **DETACH_KWARGS,
            )
        except Exception as exc:
            return f"ERROR starting background command: {exc}"
        _BG_JOBS[job_id] = _BackgroundJob(
            job_id=job_id,
            command=command,
            process=process,
            log_path=log_path,
            started_at=time.time(),
            log_handle=handle,
        )
        return (
            f"started background job={job_id} pid={process.pid}\n"
            f"log={log_path}\n"
            f"Poll it with process_control(action='poll', job_id='{job_id}')."
        )

    seconds = _clamp_timeout(timeout)
    try:
        code, output, timed_out = _run_tracked(
            command, shell=True, cwd=str(workspace), seconds=seconds, identity=identity,
        )
        if timed_out:
            return (
                f"ERROR: command timed out (>{seconds} seconds). "
                f"Retry with a larger timeout (max {config.SHELL_MAX_TIMEOUT_SECONDS}) "
                "or run it with background=true and poll it."
            )
        return f"exit={code}\n{_truncate_output(output)}"
    except Exception as exc:
        return f"ERROR running command: {exc}"


def _process_control(action: str, job_id: str = "", lines: Any = None) -> str:
    """Inspect or stop background jobs started by run_shell(background=true)."""
    action = (action or "").strip().lower()
    if action not in {"list", "poll", "log", "kill"}:
        return "ERROR: action must be one of list, poll, log, kill."
    if action == "list":
        _bg_reap()
        if not _BG_JOBS:
            return "(no background jobs)"
        rows = []
        for job in _BG_JOBS.values():
            age = int(time.time() - job.started_at)
            rows.append(f"{job.job_id} pid={job.process.pid} {_bg_status(job)} age={age}s :: {job.command[:80]}")
        return "\n".join(rows)

    job = _BG_JOBS.get((job_id or "").strip())
    if job is None:
        return f"ERROR: unknown job_id '{job_id}'. Use process_control(action='list')."

    if action == "poll":
        status = _bg_status(job)
        chunk = _bg_new_output(job)
        _bg_reap()
        return f"job={job.job_id} status={status}\n{chunk}"

    if action == "log":
        try:
            text = job.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"ERROR reading log: {exc}"
        try:
            tail = int(float(lines)) if lines is not None else 200
        except (TypeError, ValueError):
            tail = 200
        tail = max(1, min(tail, 2000))
        body = "\n".join(text.splitlines()[-tail:])
        return f"job={job.job_id} status={_bg_status(job)}\n{_truncate_output(body)}"

    # action == "kill"
    if job.process.poll() is not None:
        job.close_log()
        _BG_JOBS.pop(job.job_id, None)
        return f"job={job.job_id} already finished (exit={job.process.returncode})."
    # Satu jalur terminasi lintas-OS (killpg di POSIX, taskkill /T di Windows).
    _terminate_group(job.process)
    job.close_log()
    _BG_JOBS.pop(job.job_id, None)
    return f"job={job.job_id} killed."


def _execute_code(code: str, workspace: Path, timeout: Any = None, identity: str = "cli:local") -> str:
    """Run an owner-only Python snippet without shell interpolation."""
    if len(code) > 100_000:
        return "ERROR: code too long (maximum 100,000 characters)."
    seconds = _clamp_timeout(timeout)
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        exit_code, output, timed_out = _run_tracked(
            [os.environ.get("PYTHON", "python"), "-c", code],
            shell=False, cwd=str(workspace), seconds=seconds, identity=identity,
        )
        if timed_out:
            return (
                f"ERROR: code timed out (>{seconds} seconds). "
                f"Retry with a larger timeout (max {config.SHELL_MAX_TIMEOUT_SECONDS})."
            )
        return f"exit={exit_code}\n{_truncate_output(output)}"
    except Exception as exc:
        return f"ERROR running code: {exc}"


def _http_request(method: str, url: str, headers: str = "", body: str = "") -> str:
    """Panggil REST API dengan method bebas (GET/POST/PUT/PATCH/DELETE).

    Beda dari web_fetch (baca halaman): ini untuk memanggil API/webhook dengan
    header + body JSON. SSRF-protected: alamat internal diblokir setelah resolusi
    DNS, sama seperti web_fetch. Diinspirasi tool http_request awas-agent, ditulis
    ulang di Python dengan proteksi jaringan privat.
    """
    method = (method or "GET").strip().upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        return f"ERROR: unsupported HTTP method: {method}"
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "ERROR: URL must be a valid http/https URL."
    host = parsed.hostname or ""
    if not host or _is_internal_ip(host):
        return "ERROR: URL points to an internal address and is blocked."
    hdrs: dict[str, str] = {"User-Agent": _UA}
    if headers.strip():
        try:
            parsed_hdrs = json.loads(headers)
            if not isinstance(parsed_hdrs, dict):
                return "ERROR: headers must be a JSON object {\"Key\": \"Value\"}."
            hdrs.update({str(k): str(v) for k, v in parsed_hdrs.items()})
        except json.JSONDecodeError as exc:
            return f"ERROR: headers is not valid JSON: {exc}"
    data = body.encode("utf-8") if body else None
    if data and "content-type" not in {k.lower() for k in hdrs}:
        stripped = body.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            hdrs["Content-Type"] = "application/json"
    try:
        response = requests.request(
            method, url, headers=hdrs, data=data, timeout=WEB_TIMEOUT, allow_redirects=True,
        )
    except requests.RequestException as exc:
        return f"ERROR request: {exc.__class__.__name__}: {exc}"
    text = response.text or ""
    if len(text) > 8_000:
        text = text[:8_000] + "\n... [truncated]"
    ctype = response.headers.get("Content-Type", "")
    return f"Status: {response.status_code} {response.reason}\nContent-Type: {ctype}\n\n{text}".strip()


def _download_file(url: str, path: str, workspace: Path) -> str:
    """Unduh file (biner/teks) dari URL publik ke dalam workspace.

    SSRF-protected & path dikurung di dalam workspace. Diinspirasi tool
    download_file awas-agent. Berguna untuk ambil release/aset/dataset tanpa
    harus lewat run_shell curl.
    """
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "ERROR: URL must be a valid http/https URL."
    host = parsed.hostname or ""
    if not host or _is_internal_ip(host):
        return "ERROR: URL points to an internal address and is blocked."
    try:
        dest = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, headers={"User-Agent": _UA}, timeout=WEB_TIMEOUT, stream=True, allow_redirects=True) as response:
            if not response.ok:
                return f"ERROR: HTTP {response.status_code} {response.reason}."
            size = 0
            with open(dest, "wb") as handle:
                for chunk in response.iter_content(65536):
                    handle.write(chunk)
                    size += len(chunk)
                    if size > DOWNLOAD_MAX_BYTES:
                        handle.close()
                        dest.unlink(missing_ok=True)
                        return f"ERROR: file exceeds the {DOWNLOAD_MAX_BYTES // (1024*1024)} MB limit."
    except requests.RequestException as exc:
        return f"ERROR download: {exc.__class__.__name__}: {exc}"
    rel = dest.relative_to(workspace) if dest.is_relative_to(workspace) else dest
    return f"OK, downloaded: {rel} ({_format_size(size)})"


def _format_size(num_bytes: int) -> str:
    for unit, factor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if num_bytes >= factor:
            return f"{num_bytes / factor:.1f} {unit}"
    return f"{num_bytes} B"


# Batas ukuran media yang dikirim ke model vision (base64 membengkak ~33%).
VISION_MAX_BYTES = 8 * 1024 * 1024
_VISION_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _analyze_media(path_or_url: str, question: str, workspace: Path) -> str:
    """Look at an image and answer a question about it via the provider vision model.

    Menerima path file di workspace ATAU URL http/https gambar. Gambar dikirim ke
    endpoint chat/completions provider aktif sebagai konten image_url (data URI
    untuk file lokal). Untuk audio/video, kembalikan pesan yang mengarahkan ke
    jalur yang tepat (transkrip/ekstraksi frame) daripada mengarang isi.
    """
    src = (path_or_url or "").strip()
    if not src:
        return "ERROR: need an image file path or URL."
    prompt = (question or "").strip() or "Describe this image in detail."

    image_url: str
    if src.lower().startswith(("http://", "https://")):
        parsed = urlparse(src)
        host = parsed.hostname or ""
        if not host or _is_internal_ip(host):
            return "ERROR: URL points to an internal address and is blocked."
        ext = Path(parsed.path).suffix.lower()
        if ext and ext not in _VISION_IMAGE_EXT:
            return (
                f"ERROR: extension `{ext}` is not a supported image. "
                "Vision supports PNG/JPG/WEBP/GIF. For audio/video, request a transcript "
                "or frame extraction first."
            )
        image_url = src
    else:
        try:
            target = _resolve_workspace_path(src, workspace)
        except ValueError as exc:
            return f"ERROR: {exc}"
        if not target.is_file():
            return f"ERROR: not a file or not found: {target}"
        ext = target.suffix.lower()
        if ext not in _VISION_IMAGE_EXT:
            if ext in {".mp3", ".ogg", ".wav", ".m4a", ".flac", ".opus"}:
                return (
                    f"File `{target.name}` is audio. The vision model only sees images. "
                    "To 'listen', transcribe first (e.g. an STT/Whisper tool) then process the text."
                )
            if ext in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
                return (
                    f"File `{target.name}` is video. The vision model only sees still images. "
                    "To 'watch', extract key frames to images (e.g. ffmpeg) then analyze "
                    "the frames with analyze_media, and/or transcribe the audio."
                )
            return f"ERROR: extension `{ext}` is not an image. Vision supports PNG/JPG/WEBP/GIF."
        data = target.read_bytes()
        if len(data) > VISION_MAX_BYTES:
            return f"ERROR: image too large (limit {VISION_MAX_BYTES // (1024*1024)} MB)."
        mime = mimetypes.guess_type(target.name)[0] or "image/png"
        b64 = base64.b64encode(data).decode("ascii")
        image_url = f"data:{mime};base64,{b64}"

    if not config.API_KEY or not config.BASE_URL or not config.MODEL:
        return "ERROR: provider is not configured for image analysis."
    payload = {
        "model": config.MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "temperature": 0.3,
        "stream": False,
    }
    try:
        response = requests.post(
            f"{config.BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=180,
        )
    except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.Timeout):
        return (
            f"ERROR: the vision model '{config.MODEL}' did not respond within 180s (timed out). "
            "The model/route is likely overloaded — try again, or switch to a faster vision-capable model with /model."
        )
    except requests.exceptions.ConnectionError:
        return f"ERROR: could not connect to the vision provider at {config.BASE_URL}. Check the router/proxy is running."
    except requests.RequestException as exc:
        return f"ERROR: network error contacting the vision provider ({exc.__class__.__name__}). Try again."
    if not response.ok:
        hint = ""
        if response.status_code in (401, 403):
            hint = " — the API key is invalid or unauthorized."
        elif response.status_code == 404:
            hint = f" — the model '{config.MODEL}' was not found or does not accept image input; switch to a vision-capable model with /model."
        elif response.status_code == 429:
            hint = " — rate limited or out of credits on the provider."
        elif response.status_code >= 500:
            hint = " — the provider is having a server-side problem; try again shortly."
        else:
            hint = " — the active model may not support image input; switch to a vision-capable model."
        return f"ERROR: vision provider HTTP {response.status_code}{hint}"
    try:
        answer = str(response.json()["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, ValueError):
        return "ERROR: vision provider returned an unexpected response."
    return answer or "(model returned no description)"


# Batas ukuran gambar hasil generate yang ditulis ke workspace (10 MB).
GENERATED_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_SIZE_ALLOWED = {"256x256", "512x512", "1024x1024", "1024x1536", "1536x1024", "1792x1024", "1024x1792", "auto"}


def _generate_image(prompt: str, path: str, workspace: Path, size: str = "1024x1024") -> str:
    """Generate an image from a text prompt via the provider's images API.

    Uses the OpenAI-compatible ``/images/generations`` endpoint against the
    active provider (works with OpenAI, or any router/proxy that forwards it).
    Requires the owner to have set a text-to-image model (``image_model`` in
    config, or ``ZELINE_IMAGE_MODEL``). The result is decoded and written into
    the workspace so it can be sent back or reused by other tools.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return "ERROR: need a text prompt describing the image to generate."
    image_model = getattr(config, "IMAGE_MODEL", "") or ""
    if not config.API_KEY or not config.BASE_URL:
        return "ERROR: provider is not configured for image generation."
    if not image_model:
        return (
            "ERROR: no text-to-image model is configured. The owner can set one with "
            "`zeline setup` (image model) or the ZELINE_IMAGE_MODEL environment variable, "
            "e.g. gpt-image-1 or dall-e-3."
        )
    size = (size or "1024x1024").strip() or "1024x1024"
    if size not in _IMAGE_SIZE_ALLOWED:
        return f"ERROR: unsupported size '{size}'. Allowed: {', '.join(sorted(_IMAGE_SIZE_ALLOWED))}."
    try:
        dest = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if dest.suffix.lower() not in _VISION_IMAGE_EXT:
        return "ERROR: output path must end in .png/.jpg/.jpeg/.webp/.gif."
    payload = {"model": image_model, "prompt": prompt, "size": size, "n": 1}
    try:
        response = requests.post(
            f"{config.BASE_URL}/images/generations",
            headers={"Authorization": f"Bearer {config.API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=180,
        )
    except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.Timeout):
        return (
            f"ERROR: the image model '{image_model}' did not respond within 180s (timed out). "
            "The model/route is likely overloaded — try again or switch the image model."
        )
    except requests.exceptions.ConnectionError:
        return f"ERROR: could not connect to the image provider at {config.BASE_URL}. Check the router/proxy is running."
    except requests.RequestException as exc:
        return f"ERROR: network error contacting the image provider ({exc.__class__.__name__}). Try again."
    if not response.ok:
        hint = ""
        if response.status_code in (401, 403):
            hint = " — the API key is invalid or unauthorized."
        elif response.status_code == 404:
            hint = f" — the model '{image_model}' or the images endpoint was not found on this provider."
        elif response.status_code == 429:
            hint = " — rate limited or out of credits on the provider."
        elif response.status_code >= 500:
            hint = " — the provider is having a server-side problem; try again shortly."
        return f"ERROR: image provider HTTP {response.status_code}{hint}"
    try:
        item = response.json()["data"][0]
    except (KeyError, IndexError, TypeError, ValueError):
        return "ERROR: image provider returned an unexpected response."
    # Providers return either inline base64 (b64_json) or a temporary URL.
    raw: bytes
    b64 = item.get("b64_json") if isinstance(item, dict) else None
    if b64:
        try:
            raw = base64.b64decode(b64)
        except (ValueError, TypeError):
            return "ERROR: image provider returned invalid base64 data."
    else:
        img_url = item.get("url") if isinstance(item, dict) else None
        if not img_url:
            return "ERROR: image provider returned neither image data nor a URL."
        try:
            with requests.get(img_url, headers={"User-Agent": _UA}, timeout=WEB_TIMEOUT, stream=True) as img_resp:
                if not img_resp.ok:
                    return f"ERROR: could not download generated image (HTTP {img_resp.status_code})."
                chunks = []
                total = 0
                for chunk in img_resp.iter_content(65536):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > GENERATED_IMAGE_MAX_BYTES:
                        return f"ERROR: generated image exceeds the {GENERATED_IMAGE_MAX_BYTES // (1024*1024)} MB limit."
                raw = b"".join(chunks)
        except requests.RequestException as exc:
            return f"ERROR downloading generated image: {exc.__class__.__name__}: {exc}"
    if len(raw) > GENERATED_IMAGE_MAX_BYTES:
        return f"ERROR: generated image exceeds the {GENERATED_IMAGE_MAX_BYTES // (1024*1024)} MB limit."
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    except OSError as exc:
        return f"ERROR writing image: {exc}"
    rel = dest.relative_to(workspace) if dest.is_relative_to(workspace) else dest
    return f"OK, generated image saved: {rel} ({_format_size(len(raw))}) using model {image_model}"


def _system_env() -> str:
    """Ringkasan lingkungan sistem: OS/arch, tool/runtime terpasang, port lokal aktif.

    Diinspirasi tool system_env awas-agent. Membantu model memutuskan perintah
    yang tersedia (python vs python3, ada node/git/docker?) sebelum menjalankannya.
    """
    import platform as _platform
    import shutil as _shutil

    lines = ["System Environment", ""]
    lines.append(f"- OS: {_platform.system()} {_platform.release()}")
    lines.append(f"- Arch: {_platform.machine()}")
    lines.append(f"- CPU: {os.cpu_count()} core")
    lines.append(f"- Python: {_platform.python_version()}")
    lines.append("")
    lines.append("Installed tools:")
    for tool in ("python", "python3", "pip", "node", "npm", "go", "gcc", "make", "git", "docker", "curl", "ffmpeg"):
        found = _shutil.which(tool)
        lines.append(f"- {tool}: {found or 'not found'}")
    lines.append("")
    lines.append("Active local ports (common):")
    active = []
    for port in (22, 80, 443, 3000, 5000, 8000, 8080, 8081, 8089, 8092, 20128):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.05)
        try:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                active.append(port)
        finally:
            sock.close()
    lines.append("- " + (", ".join(str(p) for p in active) if active else "none detected"))
    return "\n".join(lines)


WEB_TIMEOUT = 12
# (connect, read) tuple — connect di-cap ketat agar tidak menggantung saat
# host lambat/diblokir; read sedikit lebih longgar untuk halaman besar.
SEARCH_TIMEOUT = (4, 6)
# Reader-proxy (r.jina.ai) merender SERP Bing/DDG server-side; ini kerap butuh
# >6s untuk selesai. Read-timeout SEARCH_TIMEOUT yang ketat membuatnya sering
# ke-timeout dan balik 0 hasil padahal engine hidup (HTTP 200 saat diberi
# waktu). Beri read-window lebih lega KHUSUS jalur reader-proxy.
READER_SEARCH_TIMEOUT = (4, 12)
WEB_MAX_BYTES = 200_000
WEB_MAX_RESULTS = 5
DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024  # 50 MB cap untuk download_file
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
# Reader proxy: cepat & tahan blokir dari jaringan mobile/Termux (DuckDuckGo
# langsung sering timeout/HTTP 000). Semua pencarian & fetch lewat sini dulu.
_JINA_READER = "https://r.jina.ai/"


def _is_internal_ip(host: str) -> bool:
    """True jika hostname/IP menunjuk ke jaringan internal (proteksi SSRF)."""
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
        return _addr_is_internal(addr)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # DNS gagal = tidak boleh dicoba
    return all(_addr_is_internal(ipaddress.ip_address(info[4][0])) for info in infos)


def _addr_is_internal(addr: ipaddress._BaseAddress) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _html_to_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _parse_ddg_html(html_text: str) -> list[str]:
    """Ambil hasil (judul + url + snippet) dari HTML DuckDuckGo."""
    results: list[str] = []
    for match in re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?(?:result__snippet"[^>]*>(.*?)</a>)?',
        html_text,
        re.S,
    ):
        url, title, snippet = match.group(1), match.group(2), match.group(3) or ""
        title = re.sub(r"(?s)<[^>]+>", "", title).strip()
        snippet = re.sub(r"(?s)<[^>]+>", "", snippet).strip()
        if title:
            results.append(f"- {title}\n  {snippet[:300]}")
        if len(results) >= WEB_MAX_RESULTS:
            break
    return results


def _search_gnews(query: str) -> list[tuple[str, str]]:
    """Google News RSS — paling andal & cepat dari Termux (200, <1s).
    Kembalikan [(judul, link)]."""
    try:
        response = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={"User-Agent": _UA},
            timeout=SEARCH_TIMEOUT,
        )
        if not response.ok:
            return []
        root = ET.fromstring(response.content)
        out: list[tuple[str, str]] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title:
                out.append((title, link))
            if len(out) >= WEB_MAX_RESULTS:
                break
        return out
    except (requests.RequestException, ET.ParseError):
        return []


def _search_wikipedia(query: str) -> list[tuple[str, str]]:
    """Wikipedia search API — cepat & stabil (200, <1s). Bagus untuk entitas."""
    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": query,
                    "format": "json", "srlimit": WEB_MAX_RESULTS},
            headers={"User-Agent": _UA},
            timeout=SEARCH_TIMEOUT,
        )
        if not response.ok:
            return []
        hits = response.json().get("query", {}).get("search", [])
        out: list[tuple[str, str]] = []
        for h in hits:
            title = h.get("title", "").strip()
            if title:
                url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
                snippet = re.sub(r"<[^>]+>", "", h.get("snippet", "")).strip()
                out.append((f"{title} — {snippet[:120]}" if snippet else title, url))
            if len(out) >= WEB_MAX_RESULTS:
                break
        return out
    except (requests.RequestException, ValueError):
        return []


def _search_jina_ddg(query: str) -> list[tuple[str, str]]:
    """DuckDuckGo via reader proxy. Cepat bila tidak kena 403; sering gagal."""
    from urllib.parse import quote, unquote
    try:
        response = _reader_get(f"https://duckduckgo.com/html/?q={quote(query)}")
        if response is None or not response.ok or not response.text.strip():
            return []
        out: list[tuple[str, str]] = []
        # Judul: '## [judul](link)'. URL asli DDG di parameter uddg=.
        for m in re.finditer(r"#+\s*\[([^\]]+)\]\(([^)]+)\)", response.text):
            title = m.group(1).strip()
            raw = m.group(2)
            url = unquote(raw.split("uddg=", 1)[1].split("&", 1)[0]) if "uddg=" in raw else raw
            if title and url.startswith("http"):
                out.append((title, url))
            if len(out) >= WEB_MAX_RESULTS:
                break
        return out
    except requests.RequestException:
        return []


def _decode_bing_redirect(url: str) -> str:
    """Bing membungkus URL hasil di redirect `bing.com/ck/a?...&u=a1<base64url>`.
    Ekstrak & decode ke URL aslinya; kalau gagal, kembalikan apa adanya."""
    match = re.search(r"[?&]u=a1([A-Za-z0-9_\-]+)", url)
    if not match:
        return url
    encoded = match.group(1)
    encoded += "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return url


def _reader_get(target_url: str):
    """GET lewat reader proxy dengan satu retry saat timeout/gagal transien.

    Reader proxy (r.jina.ai) merender SERP server-side dan sesekali lambat pada
    percobaan pertama (cold), lalu sukses pada retry. Satu retry singkat menutup
    kasus 0-hasil-padahal-engine-hidup tanpa menggantung lama.
    """
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = requests.get(
                _JINA_READER + target_url,
                headers={"User-Agent": _UA},
                timeout=READER_SEARCH_TIMEOUT,
            )
            if resp.ok and resp.text.strip():
                return resp
        except requests.RequestException as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    return None


def _search_bing_jina(query: str) -> list[tuple[str, str]]:
    """SERP umum via Bing yang dirender reader proxy (server-side, tahan blokir).

    Ini mesin utama untuk kueri sehari-hari: mengembalikan hasil web nyata yang
    relevan (bukan cuma berita/wiki). Hasil Bing berupa link redirect ck/a yang
    di-decode balik ke URL asli.
    """
    from urllib.parse import quote
    try:
        response = _reader_get(f"https://www.bing.com/search?q={quote(query)}")
        if response is None or not response.ok or not response.text.strip():
            return []
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for match in re.finditer(r"#+\s*\[([^\]]+)\]\((https?://www\.bing\.com/ck/a[^)]+)\)", response.text):
            title = re.sub(r"\*+", "", match.group(1)).strip()
            url = _decode_bing_redirect(match.group(2))
            if not title or not url.startswith("http"):
                continue
            domain = re.sub(r"^https?://", "", url).split("/", 1)[0]
            if domain in seen:
                continue
            seen.add(domain)
            out.append((title, url))
            if len(out) >= WEB_MAX_RESULTS:
                break
        return out
    except requests.RequestException:
        return []


def _web_search(query: str) -> str:
    """Cari web dari jaringan Termux (DuckDuckGo langsung mati/SSL-fail).

    Urutan: provider premium OPSIONAL (Tavily→Exa→Brave, hanya aktif bila API
    key-nya di-set) → lalu rantai gratis bawaan jina→Bing (SERP umum, paling
    relevan untuk kueri harian) → jina→DDG → Google News RSS → Wikipedia. Bing
    lewat reader proxy dirender server-side jadi tahan blokir jaringan
    mobile/Termux. Tanpa API key, perilaku identik dengan rantai gratis lama.
    Selalu fail-fast; tidak pernah menggantung lama."""
    query = query.strip()
    if not query:
        return "ERROR: empty query."
    # 1) Premium opsional (key-gated). None bila tak ada key / semua gagal.
    try:
        from zeline import web_providers

        premium = web_providers.search_premium(query)
    except Exception:  # noqa: BLE001 — premium layer must never break free search
        premium = None
    if premium:
        return "\n".join(f"- {title}\n  {url}" for title, url in premium)
    # 2) Rantai gratis bawaan (selalu tersedia, tanpa key/dependency baru).
    for engine in (_search_bing_jina, _search_jina_ddg, _search_gnews, _search_wikipedia):
        results = engine(query)
        if results:
            return "\n".join(f"- {title}\n  {url}" for title, url in results)
    return "ERROR: could not search the web (all sources failed). Try again later."


def _looks_like_cf_challenge(text: str) -> bool:
    """Deteksi halaman tantangan Cloudflare (bukan konten asli).

    FTMO & banyak situs prop firm pakai CF 'managed challenge': fetch (termasuk
    via reader proxy) balik halaman 'Just a moment…' berisi JS challenge, bukan
    isi halaman. Ciri khas: title 'Just a moment', variabel _cf_chl_opt, atau
    token challenge __cf_chl. Kalau kena ini, konten tidak berguna → picu
    fallback Wayback.

    Catatan: sengaja TIDAK mencocokkan hostname `challenges.cloudflare.com`
    mentah — CodeQL menandainya sebagai 'incomplete URL sanitization' (padahal
    ini bukan sanitasi URL, cuma pindai konten). Marker `__cf_chl` /
    `_cf_chl_opt` sudah unik untuk halaman challenge, jadi lebih presisi.
    """
    low = text[:4000].lower()
    return (
        "just a moment" in low
        or "_cf_chl_opt" in low
        or "__cf_chl" in low
        or "cf-browser-verification" in low
        or "enable javascript and cookies to continue" in low
    )


def _fetch_via_wayback(url: str) -> str | None:
    """Ambil isi halaman dari snapshot terbaru archive.org (bypass Cloudflare).

    Cloudflare tidak melindungi archive.org, jadi snapshot yang sudah tersimpan
    bisa dibaca bebas dari Termux. Alur:
      1) CDX API → cari timestamp snapshot 200 TERBARU untuk URL itu.
      2) Ambil versi mentah `<ts>id_/<url>` (id_ = original bytes, tanpa
         toolbar archive). archive.org menyajikan byte asli yang mungkin masih
         ter-gzip → dekompres manual bila perlu.
      3) Bersihkan HTML → teks. Kembalikan None kalau tidak ada snapshot.
    Ini fallback zero-cost (tanpa browser/proxy berbayar) untuk situs ber-CF.
    """
    import gzip

    # archive.org kerap lambat / rate-limited (429). Beri timeout lebih lega
    # dari WEB_TIMEOUT biasa karena ini fallback terakhir; lebih baik nunggu
    # sebentar daripada gagal total di situs ber-Cloudflare.
    wayback_timeout = 25
    try:
        cdx = requests.get(
            "https://web.archive.org/cdx/search/cdx",
            params={
                "url": url,
                "output": "json",
                "limit": "-3",  # 3 snapshot terbaru
                "filter": "statuscode:200",
                "fl": "timestamp,original",
            },
            headers={"User-Agent": _UA},
            timeout=wayback_timeout,
        )
        if not cdx.ok:
            return None
        rows = cdx.json()
        # rows[0] = header ['timestamp','original']; sisanya data.
        if not isinstance(rows, list) or len(rows) < 2:
            return None
        timestamp = str(rows[-1][0])  # snapshot paling baru
    except (requests.RequestException, ValueError, IndexError, KeyError):
        return None

    try:
        snap = requests.get(
            f"https://web.archive.org/web/{timestamp}id_/{url}",
            headers={"User-Agent": _UA, "Accept-Encoding": "gzip, deflate"},
            timeout=wayback_timeout,
        )
        if not snap.ok:
            return None
        raw = snap.content
        # archive.org id_ kadang mengembalikan byte asli yang masih ter-gzip
        # tanpa header Content-Encoding → requests tidak auto-dekompres. Coba
        # gunzip manual bila terdeteksi magic byte gzip (0x1f 0x8b).
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        text = _html_to_text(raw)
        if not text or _looks_like_cf_challenge(text):
            return None
        note = f"[via arsip web {timestamp[:8]} — situs asli diblokir Cloudflare]\n\n"
        return note + text[:12_000] + ("\n... [truncated]" if len(text) > 12_000 else "")
    except requests.RequestException:
        return None


def _looks_like_geo_block(response: Any, text: str = "") -> bool:
    final_url = str(getattr(response, "url", "") or "")
    if re.search(r"/block/[A-Za-z]{2}\.html(?:$|[?#])", final_url, re.IGNORECASE):
        return True
    lowered = (text or "").lower()
    return "geo-block" in lowered or "not available in your country" in lowered


def _fetch_with_network_routes(url: str) -> str | None:
    """Try owner-configured routes without changing process-wide networking."""
    for route in network_routes.enabled_routes():
        label = str(route.get("label", "route"))
        country = str(route.get("country", "")) or "unknown"
        try:
            response = requests.get(
                url,
                headers={"User-Agent": _UA},
                proxies=network_routes.proxies(str(route["proxy_url"])),
                timeout=WEB_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(8192):
                chunks.append(chunk)
                size += len(chunk)
                if size > WEB_MAX_BYTES:
                    break
            text = _html_to_text(b"".join(chunks))
            if _looks_like_geo_block(response, text):
                continue
            if response.ok and text and not _looks_like_cf_challenge(text):
                prefix = f"[via network route {label} · country={country}]\n\n"
                return prefix + text[:12_000] + ("\n... [truncated]" if size > 12_000 else "")
            if response.ok and _looks_like_cf_challenge(text):
                return (
                    f"ERROR [CLOUDFLARE_CHALLENGE route={label} country={country} url={url}]: "
                    "geo route succeeded but a CAPTCHA challenge remains. Keep this route/session "
                    "and continue with captcha-solving-2captcha."
                )
        except requests.RequestException:
            continue
    return None


def _web_fetch(url: str, use_private_routes: bool = False) -> str:
    """Open a public URL, optionally using owner-only per-request routes."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "ERROR: URL must be a valid http/https URL."
    host = parsed.hostname or ""
    if not host or _is_internal_ip(host):
        return "ERROR: URL points to an internal address and is blocked."
    # 1) Reader proxy: mengembalikan teks/markdown bersih, jarang kena blokir.
    try:
        response = requests.get(
            _JINA_READER + url,
            headers={"User-Agent": _UA},
            timeout=WEB_TIMEOUT,
        )
        if response.ok and response.text.strip() and not _looks_like_cf_challenge(response.text):
            text = response.text
            if use_private_routes and _looks_like_geo_block(response, text):
                routed = _fetch_with_network_routes(url)
                if routed:
                    return routed
            if not _looks_like_geo_block(response, text):
                return text[:12_000] + ("\n... [truncated]" if len(text) > 12_000 else "")
    except requests.RequestException:
        pass
    # 2) Fallback: fetch langsung.
    try:
        response = requests.get(
            url,
            headers={"User-Agent": _UA},
            timeout=WEB_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        if response.ok:
            chunks = []
            size = 0
            for chunk in response.iter_content(8192):
                chunks.append(chunk)
                size += len(chunk)
                if size > WEB_MAX_BYTES:
                    break
            body = b"".join(chunks)
            text = _html_to_text(body)
            # Geo redirects need a non-ID route; Cloudflare challenges need the
            # solver workflow. Neither should be returned as page content.
            if use_private_routes and _looks_like_geo_block(response, text):
                routed = _fetch_with_network_routes(url)
                if routed:
                    return routed
            if text and not _looks_like_cf_challenge(text) and not _looks_like_geo_block(response, text):
                return text[:12_000] + ("\n... [truncated]" if size > 12_000 else "")
    except requests.RequestException:
        pass
    # 3) Owner-only per-request routes. Telegram/provider/localhost stay direct.
    if use_private_routes:
        routed = _fetch_with_network_routes(url)
        if routed:
            return routed
    # 4) Fallback terakhir: snapshot archive.org (bypass Cloudflare, zero-cost).
    archived = _fetch_via_wayback(url)
    if archived:
        return archived
    return (
        f"ERROR [CLOUDFLARE_CHALLENGE url={url}]: halaman publik tidak bisa "
        "dibaca lewat fetch/arsip karena challenge Cloudflare. Runtime harus "
        "mengaktifkan skill captcha-solving-2captcha dan melanjutkan lewat "
        "solver bila aksi dasarnya berada dalam scope yang diizinkan. Jika ini "
        "geo-block dan belum ada route, tambahkan owner proxy lewat network_route."
    )


def _search_result_urls(query: str, limit: int = 4) -> list[str]:
    """Kumpulkan URL hasil (untuk deep_research) dari sumber yang andal.
    Hanya URL yang benar-benar bisa di-fetch (bukan redirect Google News)."""
    urls: list[str] = []
    seen: set[str] = set()
    for engine in (_search_jina_ddg, _search_wikipedia):
        for _title, url in engine(query):
            if not url or not url.startswith("http"):
                continue
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if not host or _is_internal_ip(host):
                continue
            # Lewati proxy & redirect yang tidak bisa dibaca langsung.
            if any(bad in host for bad in ("jina.ai", "duckduckgo.com", "news.google.com")):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= limit:
                return urls
        if urls:
            break
    return urls


def _deep_research(query: str) -> str:
    """Riset multi-sumber: cari URL teratas, baca 2-3 sumber PARALEL via reader
    proxy, lalu kumpulkan kutipan untuk disintesis. Dibatasi ketat agar cepat."""
    query = query.strip()
    if not query:
        return "ERROR: empty query."
    urls = _search_result_urls(query, limit=3)
    if not urls:
        # Tidak dapat URL → pakai hasil web_search ringkas saja (cepat).
        return _web_search(query)

    import concurrent.futures

    bodies: dict[str, str] = {}
    # Batas total waktu keras agar tidak pernah menggantung lama.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_web_fetch, url): url for url in urls}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=14):
                url = futures[future]
                try:
                    bodies[url] = future.result()
                except Exception:
                    bodies[url] = "ERROR"
        except concurrent.futures.TimeoutError:
            pass  # ambil yang sudah selesai; sisanya dilewati

    sections: list[str] = [f"Riset untuk: {query}", ""]
    read = 0
    for url in urls:
        body = bodies.get(url, "")
        if not body or body.startswith("ERROR") or body.startswith("(halaman"):
            continue
        sections.append(f"### Sumber: {url}\n{body[:1_800].strip()}")
        sections.append("")
        read += 1
    if read == 0:
        return _web_search(query)
    sections.append(
        "Instruksi: sintesis poin-poin di atas menjadi jawaban ringkas & "
        "berbukti. Sebutkan sumber (URL) untuk klaim penting. Jangan mengarang "
        "fakta yang tidak ada di sumber. Jangan panggil tool lagi bila cukup."
    )
    return "\n".join(sections)[:14_000]


TOOL_DEFS: list[ToolDef] = [
    ToolDef(
        "runtime_info",
        "Show Zeline runtime identity, model, provider, protocol, profile, and tools without leaking the API key or token.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "add_memory",
        "Save one long-term fact about the user in this conversation's memory.",
        {
            "type": "object",
            "properties": {"fact": {"type": "string", "description": "Short fact to remember"}},
            "required": ["fact"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "remove_memory",
        "Remove a fact in this conversation's memory containing a given substring.",
        {
            "type": "object",
            "properties": {"substring": {"type": "string", "description": "Substring of the fact to remove"}},
            "required": ["substring"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "list_memory",
        "Show all facts stored for this user/conversation.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "load_skill",
        "Read the full content of a skill/procedure by its skill file name.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name without .md"}},
            "required": ["name"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "web_search",
        "Search the web for current information (news, articles, public data). Use when the user asks for info you don't know or that needs fresh data.",
        {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search keywords"}},
            "required": ["query"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "web_fetch",
        "Open one public URL and return its page text. On the owner/full profile, automatically try configured private network routes when direct access is geo-blocked.",
        {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full URL, e.g. https://example.com/article"}},
            "required": ["url"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "network_route",
        "Owner-only proxy route manager for geo-blocked public websites. List, add, remove, or health-test HTTP/HTTPS/SOCKS5 routes. Credentials are stored privately and never shown back.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "add", "remove", "test"]},
                "label": {"type": "string", "description": "Short route label"},
                "proxy_url": {"type": "string", "description": "http(s)://user:pass@host:port or socks5h://user:pass@host:port"},
                "country": {"type": "string", "description": "Expected 2-letter exit country"},
            },
            "required": ["action"],
        },
        frozenset({"full"}),
    ),
    ToolDef(
        "deep_research",
        "In-depth multi-source research: search the web, open the top 3 pages, and gather evidence-backed quotes to synthesize. Use when the user asks for research, comparison, or an answer needing several sources — not just one quick fact.",
        {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Research topic or question"}},
            "required": ["query"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "analyze_media",
        "Look at an image (PNG/JPG/WEBP/GIF) and answer a question about it using the vision model. Accepts a workspace file path OR an http/https URL. Use when the user sends/points to an image (screenshot, photo, diagram). For audio/video, this tool explains the correct step (transcript/frame extraction).",
        {
            "type": "object",
            "properties": {
                "path_or_url": {"type": "string", "description": "Image file path in the workspace or an http/https URL"},
                "question": {"type": "string", "description": "Question/instruction about the image (optional)"},
            },
            "required": ["path_or_url"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "generate_image",
        "Generate an image from a text prompt (text-to-image) and save it into the workspace as a PNG/JPG/WEBP. Use when the user asks to create/draw/render a picture, illustration, logo, or artwork. Requires the owner to have configured an image model. Returns the saved file path.",
        {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed description of the image to create"},
                "path": {"type": "string", "description": "Output file path in the workspace, ending in .png/.jpg/.webp"},
                "size": {"type": "string", "description": "Image size like 1024x1024, 1536x1024, or 1024x1536. Optional (default 1024x1024)."},
            },
            "required": ["prompt", "path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "http_request",
        "Call a REST API/webhook with any method (GET/POST/PUT/PATCH/DELETE), headers, and a JSON body. Unlike web_fetch which only reads GET pages. Internal network addresses are blocked automatically.",
        {
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "GET, POST, PUT, PATCH, DELETE"},
                "url": {"type": "string", "description": "http/https endpoint URL"},
                "headers": {"type": "string", "description": "Headers as JSON, e.g. {\"Authorization\": \"Bearer x\"}. Optional."},
                "body": {"type": "string", "description": "Request body (JSON/text). Optional."},
            },
            "required": ["method", "url"],
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "system_env",
        "Show environment info: OS/arch/CPU, installed runtimes & tools (python/node/go/git/docker/ffmpeg), and active local ports. Call before running commands to see which tools are available.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "read_file",
        "Read a text file inside the allowed workspace.",
        {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path in the workspace"}},
            "required": ["path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "write_file",
        "Write/overwrite a text file inside the allowed workspace.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path in the workspace"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "edit_file",
        "Edit one unique section of a text file in the workspace.",
        {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "patch_file",
        "Apply a unique replace patch to one workspace file.",
        {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "search_files",
        "Search text within workspace files.",
        {"type": "object", "properties": {"query": {"type": "string"}, "pattern": {"type": "string", "description": "File glob, default *"}}, "required": ["query"]},
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "download_file",
        "Download a file from a public URL (http/https) into the workspace. For assets/releases/datasets. Internal addresses blocked; 50 MB limit.",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL of the file to download"},
                "path": {"type": "string", "description": "Destination relative path in the workspace"},
            },
            "required": ["url", "path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "update_task",
        "Report a status change for one coding task. Call when a task starts, finishes, is cancelled, or is replaced.",
        {"type": "object", "properties": {"task": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]}}, "required": ["task", "status"]},
        frozenset({"full"}),
    ),
    ToolDef(
        "save_skill",
        "Save a new skill owned by the Zeline operator. Only use when the local user asks for a reusable procedure.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name"},
                "content": {"type": "string", "description": "Skill markdown (# title, > description, steps)"},
            },
            "required": ["name", "content"],
        },
        frozenset({"full"}),
    ),
    ToolDef(
        "update_skill",
        "Patch one unique section of the operator's private skill.",
        {"type": "object", "properties": {"name": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["name", "old_text", "new_text"]},
        frozenset({"full"}),
    ),
    ToolDef(
        "execute_code",
        "Run a Python snippet in the operator workspace and return the real output. Raise 'timeout' for slow work (heavy computation, large downloads) instead of letting it fail at the 60s default.",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds to wait before giving up. Default 60, maximum 900. Returns as soon as the code finishes, so a high value costs nothing."},
            },
            "required": ["code"],
        },
        frozenset({"full"}),
    ),
    ToolDef(
        "run_shell",
        "Run a shell command in the owner workspace. Only for the authorized local operator. For genuinely slow commands (pip/npm/apt install, builds, tests) pass a larger 'timeout' — do NOT report failure just because the 60s default was hit. For servers/watchers/very long builds pass background=true and poll with process_control.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command"},
                "timeout": {"type": "integer", "description": "Seconds to wait before giving up. Default 60, maximum 900. Returns as soon as the command finishes, so setting 600 for an install costs nothing when it takes 20s."},
                "background": {"type": "boolean", "description": "Start the command detached and return a job id immediately instead of waiting. Use for servers, watchers, daemons, or builds longer than the foreground maximum."},
            },
            "required": ["command"],
        },
        frozenset({"full"}),
    ),
    ToolDef(
        "process_control",
        "Inspect or stop background processes started by run_shell(background=true). Actions: 'list' (all jobs + status), 'poll' (status + output written since the last poll), 'log' (tail the full log), 'kill' (terminate the process group).",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "poll", "log", "kill"], "description": "What to do."},
                "job_id": {"type": "string", "description": "Job id returned by run_shell(background=true). Required for poll/log/kill."},
                "lines": {"type": "integer", "description": "For action='log': how many trailing lines to return (default 200, max 2000)."},
            },
            "required": ["action"],
        },
        frozenset({"full"}),
    ),
    ToolDef(
        "delegate_task",
        "Delegate a focused subtask to a sub-agent that runs in its own isolated context and returns only a concise final summary — keeping this conversation's context clean. Use for self-contained work (research a topic, review/refactor a file, debug an error) where you don't need every intermediate step. The sub-agent knows NOTHING about this chat, so put ALL needed info (paths, constraints, error text, desired output language) in 'context'. It inherits the same tools/workspace under the same profile but cannot delegate further.",
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "What the sub-agent should accomplish (specific, self-contained)."},
                "context": {"type": "string", "description": "All background the sub-agent needs: file paths, error messages, constraints, output language. Optional but recommended."},
            },
            "required": ["goal"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "recall_history",
        "Search THIS chat's own past conversation transcript (permanent archive across /new resets) for what was actually said/done before. Use this FIRST whenever the user refers to the past — 'lanjutin yang tadi', 'file tadi', 'kemarin kita bahas apa', 'yang barusan', 'history X', 'terusin', or any reference to an earlier decision/task/file — instead of guessing or listing workspace files. Returns the matching past user/assistant messages with timestamps. Leave 'query' empty to get the most recent turns (good for 'what were we just doing').",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords about the earlier topic (e.g. 'xauusd analysis', 'file edit', 'ftmo pricing'). Empty = most recent turns."},
            },
        },
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "ask_user",
        (
            "Ask the operator ONE short question and wait for their answer before continuing. "
            "Use this when the request is genuinely ambiguous, when several approaches have different "
            "trade-offs the user should pick between, or before an action that is risky/hard to undo "
            "(deleting data, overwriting an important file, deploying, spending money). "
            "Supply 'options' to offer up to 6 tappable choices; omit it for a free-text answer. "
            "Do NOT use this for things you can decide yourself (naming, formatting, step order) or "
            "for a request that is already clear — asking when the intent is obvious wastes the user's "
            "time. Ask once, then act on the answer; never re-ask the same thing."
        ),
        {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question itself, one sentence. Do not list the options inside this text; pass them in 'options'.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: up to 6 distinct choices, each its own array element. Omit for a free-text answer.",
                },
            },
            "required": ["question"],
        },
        frozenset(SAFE_PROFILES),
    ),
]


class ToolExecutor:
    """Tool binding for one session/identity and one security profile."""

    def __init__(self, identity: str, profile: str = "safe", workspace: str | Path | None = None, depth: int = 0):
        if profile not in SAFE_PROFILES:
            raise ValueError(f"unknown tool profile: {profile}")
        self.identity = identity or "cli:local"
        self.profile = profile
        self.workspace = Path(workspace or config.WORKSPACE).expanduser().resolve(strict=False)
        # Kedalaman agen: 0 = agen utama. Sub-agent yang dibuat delegate_task
        # menaikkan depth; delegate_task dinonaktifkan saat depth sudah mencapai
        # batas (mencegah rekursi tak terbatas / cucu-agent).
        self.depth = int(depth)
        self.memory = memory.MemoryStore(self.identity)
        # Snapshot once per session. Tool schemas must remain stable throughout
        # a model turn for prompt caching and tool-call consistency; config
        # changes apply when a new ToolExecutor/session is created.
        disabled: set[str] = set(getattr(config, "DISABLED_TOOLS", ()))
        # Batas kedalaman sub-agent: kalau sudah di atau melewati batas,
        # delegate_task tidak boleh muncul di skema anak (leaf agent).
        max_depth = int(getattr(config, "MAX_SUBAGENT_DEPTH", getattr(config, "DEFAULT_MAX_SUBAGENT_DEPTH", 1)))
        if self.depth >= max_depth:
            disabled.add("delegate_task")
        self._disabled_tools = frozenset(disabled)
        self._native_defs = tuple(
            definition
            for definition in TOOL_DEFS
            if profile in definition.profiles and definition.name not in disabled
        )
        # Private skill hanya boleh dibaca operator local/full profile.
        self._can_read_private_skills = profile == "full"
        # MCP hanya untuk operator (workspace/full). Server stdio menjalankan
        # perintah lokal, jadi tidak boleh diekspos ke gateway publik (safe).
        self.mcp: mcp_module.MCPRegistry | None = None
        if profile in {"workspace", "full"} and getattr(config, "MCP_SERVERS", None):
            try:
                self.mcp = mcp_module.MCPRegistry.from_config({"mcp": {"servers": config.MCP_SERVERS}})
            except Exception:
                self.mcp = None
        self._handlers: dict[str, ToolFunction] = {
            "runtime_info": self._runtime_info,
            "add_memory": self.memory.add,
            "remove_memory": self.memory.remove,
            "list_memory": self.memory.formatted,
            "load_skill": lambda name: skills.load_skill(name, include_private=self._can_read_private_skills),
            "web_search": lambda query: _web_search(query),
            "web_fetch": lambda url: _web_fetch(url, use_private_routes=self.profile == "full"),
            "network_route": network_routes.tool,
            "deep_research": lambda query: _deep_research(query),
            "analyze_media": lambda path_or_url, question="": _analyze_media(path_or_url, question, self.workspace),
            "generate_image": lambda prompt, path, size="1024x1024": _generate_image(prompt, path, self.workspace, size),
            "http_request": lambda method, url, headers="", body="": _http_request(method, url, headers, body),
            "system_env": lambda: _system_env(),
            "read_file": lambda path: _read_file(path, self.workspace),
            "write_file": lambda path, content: _write_file(path, content, self.workspace),
            "edit_file": lambda path, old_text, new_text: _edit_file(path, old_text, new_text, self.workspace),
            "patch_file": lambda path, old_text, new_text: _patch_file(path, old_text, new_text, self.workspace),
            "search_files": lambda query, pattern="*": _search_files(query, self.workspace, pattern),
            "download_file": lambda url, path: _download_file(url, path, self.workspace),
            "update_task": _update_task,
            "save_skill": skills.save_skill,
            "update_skill": skills.update_skill,
            "execute_code": lambda code, timeout=None: _execute_code(code, self.workspace, timeout, self.identity),
            "run_shell": lambda command, timeout=None, background=False: _run_shell(command, self.workspace, timeout, background, self.identity),
            "process_control": lambda action, job_id="", lines=None: _process_control(action, job_id, lines),
            "delegate_task": lambda goal, context="": self._delegate_task(goal, context),
            "recall_history": lambda query="": self._recall_history(query),
            "ask_user": lambda question, options=None: interaction.ask(self.identity, question, options),
        }

    def _recall_history(self, query: str = "") -> str:
        """Cari transkrip percakapan lama chat ini (archive permanen).

        Ini yang bikin Zeline tidak amnesia lintas /new: 'lanjut file tadi' →
        cari di archive, bukan nebak file workspace. Sub-agent (identity ::sub)
        tidak punya archive sendiri, jadi aman mengembalikan kosong.
        """
        from zeline.session_store import SessionPersistence
        try:
            store = SessionPersistence()
        except Exception as exc:
            return f"ERROR: cannot open history archive: {exc}"
        q = (query or "").strip()
        rows = store.search_archive(self.identity, q) if q else store.recent_archive(self.identity)
        if not rows:
            if q:
                return f"No past conversation found matching '{q}'. This chat has no earlier transcript on that topic."
            return "No earlier conversation archived for this chat yet."
        header = (
            f"Past conversation matching '{q}' (most relevant first):"
            if q else "Most recent earlier turns in this chat (chronological):"
        )
        lines = [header, ""]
        for r in rows:
            who = "User" if r["role"] == "user" else "You"
            snippet = r["content"].replace("\n", " ").strip()
            if len(snippet) > 400:
                snippet = snippet[:400] + "…"
            lines.append(f"[{r['when']}] {who}: {snippet}")
        return "\n".join(lines)

    def _delegate_task(self, goal: str, context: str = "") -> str:
        """Jalankan subtask di sub-agent terisolasi; kembalikan ringkasan akhir.

        Sub-agent punya ToolExecutor & sesi Zeline sendiri (history kosong,
        depth+1) dengan profil/workspace yang sama, tapi TIDAK bisa memanggil
        delegate_task lagi (dibatasi MAX_SUBAGENT_DEPTH). Hanya jawaban final
        yang balik ke agen induk — langkah antara tidak mengotori konteks induk.
        """
        goal = (goal or "").strip()
        if not goal:
            return "ERROR: delegate_task needs a non-empty goal."
        # Import di dalam fungsi untuk menghindari circular import (agent → tools).
        from zeline.agent import Zeline, ZelineError as _ZErr

        brief = goal if not context.strip() else f"{goal}\n\n---\nContext you must use (you have no other memory of the parent conversation):\n{context.strip()}"
        sub_extra = (
            "\n\nYou are a SUB-AGENT spawned to complete ONE focused task and report back. "
            "You have no memory of the parent conversation beyond the brief you were given. "
            "Do the work with your tools, then reply with a concise, self-contained final "
            "summary of what you found or did (include concrete results, file paths, or key "
            "findings the caller needs). Do not ask the caller questions — decide and act."
        )
        try:
            sub = Zeline(
                identity=f"{self.identity}::sub",
                tool_profile=self.profile,
                workspace=str(self.workspace),
                system_extra=sub_extra,
                depth=self.depth + 1,
            )
            summary = sub.send(brief)
        except _ZErr as exc:
            return f"ERROR: sub-agent failed: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            return f"ERROR: sub-agent crashed ({exc.__class__.__name__}): {exc}"
        summary = (summary or "").strip()
        return f"[sub-agent result]\n{summary}" if summary else "[sub-agent returned no output]"

    def _enabled_native_defs(self) -> tuple[ToolDef, ...]:
        return self._native_defs

    def _runtime_info(self) -> str:
        available = [definition.name for definition in self._enabled_native_defs()]
        return json.dumps({
            "identity": config.NAME,
            "framework": "Zeline",
            "lab": "Zerolinear",
            "model": config.MODEL,
            "protocol": config.PROTOCOL,
            "tool_profile": self.profile,
            "tools": available,
            "secrets": "API key, token, provider base URL, and host/relay are hidden — never disclose them",
        }, ensure_ascii=False, indent=2)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        native = [definition.schema() for definition in self._enabled_native_defs()]
        if self.mcp is not None:
            try:
                native.extend(self.mcp.schemas())
            except Exception:
                pass
        return native

    def run(self, name: str, args: dict[str, Any]) -> str:
        # Tool MCP di-dispatch ke registry (hanya untuk profile workspace/full).
        if self.mcp is not None and name.startswith(mcp_module.MCP_TOOL_PREFIX):
            if not self.mcp.has_tool(name):
                return f"ERROR: MCP tool '{name}' is not registered."
            return self.mcp.call(name, args)
        allowed = {definition.name for definition in self._enabled_native_defs()}
        if name not in allowed:
            if name in self._disabled_tools:
                return f"ERROR: tool '{name}' is disabled by the owner."
            return f"ERROR: tool '{name}' is not allowed for profile '{self.profile}'."
        handler = self._handlers.get(name)
        if handler is None:
            return f"ERROR: tool '{name}' is not available."
        try:
            return str(handler(**args))
        except TypeError as exc:
            return f"ERROR argument {name}: {exc}"
        except Exception as exc:
            return f"ERROR running {name}: {exc}"


# Backward-compatible aliases for kode kecil yang mungkin sudah import ini.
TOOLS = {}
TOOL_SCHEMAS = [definition.schema() for definition in TOOL_DEFS]
