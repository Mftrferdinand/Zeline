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
import contextlib
import ipaddress
import itertools
import json
import mimetypes
import os
import re
import signal
import shutil
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
from zeline import compaction
from zeline import memory
from zeline import offload
from zeline import skills
from zeline import tasks
from zeline import transcribe
from zeline import vcs
from zeline import network_routes
from zeline import interaction
from zeline import checkpoints, custom_tools, formatters, openapi_tools
from zeline import plugins as plugin_bus
from zeline import tool_index
from zeline import mcp as mcp_module
from zeline import events as events_module
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


#: Kata yang HANYA berarti "teruskan pekerjaan terakhir" dan bukan topik.
#: Query yang seluruhnya tersusun dari kata-kata ini tidak boleh dicari sebagai
#: kata kunci — lihat ``ToolExecutor._recall_history``.
_CONTINUATION_WORDS = {
    "lanjut", "lanjutin", "lanjutkan", "lanjutan", "terusin", "teruskan", "terus",
    "gas", "gaskan", "next", "continue", "resume", "go", "proceed", "on",
    "yang", "yg", "tadi", "barusan", "kemarin", "sebelumnya", "itu", "aja", "dong",
    "oy", "woy", "p", "semua", "sisanya", "sisa", "kerjain", "kerjakan", "lagi",
    "backlog", "pending", "belum", "selesai", "please", "pls", "the", "rest",
    "what", "were", "we", "doing", "last", "again",
}

#: Umur maksimal turn TERBARU agar "lanjut" masih dianggap punya rujukan.
#: Umur maksimal turn TERBARU agar "lanjut" masih dianggap punya rujukan.
#: ``append_turn`` baru jalan SETELAH reply, jadi saat user mengetik "lanjut"
#: di sesi baru, baris terbaru di archive masih milik sesi sebelumnya. Tanpa
#: batas ini, "lanjut" pagi ini me-recall pekerjaan semalam seolah itu yang
#: sedang dikerjakan. Diperpanjang ke 24 jam: gateway restart bisa terjadi
#: kapan saja, dan user tetap berhak melanjutkan pekerjaan terakhirnya
#: selama masih dalam hari yang sama.
_CONTINUATION_STALE_AFTER = 24 * 3600

#: Budget digest ``_recall_history``: maksimal karakter per thread dan total.
#: Menjaga output recall tidak meledakkan context window walau archive besar.
_RECALL_THREAD_BUDGET = 1500
_RECALL_TOTAL_BUDGET = 6000
_TRUNC_MARK = "…(truncated)"


def _is_continuation_query(query: str) -> bool:
    """True bila query cuma bilang "lanjut" tanpa menyebut topik apa pun.

    Ambil kata-katanya; jika SEMUA kata ada di ``_CONTINUATION_WORDS``, query
    ini tidak membawa informasi topik sama sekali. Mencarinya sebagai kata kunci
    mengembalikan percakapan terlama yang paling sering menyebut "lanjut" —
    bukan yang terakhir dikerjakan. Sebaliknya "lanjut invoice" MEMBAWA topik
    ("invoice"), jadi tetap dicari sebagai kata kunci.
    """
    words = [w for w in re.findall(r"[\w]+", (query or "").lower(), flags=re.UNICODE)]
    if not words:
        return True
    return all(word in _CONTINUATION_WORDS for word in words)


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


def _read_file(path: str, workspace: Path, offset: int = 1, limit: int = 0) -> str:
    """Read a text file, optionally a line window.

    Offloaded tool payloads live outside the workspace by design, so they are
    resolved separately instead of widening the workspace sandbox.
    """
    try:
        requested = Path(path).expanduser()
        if requested.is_absolute() and (
            offload.is_offload_path(requested) or compaction.is_archive_path(requested)
        ):
            target = requested.resolve(strict=False)
        else:
            target = _resolve_workspace_path(path, workspace)
        if not target.is_file():
            return f"ERROR read file: not a file or not found: {target}"
        content = target.read_text(encoding="utf-8", errors="replace")
        if offset > 1 or limit > 0:
            lines = content.splitlines()
            start = max(offset, 1) - 1
            if start >= len(lines):
                return f"ERROR read file: offset {offset} is past the last line ({len(lines)})."
            end = start + limit if limit > 0 else len(lines)
            window = lines[start:end]
            shown = "\n".join(window)
            remaining = len(lines) - (start + len(window))
            note = f"[lines {start + 1}-{start + len(window)} of {len(lines)}"
            note += f"; {remaining} remaining, continue with offset={start + len(window) + 1}]" if remaining else "]"
            return f"{note}\n{shown}"
        if len(content) > 20_000:
            return offload.maybe_offload(content, 20_000)
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
        # Snapshot the previous bytes BEFORE they are gone. Best-effort by
        # design: a failed snapshot must never block the write it protects.
        checkpoints.snapshot(target, reason="write_file")
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
        # Snapshot only once the edit is known to be applicable, so a rejected
        # edit does not fill the store with identical copies.
        checkpoints.snapshot(target, reason="edit_file")
        target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"OK, {target} edited.{_format_note(target)}"
    except Exception as exc:
        return f"ERROR edit file: {exc}"


def _patch_file(path: str, old_text: str, new_text: str, workspace: Path) -> str:
    result = _edit_file(path, old_text, new_text, workspace)
    return result.replace("edited", "patched")


_UNDO_ACTIONS = ("list", "diff", "restore")


def _undo_file(action: str, workspace: Path, path: str = "", checkpoint_id: str = "") -> str:
    """Revert a file to the bytes it had before a write in THIS workspace.

    write_file/edit_file already snapshot the previous content, but until now
    only the operator's `zeline undo` could reach those snapshots. So an agent
    that clobbered a file it should not have had exactly one recovery move left:
    retype the old content from memory — which is how a bad edit turns into a
    fabricated "restore". The snapshots existed; the agent just could not see
    them.

    Every call passes ``workspace``, so the checkpoint store (deliberately
    global, one operator/one machine) is filtered down to the files this
    executor is already allowed to write. Without that the tool would be a
    write primitive pointing anywhere on disk by id.
    """
    verb = (action or "").strip().lower()
    if verb not in _UNDO_ACTIONS:
        return f"ERROR undo: action must be one of {', '.join(_UNDO_ACTIONS)}."
    if not checkpoints.enabled():
        return (
            "ERROR undo: checkpoints are disabled (tools.checkpoints = false), so no "
            "previous content was recorded. Nothing can be restored."
        )

    target: Path | None = None
    if path:
        try:
            target = _resolve_workspace_path(path, workspace)
        except ValueError as exc:
            return f"ERROR undo: {exc}"

    if verb == "list":
        entries = checkpoints.list_checkpoints(target, workspace=workspace)
        if not entries:
            where = f" for {target}" if target else " in this workspace"
            return (
                f"(no checkpoints{where}) — a checkpoint appears after write_file or "
                "edit_file changes a file that already existed."
            )
        lines = [f"{len(entries)} checkpoint(s), newest first:"]
        for entry in entries:
            lines.append(
                f"  {entry.get('id', '')}  {checkpoints.format_age(float(entry.get('ts', 0))):>9}  "
                f"{str(entry.get('reason', '')):<12} {entry.get('path', '')}"
            )
        lines.append("Preview with action='diff', put it back with action='restore'.")
        return "\n".join(lines)

    if not checkpoint_id:
        # Restoring "the newest" without naming it is how the wrong file gets
        # overwritten when several are in flight, so an id is required.
        return (
            f"ERROR undo: action='{verb}' needs checkpoint_id. "
            "Call action='list' first to see the ids."
        )
    if verb == "diff":
        return checkpoints.diff_preview(checkpoint_id, workspace=workspace)
    ok, message = checkpoints.restore(checkpoint_id, workspace=workspace)
    return message if ok else f"ERROR undo: {message}"


def _update_task(task: str, status: str, identity: str) -> str:
    """Record a task status on the identity's persistent board.

    Returning the board rather than an echo of the arguments is the point: the model
    reads back what is still open, so a long build does not lose its own plan when
    older turns are compacted out of the window.
    """
    try:
        board, note = tasks.update(identity, task, status)
    except ValueError as exc:
        return f"ERROR task: {exc}"
    except OSError as exc:
        return f"ERROR task: could not save the board ({exc.__class__.__name__})."
    prefix = f"NOTE: {note}\n" if note else ""
    return f"{prefix}{tasks.render(board)}"


def task_progress_summary(identity: str) -> str:
    """Ringkasan progress task board untuk ditampilkan di UI.

    Format: "📋 Updating tasks planning 6 task(s) — 2 completed, 3 remaining, 1 in progress"
    Dipakai oleh gateway untuk menampilkan progress nyata, bukan cuma "Updating tasks".
    """
    try:
        items = tasks.load(identity)
    except Exception:
        return "📋 Updating tasks"
    if not items:
        return "📋 Updating tasks (no active tasks)"
    total = len(items)
    completed = sum(1 for i in items if i["status"] == "completed")
    in_progress = sum(1 for i in items if i["status"] == "in_progress")
    pending = sum(1 for i in items if i["status"] == "pending")
    cancelled = sum(1 for i in items if i["status"] == "cancelled")
    remaining = total - completed - cancelled
    parts = [f"planning {total} task(s)"]
    if completed:
        parts.append(f"{completed} completed")
    if remaining:
        parts.append(f"{remaining} remaining")
    if in_progress:
        parts.append(f"{in_progress} in progress")
    return f"📋 Updating tasks {', '.join(parts)}"


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
    return offload.maybe_offload(text, limit)


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
            handle.close()
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


def _git(
    action: str,
    workspace: Path,
    *,
    path: str = "",
    message: str = "",
    ref: str = "",
    staged: Any = False,
    limit: Any = 10,
) -> str:
    """Structured git, so a repo-capable agent does not need a whole shell.

    Read operations plus the two writes that cannot lose work. Anything that
    rewrites or discards history is refused by name — see ``zeline.vcs``.
    """
    verb = (action or "").strip().lower()
    if verb in vcs.REFUSED:
        return (
            f"ERROR git: '{verb}' is not available here because it {vcs.REFUSED[verb]}. "
            "Allowed: " + ", ".join(vcs.ACTIONS) + ". If the operator really wants "
            f"'{verb}', run it with run_shell so it is an explicit, visible step."
        )
    if verb not in vcs.ACTIONS:
        return f"ERROR git: unknown action '{action}'. Use one of: {', '.join(vcs.ACTIONS)}."
    try:
        if verb == "status":
            return vcs.status(workspace)
        if verb == "diff":
            return vcs.diff(workspace, staged=_as_bool(staged), path=path)
        if verb == "log":
            return vcs.log(workspace, limit=_as_int(limit, 10), path=path)
        if verb == "show":
            return vcs.show(workspace, ref=ref or "HEAD")
        if verb == "branch":
            return vcs.branch(workspace)
        if verb == "add":
            return vcs.add(workspace, path=path)
        return vcs.commit(workspace, message=message)
    except vcs.GitError as exc:
        return f"ERROR git: {exc}"
    except OSError as exc:
        return f"ERROR git: {exc.__class__.__name__}: {exc}"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


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
#: Video containers. Their audio track is transcribed; the picture needs frames.
_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp"}


def _analyze_media(path_or_url: str, question: str, workspace: Path) -> str:
    """Look at an image and answer a question about it via the provider vision model.

    Menerima path file di workspace ATAU URL http/https gambar. Gambar dikirim ke
    endpoint chat/completions provider aktif sebagai konten image_url (data URI
    untuk file lokal). Audio DITRANSKRIPKAN lewat zeline.transcribe — dulu tool ini
    hanya menyarankan "pakai STT/Whisper" padahal tool itu tidak ada sama sekali,
    jadi voice note selalu berakhir jadi permintaan maaf.
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
            is_video = ext in _VIDEO_EXT
            if is_video or ext in transcribe.NATIVE_FORMATS or ext in transcribe.CONVERTIBLE_FORMATS:
                # Transcribe rather than explaining how someone else might: this
                # used to point at an "STT/Whisper tool" that did not exist.
                #
                # Video is handled here too — `.mp4`/`.webm` are in both lists —
                # because the audio track is usually the content. The reply says so
                # explicitly, so the model does not report a transcript as though it
                # had watched the picture.
                try:
                    text = transcribe.transcribe(target, prompt=question)
                except transcribe.TranscribeError as exc:
                    if is_video:
                        return (
                            f"ERROR transcribing the audio of {target.name}: {exc}\n"
                            "For the visuals, extract key frames with ffmpeg and call "
                            "analyze_media on those images."
                        )
                    return f"ERROR transcribing {target.name}: {exc}"
                header = f"Transcript of `{target.name}`"
                if is_video:
                    header += " (AUDIO TRACK ONLY — nothing here describes the picture)"
                if question:
                    header += f" (asked: {question[:120]})"
                footer = (
                    "\n\nFor what is on screen, extract key frames with ffmpeg and "
                    "call analyze_media on those images."
                    if is_video
                    else ""
                )
                return f"{header}:\n\n{text}{footer}"
            return (
                f"ERROR: extension `{ext}` is neither an image nor audio/video. "
                "Vision supports PNG/JPG/WEBP/GIF; audio is transcribed."
            )
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
        # Arti kode diambil dari tabel bersama (agent.PROVIDER_STATUS_HINTS) —
        # 403 = kuota habis, bukan kunci invalid. Yang khas-vision hanya 404
        # dan status tak terduga (biasanya model tanpa input gambar).
        from zeline.agent import PROVIDER_STATUS_HINTS

        if response.status_code == 404:
            hint = f" — the model '{config.MODEL}' was not found or does not accept image input; switch to a vision-capable model with /model."
        elif response.status_code in PROVIDER_STATUS_HINTS:
            hint = f" — {PROVIDER_STATUS_HINTS[response.status_code]}"
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


def _save_image_item(item: Any, dest: Path, workspace: Path, image_model: str, label: str = "generated image") -> str:
    """Decode a provider image item (b64_json or temporary URL) and write it into the workspace."""
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
    return f"OK, {label} saved: {rel} ({_format_size(len(raw))}) using model {image_model}"


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
        from zeline.agent import PROVIDER_STATUS_HINTS

        if response.status_code == 404:
            hint = f" — the model '{image_model}' or the images endpoint was not found on this provider."
        elif response.status_code in PROVIDER_STATUS_HINTS:
            hint = f" — {PROVIDER_STATUS_HINTS[response.status_code]}"
        else:
            hint = ""
        return f"ERROR: image provider HTTP {response.status_code}{hint}"
    try:
        item = response.json()["data"][0]
    except (KeyError, IndexError, TypeError, ValueError):
        return "ERROR: image provider returned an unexpected response."
    return _save_image_item(item, dest, workspace, image_model)


def _edit_image(
    image_path: str,
    prompt: str,
    path: str,
    workspace: Path,
    mask_path: str = "",
    size: str = "1024x1024",
) -> str:
    """Edit an existing image via the provider's OpenAI-compatible ``/images/edits`` endpoint.

    Takes a source image from the workspace plus a text instruction describing
    the change (e.g. "remove the people in the background") and writes the
    edited result into the workspace. An optional mask image (white = area to
    repaint) can steer the edit on providers that support it. Requires an
    image model that supports edits (e.g. gpt-image-1); if the configured
    ``image_model`` does not, the provider's error is surfaced honestly.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return "ERROR: need a text prompt describing the edit to make."
    image_model = getattr(config, "IMAGE_MODEL", "") or ""
    if not config.API_KEY or not config.BASE_URL:
        return "ERROR: provider is not configured for image editing."
    if not image_model:
        return (
            "ERROR: no image model is configured. The owner can set one with "
            "`zeline setup` (image model) or the ZELINE_IMAGE_MODEL environment variable, "
            "e.g. gpt-image-1 (which supports image edits)."
        )
    try:
        src = _resolve_workspace_path(image_path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if not src.is_file():
        return f"ERROR: source image not found in the workspace: {image_path}"
    if src.suffix.lower() not in _VISION_IMAGE_EXT:
        return "ERROR: source image must be a .png/.jpg/.jpeg/.webp/.gif file."
    mask_file = None
    if mask_path:
        try:
            mask_file = _resolve_workspace_path(mask_path, workspace)
        except ValueError as exc:
            return f"ERROR: {exc}"
        if not mask_file.is_file():
            return f"ERROR: mask image not found in the workspace: {mask_path}"
        if mask_file.suffix.lower() not in _VISION_IMAGE_EXT:
            return "ERROR: mask image must be a .png/.jpg/.jpeg/.webp/.gif file."
    try:
        dest = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if dest.suffix.lower() not in _VISION_IMAGE_EXT:
        return "ERROR: output path must end in .png/.jpg/.jpeg/.webp/.gif."
    size = (size or "1024x1024").strip() or "1024x1024"
    if size not in _IMAGE_SIZE_ALLOWED:
        return f"ERROR: unsupported size '{size}'. Allowed: {', '.join(sorted(_IMAGE_SIZE_ALLOWED))}."
    try:
        image_bytes = src.read_bytes()
        mask_bytes = mask_file.read_bytes() if mask_file else None
    except OSError as exc:
        return f"ERROR: could not read source image: {exc}"
    files: dict[str, tuple[str, bytes, str]] = {"image": (src.name, image_bytes, "image/png")}
    if mask_bytes is not None and mask_file is not None:
        files["mask"] = (mask_file.name, mask_bytes, "image/png")
    data = {"model": image_model, "prompt": prompt, "size": size, "n": "1"}
    try:
        response = requests.post(
            f"{config.BASE_URL}/images/edits",
            headers={"Authorization": f"Bearer {config.API_KEY}"},
            files=files,
            data=data,
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
        from zeline.agent import PROVIDER_STATUS_HINTS

        if response.status_code == 404:
            hint = (
                f" — the model '{image_model}' or the /images/edits endpoint was not found on this provider. "
                "Not every image model supports edits."
            )
        elif response.status_code in PROVIDER_STATUS_HINTS:
            hint = f" — {PROVIDER_STATUS_HINTS[response.status_code]}"
        else:
            hint = ""
        return f"ERROR: image provider HTTP {response.status_code}{hint}"
    try:
        item = response.json()["data"][0]
    except (KeyError, IndexError, TypeError, ValueError):
        return "ERROR: image provider returned an unexpected response."
    return _save_image_item(item, dest, workspace, image_model, "edited image")


_EDIT_VIDEO_ACTIONS = ("trim", "concat", "text", "audio", "speed")
_EDIT_VIDEO_TIMEOUT = 600
_EDIT_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi")
_EDIT_AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg")
_FFMPEG_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _atempo_chain(factor: float) -> str:
    """Split a speed factor into chained atempo filters (each must stay within 0.5-2.0)."""
    parts = []
    rest = factor
    while rest > 2.0:
        parts.append("atempo=2.0")
        rest /= 2.0
    while rest < 0.5:
        parts.append("atempo=0.5")
        rest /= 0.5
    parts.append(f"atempo={rest:.4f}")
    return ",".join(parts)


def _drawtext_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def _edit_video(
    action: str,
    video: str,
    path: str,
    workspace: Path,
    videos: str = "",
    start: str = "",
    duration: str = "",
    text: str = "",
    fontsize: int = 48,
    fontcolor: str = "white",
    position: str = "bottom",
    audio: str = "",
    volume: float = 1.0,
    factor: float = 1.0,
) -> str:
    """Edit video files with ffmpeg (CapCut-style operations, no GUI app needed).

    Actions:
      trim   — cut a segment (``start``/``duration`` in seconds).
      concat — join clips (``videos`` = comma-separated workspace paths).
      text   — overlay a title/caption (``text``, ``fontsize``, ``fontcolor``,
               ``position`` = top/center/bottom, optional ``start``/``duration`` timing).
      audio  — add or replace the audio track (``audio`` = workspace audio file,
               ``volume`` multiplier).
      speed  — change playback speed (``factor`` 0.25-4.0).

    All inputs must live in the workspace; output is always MP4.
    """
    action = (action or "").strip().lower()
    if action not in _EDIT_VIDEO_ACTIONS:
        return f"ERROR: unknown action '{action}'. Allowed: {', '.join(_EDIT_VIDEO_ACTIONS)}."
    if not shutil.which("ffmpeg"):
        return "ERROR: ffmpeg is not installed on this machine, video editing is unavailable."
    try:
        dest = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if dest.suffix.lower() != ".mp4":
        return "ERROR: output path must end in .mp4."

    def _resolve_video(p: str) -> Path | str:
        try:
            src = _resolve_workspace_path(p, workspace)
        except ValueError as exc:
            return f"ERROR: {exc}"
        if not src.is_file():
            return f"ERROR: video not found in the workspace: {p}"
        if src.suffix.lower() not in _EDIT_VIDEO_EXTS:
            return f"ERROR: unsupported video format '{src.suffix}'. Allowed: {', '.join(_EDIT_VIDEO_EXTS)}."
        return src

    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    tmp_list = None
    if action == "concat":
        parts = [p.strip() for p in (videos or "").split(",") if p.strip()]
        if len(parts) < 2:
            return "ERROR: concat needs at least 2 videos (comma-separated in 'videos')."
        srcs = []
        for p in parts:
            r = _resolve_video(p)
            if isinstance(r, str):
                return r
            srcs.append(r)
        tmp_list = workspace / f".concat_{os.getpid()}.txt"
        try:
            tmp_list.write_text("".join(f"file '{s}'\n" for s in srcs))
        except OSError as exc:
            return f"ERROR: could not write concat list: {exc}"
        cmd += ["-f", "concat", "-safe", "0", "-i", str(tmp_list),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)]
    else:
        r = _resolve_video(video)
        if isinstance(r, str):
            return r
        src = r
        if action == "trim":
            cmd += ["-i", str(src)]
            if (start or "").strip():
                try:
                    float(start)
                except ValueError:
                    return "ERROR: start must be a number of seconds."
                cmd += ["-ss", start.strip()]
            if (duration or "").strip():
                try:
                    d = float(duration)
                except ValueError:
                    return "ERROR: duration must be a number of seconds."
                if d <= 0:
                    return "ERROR: duration must be positive."
                cmd += ["-t", duration.strip()]
            cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)]
        elif action == "text":
            overlay = (text or "").strip()
            if not overlay:
                return "ERROR: text action needs the 'text' to overlay."
            try:
                fs = int(fontsize)
            except (TypeError, ValueError):
                return "ERROR: fontsize must be an integer."
            if not 8 <= fs <= 200:
                return "ERROR: fontsize must be between 8 and 200."
            pos = (position or "bottom").strip().lower()
            coords = {
                "top": "x=(w-text_w)/2:y=60",
                "center": "x=(w-text_w)/2:y=(h-text_h)/2",
                "bottom": "x=(w-text_w)/2:y=h-text_h-60",
            }
            if pos not in coords:
                return f"ERROR: unknown position '{position}'. Allowed: top, center, bottom."
            filt = f"drawtext={coords[pos]}:fontsize={fs}:fontcolor={fontcolor}:text='{_drawtext_escape(overlay)}'"
            if os.path.exists(_FFMPEG_FONT):
                filt = f"drawtext=fontfile={_FFMPEG_FONT}:{coords[pos]}:fontsize={fs}:fontcolor={fontcolor}:text='{_drawtext_escape(overlay)}'"
            timing = ""
            if (start or "").strip() or (duration or "").strip():
                try:
                    s = float(start or 0)
                    e = s + float(duration) if (duration or "").strip() else 1e9
                except ValueError:
                    return "ERROR: start/duration must be numbers of seconds."
                timing = f":enable='between(t,{s},{e})'"
            cmd += ["-i", str(src), "-vf", filt + timing,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)]
        elif action == "audio":
            try:
                a = _resolve_workspace_path(audio, workspace)
            except ValueError as exc:
                return f"ERROR: {exc}"
            if not a.is_file():
                return f"ERROR: audio not found in the workspace: {audio}"
            if a.suffix.lower() not in _EDIT_AUDIO_EXTS:
                return f"ERROR: unsupported audio format '{a.suffix}'. Allowed: {', '.join(_EDIT_AUDIO_EXTS)}."
            try:
                vol = float(volume)
            except (TypeError, ValueError):
                return "ERROR: volume must be a number."
            if not 0 <= vol <= 5:
                return "ERROR: volume must be between 0 and 5."
            cmd += ["-i", str(src), "-i", str(a), "-c:v", "copy",
                    "-filter:a", f"volume={vol}", "-c:a", "aac", "-shortest", str(dest)]
        elif action == "speed":
            try:
                f = float(factor)
            except (TypeError, ValueError):
                return "ERROR: factor must be a number."
            if not 0.25 <= f <= 4.0:
                return "ERROR: factor must be between 0.25 and 4.0."
            cmd += ["-i", str(src), "-vf", f"setpts=PTS/{f}", "-af", _atempo_chain(f),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_EDIT_VIDEO_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"ERROR: ffmpeg took longer than {_EDIT_VIDEO_TIMEOUT}s — video may be too large."
    except OSError as exc:
        return f"ERROR: could not run ffmpeg: {exc}"
    finally:
        if tmp_list is not None:
            try:
                tmp_list.unlink()
            except OSError:
                pass
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        tail = " ".join(err[-3:])[:400] if err else "unknown ffmpeg error"
        return f"ERROR: ffmpeg failed: {tail}"
    if not dest.is_file() or dest.stat().st_size == 0:
        return "ERROR: ffmpeg produced no output file."
    rel = dest.relative_to(workspace) if dest.is_relative_to(workspace) else dest
    return f"OK, edited video saved: {rel} ({_format_size(dest.stat().st_size)}) [action={action}]"


_TTS_MAX_CHARS = 4000


def _text_to_speech(text: str, path: str, workspace: Path, voice: str = "alloy", model: str = "tts-1") -> str:
    """Convert text to spoken audio via the provider's OpenAI-compatible ``/audio/speech`` endpoint.

    Saves an MP3 voice note into the workspace — use when the user asks the bot
    to reply with voice, read something aloud, or make an audio version of text.
    """
    text = (text or "").strip()
    if not text:
        return "ERROR: need the text to speak."
    if len(text) > _TTS_MAX_CHARS:
        return f"ERROR: text is too long ({len(text)} chars, max {_TTS_MAX_CHARS}). Split it and call again."
    if not config.API_KEY or not config.BASE_URL:
        return "ERROR: provider is not configured for text-to-speech."
    model = (model or "tts-1").strip() or "tts-1"
    voice = (voice or "alloy").strip() or "alloy"
    try:
        dest = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if dest.suffix.lower() != ".mp3":
        return "ERROR: output path must end in .mp3."
    try:
        response = requests.post(
            f"{config.BASE_URL}/audio/speech",
            headers={"Authorization": f"Bearer {config.API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "input": text, "voice": voice, "response_format": "mp3"},
            timeout=180,
        )
    except requests.RequestException as exc:
        return f"ERROR: network error contacting the speech provider ({exc.__class__.__name__}). Try again."
    if not response.ok:
        from zeline.agent import PROVIDER_STATUS_HINTS

        if response.status_code == 404:
            hint = f" — the model '{model}' or the /audio/speech endpoint was not found on this provider."
        elif response.status_code == 400:
            # The provider rejected the TTS request. The common real cause is
            # that the routed provider has no text-to-speech credentials at all
            # (9Router answers e.g. "No credentials for provider: openai" when
            # 'tts-1' is requested but no OpenAI key is configured). Surface the
            # provider's own message when present — it names the missing
            # provider, which is the fix.
            detail = ""
            try:
                body = response.json()
                message = str(((body or {}).get("error") or {}).get("message") or "").strip()
                if message:
                    detail = f" (provider said: {message[:160]})"
            except (ValueError, AttributeError):
                pass
            hint = (
                f"{detail} — text-to-speech is not available on this route. The "
                f"provider needs speech credentials (e.g. an OpenAI key for "
                f"'{model}'), or pick a provider that offers TTS. Voice replies "
                "stay off until then."
            )
        elif response.status_code in PROVIDER_STATUS_HINTS:
            hint = f" — {PROVIDER_STATUS_HINTS[response.status_code]}"
        else:
            hint = ""
        return f"ERROR: speech provider HTTP {response.status_code}{hint}"
    raw = response.content or b""
    if len(raw) < 1024:
        return "ERROR: speech provider returned suspiciously little audio data."
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    except OSError as exc:
        return f"ERROR writing audio: {exc}"
    rel = dest.relative_to(workspace) if dest.is_relative_to(workspace) else dest
    return f"OK, speech saved: {rel} ({_format_size(len(raw))}) using model {model}"


def _qr_code(text: str, path: str, workspace: Path, size: int = 10) -> str:
    """Generate a QR code image (PNG) from text — links, WiFi credentials, plain text.

    Runs fully offline. Use when the user asks for a QR code / barcode image.
    """
    try:
        import qrcode
    except ImportError:
        return "ERROR: the 'qrcode' package is not installed on this machine (pip install 'qrcode[pil]')."
    data = (text or "").strip()
    if not data:
        return "ERROR: need the text/data to encode in the QR code."
    try:
        dest = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if dest.suffix.lower() != ".png":
        return "ERROR: output path must end in .png."
    try:
        box = int(size)
    except (TypeError, ValueError):
        return "ERROR: size must be an integer."
    box = max(2, min(20, box))
    try:
        qr = qrcode.QRCode(box_size=box, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(dest))
    except Exception as exc:
        return f"ERROR generating QR code: {exc.__class__.__name__}: {exc}"
    rel = dest.relative_to(workspace) if dest.is_relative_to(workspace) else dest
    return f"OK, QR code saved: {rel}"


def _transcribe_audio(audio: str, workspace: Path, language: str = "", prompt: str = "") -> str:
    """Transcribe a voice note / audio file in the workspace to text.

    Uses the provider's ``/audio/transcriptions`` endpoint (same engine behind
    analyze_media). Use when the user sends a voice message and wants the words,
    without a full media analysis.
    """
    from zeline import transcribe as _stt

    try:
        src = _resolve_workspace_path(audio, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if not src.is_file():
        return f"ERROR: audio not found in the workspace: {audio}"
    try:
        text = _stt.transcribe(src, language=(language or "").strip(), prompt=(prompt or "").strip())
    except _stt.TranscribeError as exc:
        return f"ERROR: {exc}"
    text = (text or "").strip()
    if not text:
        return "ERROR: transcription came back empty — the audio may be silent."
    return f"OK, transcription of {src.name}:\n{text}"


_PDF_ACTIONS = ("merge", "split", "info")


def _parse_pdf_pages(spec: str, total: int) -> list[int] | str:
    """Parse '1-3,5' (1-based) into 0-based page indexes. Returns an error string on failure."""
    idx: list[int] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                lo, hi = int(a), int(b)
            except ValueError:
                return f"ERROR: bad page range '{part}'. Use like 1-3,5."
            if lo < 1 or hi > total or lo > hi:
                return f"ERROR: page range '{part}' out of bounds (document has {total} pages)."
            idx.extend(range(lo - 1, hi))
        else:
            try:
                n = int(part)
            except ValueError:
                return f"ERROR: bad page '{part}'. Use like 1-3,5."
            if n < 1 or n > total:
                return f"ERROR: page {n} out of bounds (document has {total} pages)."
            idx.append(n - 1)
    if not idx:
        return "ERROR: no pages selected. Use like 1-3,5."
    return idx


def _pdf_tool(action: str, path: str, workspace: Path, pdfs: str = "", pages: str = "") -> str:
    """Work with PDF files. Actions:

    - merge: join PDFs (``pdfs`` = comma-separated workspace paths) into one.
    - split: extract pages (``pages`` like "1-3,5") from one PDF (``pdfs`` = single path).
    - info: report page count (``pdfs`` = single path; no output file needed).
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return "ERROR: the 'pypdf' package is not installed on this machine (pip install pypdf)."
    action = (action or "").strip().lower()
    if action not in _PDF_ACTIONS:
        return f"ERROR: unknown action '{action}'. Allowed: {', '.join(_PDF_ACTIONS)}."

    def _resolve_pdf(p: str):
        try:
            src = _resolve_workspace_path(p, workspace)
        except ValueError as exc:
            return f"ERROR: {exc}"
        if not src.is_file():
            return f"ERROR: PDF not found in the workspace: {p}"
        if src.suffix.lower() != ".pdf":
            return f"ERROR: not a PDF file: {p}"
        return src

    parts = [p.strip() for p in (pdfs or "").split(",") if p.strip()]
    if not parts:
        return "ERROR: need at least one PDF path in 'pdfs'."
    srcs = []
    for p in parts:
        r = _resolve_pdf(p)
        if isinstance(r, str):
            return r
        srcs.append(r)

    if action == "info":
        try:
            reader = PdfReader(str(srcs[0]))
            n = len(reader.pages)
        except Exception as exc:
            return f"ERROR reading PDF: {exc.__class__.__name__}: {exc}"
        return f"OK, {srcs[0].name}: {n} page(s)."

    try:
        dest = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if dest.suffix.lower() != ".pdf":
        return "ERROR: output path must end in .pdf."
    try:
        writer = PdfWriter()
        if action == "merge":
            total = 0
            for src in srcs:
                reader = PdfReader(str(src))
                for page in reader.pages:
                    writer.add_page(page)
                    total += 1
            if total == 0:
                return "ERROR: the PDFs contain no pages."
        else:  # split
            if len(srcs) != 1:
                return "ERROR: split takes exactly one PDF in 'pdfs'."
            reader = PdfReader(str(srcs[0]))
            sel = _parse_pdf_pages(pages, len(reader.pages))
            if isinstance(sel, str):
                return sel
            for i in sel:
                writer.add_page(reader.pages[i])
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            writer.write(fh)
    except Exception as exc:
        return f"ERROR working with PDF: {exc.__class__.__name__}: {exc}"
    rel = dest.relative_to(workspace) if dest.is_relative_to(workspace) else dest
    return f"OK, PDF saved: {rel} [action={action}]"


_VEO_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_VEO_POLL_INTERVAL = 10
_VEO_POLL_MAX_ATTEMPTS = 30  # ~5 minutes of polling
_VEO_MAX_BYTES = 200 * 1024 * 1024
_VEO_DURATIONS = (5, 8)
_VEO_ASPECTS = ("16:9", "9:16")


def _generate_video(
    prompt: str,
    path: str,
    workspace: Path,
    duration: int = 8,
    aspect_ratio: str = "16:9",
    operation: str = "",
) -> str:
    """Generate a short video clip from a text prompt via Google's Veo API.

    The chat/text provider usually cannot render video, so this tool uses a
    separate capability: a Gemini API key (``gemini_api_key`` in config, or
    ``ZELINE_GEMINI_API_KEY``) plus a Veo video model (``video_model`` in
    config, or ``ZELINE_VIDEO_MODEL``). Generation is a long-running
    operation: the tool submits the job, polls for completion, downloads the
    MP4 and writes it into the workspace. If the job is still running when
    polling times out, the operation name is returned so a later call with
    ``operation=<name>`` can resume instead of starting over.
    """
    prompt = (prompt or "").strip()
    operation = (operation or "").strip()
    if not prompt and not operation:
        return "ERROR: need a text prompt describing the video to generate."
    api_key = getattr(config, "GEMINI_API_KEY", "") or ""
    video_model = getattr(config, "VIDEO_MODEL", "") or ""
    if not api_key:
        return (
            "ERROR: video generation is not available — no Gemini API key is configured. "
            "The owner can add one with `zeline setup` (Gemini API key for video) or the "
            "ZELINE_GEMINI_API_KEY environment variable. A Gemini API key with Veo access "
            "is required because the chat provider cannot render video itself."
        )
    if not video_model:
        return (
            "ERROR: no text-to-video model is configured. The owner can set one with "
            "`zeline setup` (video model) or the ZELINE_VIDEO_MODEL environment variable, "
            "e.g. veo-3.0-generate-001."
        )
    try:
        dest = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if dest.suffix.lower() != ".mp4":
        return "ERROR: output path must end in .mp4."
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    def _poll(op_name: str) -> dict | None:
        url = f"{_VEO_API_BASE}/{op_name}"
        for _ in range(_VEO_POLL_MAX_ATTEMPTS):
            try:
                resp = requests.get(url, headers={"x-goog-api-key": api_key}, timeout=30)
            except requests.RequestException as exc:
                return {"_error": f"network error while polling video job ({exc.__class__.__name__})"}
            if not resp.ok:
                return {"_error": f"video job poll HTTP {resp.status_code}"}
            try:
                data = resp.json()
            except ValueError:
                return {"_error": "video provider returned an unreadable poll response"}
            if data.get("done"):
                return data
            time.sleep(_VEO_POLL_INTERVAL)
        return None

    if operation:
        # Resume a previously submitted job.
        result = _poll(operation)
        op_name = operation
    else:
        try:
            duration_int = int(duration)
        except (TypeError, ValueError):
            return f"ERROR: duration must be one of {', '.join(str(d) for d in _VEO_DURATIONS)} seconds."
        if duration_int not in _VEO_DURATIONS:
            return f"ERROR: unsupported duration '{duration}'. Allowed: {', '.join(str(d) for d in _VEO_DURATIONS)}."
        aspect_ratio = (aspect_ratio or "16:9").strip()
        if aspect_ratio not in _VEO_ASPECTS:
            return f"ERROR: unsupported aspect ratio '{aspect_ratio}'. Allowed: {', '.join(_VEO_ASPECTS)}."
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"durationSeconds": duration_int, "aspectRatio": aspect_ratio},
        }
        try:
            resp = requests.post(
                f"{_VEO_API_BASE}/models/{video_model}:generateVideo",
                headers=headers,
                json=payload,
                timeout=60,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach the video provider ({exc.__class__.__name__}). Try again."
        if not resp.ok:
            hint = ""
            if resp.status_code == 404:
                hint = f" — the model '{video_model}' was not found. Check the video model name."
            elif resp.status_code in (400, 403):
                hint = " — the API key may lack Veo access or the request was rejected."
            return f"ERROR: video provider HTTP {resp.status_code}{hint}"
        try:
            op_name = resp.json().get("name", "")
        except ValueError:
            return "ERROR: video provider returned an unreadable response."
        if not op_name:
            return "ERROR: video provider did not return a job id."
        result = _poll(op_name)

    if result is None:
        return (
            f"PENDING: video job '{op_name}' is still rendering after ~5 minutes. "
            f"Call generate_video again with operation='{op_name}' (and the same path) to check it later."
        )
    if "_error" in result:
        return f"ERROR: {result['_error']}"
    try:
        video_uri = result["response"]["generatedSamples"][0]["video"]["uri"]
    except (KeyError, IndexError, TypeError):
        err = result.get("error", {})
        msg = err.get("message", "no video was produced") if isinstance(err, dict) else "no video was produced"
        return f"ERROR: video generation failed: {msg}"
    try:
        with requests.get(video_uri, headers={"x-goog-api-key": api_key}, timeout=300, stream=True) as dl:
            if not dl.ok:
                return f"ERROR: could not download generated video (HTTP {dl.status_code})."
            chunks = []
            total = 0
            for chunk in dl.iter_content(65536):
                chunks.append(chunk)
                total += len(chunk)
                if total > _VEO_MAX_BYTES:
                    return f"ERROR: generated video exceeds the {_VEO_MAX_BYTES // (1024*1024)} MB limit."
            raw = b"".join(chunks)
    except requests.RequestException as exc:
        return f"ERROR downloading generated video: {exc.__class__.__name__}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    except OSError as exc:
        return f"ERROR writing video: {exc}"
    rel = dest.relative_to(workspace) if dest.is_relative_to(workspace) else dest
    return f"OK, generated video saved: {rel} ({_format_size(len(raw))}) using model {video_model}"


def _schedule_task(
    action: str,
    identity: str,
    *,
    schedule: str = "",
    prompt: str = "",
    job_id: str = "",
    deliver: str = "",
) -> str:
    """Let the agent manage its own scheduled jobs.

    Zeline's scheduler already ran inside the gateway process, but the only way to
    reach it was `zeline cron` on a terminal. On a phone that means "remind me every
    morning" could not be set up in the conversation where it was asked for.

    Delivery defaults to the CALLING session, which is the whole point: a job created
    from a Telegram chat should report back into that chat. `local` is honoured when
    asked for explicitly, since a job whose work is a file or a commit does not need
    to say anything.
    """
    from zeline import scheduler as cron

    verb = (action or "").strip().lower()
    if verb not in {"list", "add", "remove", "pause", "resume", "run", "show"}:
        return (
            f"ERROR schedule_task: unknown action '{action}'. Use list, add, remove, "
            "pause, resume, run, or show."
        )
    if not cron.enabled():
        return (
            "ERROR schedule_task: scheduled jobs are disabled in this install "
            "(tools.cron = false). The owner must enable it before jobs can run."
        )

    def render(job) -> str:
        state = "enabled" if job.enabled else "paused"
        lines = [
            f"{job.id}  {job.parsed().describe()}  [{state}]  next {cron.describe_next_run(job)}",
            f"  target: {job.deliver}",
            f"  task: {job.prompt[:300]}",
        ]
        if job.last_status:
            stamp = f"{cron.format_time(job.last_run)} " if job.last_run else ""
            lines.append(f"  last run: {stamp}{job.last_status[:160]}")
        if job.runs or job.failures or job.skips:
            lines.append(f"  runs {job.runs}, failures {job.failures}, skipped {job.skips}")
        return "\n".join(lines)

    if verb == "list":
        jobs = cron.list_jobs()
        if not jobs:
            return (
                "No scheduled jobs. Create one with action='add', a schedule "
                "('30m', 'every 2h', '09:00') and the prompt to run."
            )
        body = "\n".join(render(job) for job in jobs)
        return (
            f"{len(jobs)} scheduled job(s):\n{body}\n\n"
            "Jobs only fire while the gateway process is running."
        )

    if verb == "add":
        if not str(prompt or "").strip():
            return (
                "ERROR schedule_task: a job needs a prompt — the full instruction to "
                "run later. Nobody will be watching, so make it self-contained."
            )
        target = (deliver or "").strip()
        if not target:
            # Report back to whoever asked for the job. A job created in a chat
            # that silently wrote to disk would look like it never ran.
            target = identity if identity.startswith("telegram:") else "local"
        try:
            job = cron.add_job(schedule, prompt, target)
        except cron.CronError as exc:
            return f"ERROR schedule_task: {exc}"
        where = (
            "results will be sent to this chat"
            if job.deliver.startswith("telegram:")
            else f"results are saved in {cron.output_dir()}"
        )
        return (
            f"Created {job.id}: {job.parsed().describe()}, first run "
            f"{cron.describe_next_run(job)} — {where}."
        )

    wanted = str(job_id or "").strip()
    if not wanted:
        return f"ERROR schedule_task: action '{verb}' needs a job_id. Use action='list' to see them."
    job = cron.find_job(wanted)
    if job is None:
        known = ", ".join(item.id for item in cron.list_jobs()) or "none"
        return f"ERROR schedule_task: no job '{wanted}'. Existing jobs: {known}."

    if verb == "show":
        return render(job)
    if verb == "remove":
        cron.remove_job(wanted)
        return f"Removed {wanted}."
    if verb in {"pause", "resume"}:
        cron.set_enabled(wanted, verb == "resume")
        refreshed = cron.find_job(wanted)
        when = cron.describe_next_run(refreshed) if refreshed else "unknown"
        if verb == "pause":
            return f"Paused {wanted}. It will not run until resumed."
        return f"Resumed {wanted}. Next run {when}."
    # run
    cron.run_now(wanted)
    return (
        f"Armed {wanted} to run on the next scheduler tick (within "
        f"{int(cron.TICK_SECONDS)}s). Its result goes to {job.deliver}."
    )


def _send_file(path: str, workspace: Path, identity: str, caption: str = "") -> str:
    """Hand a file the agent produced to the operator through the active channel.

    Zeline could already write a PNG, an XLSX, or a PDF and had no way to give it
    to the user — the model printed a filesystem path, which is unusable from a
    phone. Delivery itself lives in :mod:`zeline.delivery` so each gateway owns
    its own wire format; this wrapper only enforces the workspace sandbox.
    """
    from zeline import delivery

    try:
        target = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR send_file: {exc}"
    return delivery.send(identity, target, caption)


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
# UA khusus untuk r.jina.ai: proxy ini MEMBLOKIR UA browser (Chrome/…) dengan
# 403 tapi meloloskan UA bot ringan / curl / python-requests. Wajib beda dari
# _UA di atas, kalau tidak seluruh SERP Bing+DDG mati dan search jadi "bego".
_READER_UA = "curl/8.4.0"
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

    PENTING (bug 403): r.jina.ai kini MEMBLOKIR User-Agent browser (Chrome/…)
    dengan 403, tapi meloloskan UA kosong / curl / python-requests. Mengirim
    browser _UA di sini membuat SELURUH pencarian Bing+DDG (mesin utama untuk
    hasil web relevan) mati diam-diam — hanya menyisakan Google News + Wikipedia
    yang bego untuk kueri umum. Solusi: pakai UA bot ringan, BUKAN browser UA.
    """
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = requests.get(
                _JINA_READER + target_url,
                headers={"User-Agent": _READER_UA},
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
        return note + offload.maybe_offload(text, 12_000)
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
                return prefix + offload.maybe_offload(text, 12_000)
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
                return offload.maybe_offload(text, 12_000)
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
                return offload.maybe_offload(text, 12_000)
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
        "send_file",
        (
            "Send a file from the workspace to the user in this chat: an image, a "
            "PDF, a spreadsheet, an archive, anything you produced. Use this "
            "whenever you create a file the user should SEE — after generate_image, "
            "after edit_image, after generate_video, after edit_video, after text_to_speech, "
            "after qr_code, after pdf_tool, after building a report/invoice/chart, after exporting data. Printing "
            "the file path alone is useless to someone on a phone; the file must be "
            "delivered. Images arrive as photos, audio as a voice/audio message, "
            "everything else as a document. Optional 'caption' is one short line of "
            "context, not a summary of your whole answer."
        ),
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file in the workspace to send."},
                "caption": {"type": "string", "description": "Optional one-line caption shown with the file."},
            },
            "required": ["path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "git",
        (
            "Inspect and record work in a git repository without a shell. "
            "action='status' (branch + what changed), 'diff' (patch; staged=true for "
            "the staged version), 'log', 'show' (one commit), 'branch', 'add' (stage "
            "specific paths), 'commit' (needs a message). Use status/diff before "
            "claiming what you changed, and add specific paths rather than '.' so "
            "unrelated work is not committed. Operations that rewrite or discard "
            "history — push, pull, reset, checkout, rebase, clean, stash, tag — are "
            "refused here on purpose; ask the operator or use run_shell for those."
        ),
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "diff", "log", "show", "branch", "add", "commit"],
                },
                "path": {
                    "type": "string",
                    "description": (
                        "For 'add': the paths to stage, comma separated. For "
                        "'diff'/'log': limit output to this path."
                    ),
                },
                "message": {"type": "string", "description": "For 'commit': the commit message."},
                "ref": {"type": "string", "description": "For 'show': a commit ref. Defaults to HEAD."},
                "staged": {"type": "boolean", "description": "For 'diff': show the staged diff instead of the unstaged one."},
                "limit": {"type": "integer", "description": "For 'log': how many commits (default 10, max 100)."},
            },
            "required": ["action"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "schedule_task",
        (
            "Schedule work to run later, on a repeating schedule, without anyone "
            "present. Use when the user asks for something recurring or timed: a "
            "daily briefing, a reminder, a periodic check or poll. Actions: 'add' "
            "(needs schedule + prompt), 'list', 'show', 'pause', 'resume', 'run' "
            "(fire once now), 'remove'. The prompt runs as a fresh agent turn with "
            "no memory of this conversation, so write it self-contained. Results are "
            "sent back to this chat unless deliver='local'."
        ),
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "show", "pause", "resume", "run", "remove"],
                    "description": "What to do. 'list' first if you need job ids.",
                },
                "schedule": {
                    "type": "string",
                    "description": (
                        "For 'add': an interval ('30m', 'every 2h', '1d') or a daily "
                        "wall-clock time in the user's own timezone ('09:00'). Minimum "
                        "interval 1 minute."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "For 'add': the complete instruction to run. Nobody can answer a "
                        "question at run time, so include every detail it needs."
                    ),
                },
                "job_id": {
                    "type": "string",
                    "description": "For show/pause/resume/run/remove: the job id from 'list'.",
                },
                "deliver": {
                    "type": "string",
                    "description": (
                        "Optional. Defaults to this chat. Use 'local' to only save the "
                        "result to disk (right for jobs whose output is a file or a "
                        "commit), or 'telegram:<chat_id>' for a different chat."
                    ),
                },
            },
            "required": ["action"],
        },
        frozenset({"full"}),
    ),
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
        "consolidate_memory",
        "Tidy this conversation's long-term memory: drop duplicate and expired "
        "facts, keep the rest. Deterministic nudge (no LLM call) — safe to run "
        "periodically via cron to stop memory bloat.",
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
        (
            "See an image or HEAR audio. For an image (PNG/JPG/WEBP/GIF) it answers a "
            "question about it with the vision model; for audio or a video's soundtrack "
            "(ogg/mp3/m4a/wav/opus/mp4/webm…) it returns a transcript. Accepts a "
            "workspace file path or an http/https URL. Use it whenever the user sends a "
            "voice message: transcribe, then act on what they said. A video transcript "
            "covers the audio only — for what is on screen, extract frames with ffmpeg "
            "and analyze those images."
        ),
        {
            "type": "object",
            "properties": {
                "path_or_url": {"type": "string", "description": "Image or audio/video file path in the workspace, or an http/https image URL"},
                "question": {"type": "string", "description": "For an image: the question about it. For audio: optional spelling/vocabulary hints (names, jargon) to help the transcription."},
            },
            "required": ["path_or_url"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "generate_image",
        "Generate an image from a text prompt (text-to-image) and save it into the workspace as a PNG/JPG/WEBP. Use when the user asks to create/draw/render a picture, illustration, logo, or artwork. Requires the owner to have configured an image model. Returns the saved file path — then call send_file with that path so the user actually SEES the image instead of a filename.",
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
        "generate_video",
        "Generate a short video clip from a text prompt (text-to-video) and save it into the workspace as MP4. Use when the user asks to create/render a video, animation, or clip. Requires a Gemini API key with Veo access (the chat/text model cannot render video itself) — if it is not configured, the tool says so plainly instead of faking it. Generation takes minutes; if the job is still rendering, the tool returns an operation id you can resume with the 'operation' parameter. Returns the saved file path — then call send_file with that path so the user actually SEES the video instead of a filename.",
        {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed description of the video to create"},
                "path": {"type": "string", "description": "Output file path in the workspace, ending in .mp4"},
                "duration": {"type": "integer", "description": "Clip length in seconds: 5 or 8. Optional (default 8)."},
                "aspect_ratio": {"type": "string", "description": "16:9 or 9:16. Optional (default 16:9)."},
                "operation": {"type": "string", "description": "Resume a previously submitted job by its operation id. Optional."},
            },
            "required": ["prompt", "path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "edit_image",
        "Edit an existing image from the workspace with a text instruction (inpainting-style edit) and save the result as a new image file. Use when the user asks to change part of a picture — e.g. remove people or objects from the background, change colors, add/remove elements. Takes the source image path in the workspace plus a prompt describing the edit. Requires an image model that supports edits (e.g. gpt-image-1); the provider's error is surfaced honestly if it does not. Returns the saved file path — then call send_file with that path so the user actually SEES the edited image instead of a filename.",
        {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "Source image path in the workspace (.png/.jpg/.jpeg/.webp/.gif)"},
                "prompt": {"type": "string", "description": "Description of the edit to make, e.g. 'remove the people in the background'"},
                "path": {"type": "string", "description": "Output file path in the workspace, ending in .png/.jpg/.webp"},
                "mask": {"type": "string", "description": "Optional mask image path in the workspace (white = area to repaint). Best-effort; not all models use it."},
                "size": {"type": "string", "description": "Output size like 1024x1024, 1536x1024, or 1024x1536. Optional (default 1024x1024)."},
            },
            "required": ["image", "prompt", "path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "edit_video",
        "Edit a video file with CapCut-style operations (no phone app needed; runs ffmpeg on the server) and save the result as MP4 in the workspace. Actions: trim (cut a segment with start/duration in seconds), concat (join 2+ clips via comma-separated 'videos'), text (overlay a title/caption with font size/color/position and optional timing), audio (add or replace the soundtrack from an audio file, with volume), speed (change playback speed with 'factor' 0.25-4.0). Use when the user asks to cut, merge, caption, mute/replace audio, or speed up/slow down a video. Returns the saved file path — then call send_file with that path so the user actually SEES the video instead of a filename.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "trim, concat, text, audio, or speed"},
                "video": {"type": "string", "description": "Source video path in the workspace (not needed for concat)"},
                "videos": {"type": "string", "description": "Comma-separated video paths in the workspace, for concat"},
                "path": {"type": "string", "description": "Output file path in the workspace, ending in .mp4"},
                "start": {"type": "string", "description": "Start time in seconds (trim, text timing)"},
                "duration": {"type": "string", "description": "Duration in seconds (trim, text timing)"},
                "text": {"type": "string", "description": "Text to overlay (text action)"},
                "fontsize": {"type": "integer", "description": "Overlay font size 8-200 (default 48)"},
                "fontcolor": {"type": "string", "description": "Overlay font color name (default white)"},
                "position": {"type": "string", "description": "top, center, or bottom (default bottom)"},
                "audio": {"type": "string", "description": "Audio file path in the workspace (audio action)"},
                "volume": {"type": "number", "description": "Audio volume multiplier 0-5 (default 1.0)"},
                "factor": {"type": "number", "description": "Speed factor 0.25-4.0 (default 1.0)"},
            },
            "required": ["action", "path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "text_to_speech",
        "Convert text into spoken audio (a voice note) via the provider's /audio/speech endpoint and save it as MP3 in the workspace. Use when the user asks the bot to speak, read text aloud, or make an audio version of something. Returns the saved file path — then call send_file with that path so the user actually HEARS the audio instead of a filename.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to speak (max 4000 chars)"},
                "path": {"type": "string", "description": "Output file path in the workspace, ending in .mp3"},
                "voice": {"type": "string", "description": "Voice name, e.g. alloy, echo, fable, onyx, nova, shimmer. Optional (default alloy)."},
                "model": {"type": "string", "description": "Speech model. Optional (default tts-1)."},
            },
            "required": ["text", "path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "qr_code",
        "Generate a QR code image (PNG) from any text — a link, WiFi credentials, or plain text. Runs fully offline. Returns the saved file path — then call send_file with that path so the user actually SEES the QR code instead of a filename.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text/data to encode in the QR code"},
                "path": {"type": "string", "description": "Output file path in the workspace, ending in .png"},
                "size": {"type": "integer", "description": "Module size 2-20, bigger = larger image. Optional (default 10)."},
            },
            "required": ["text", "path"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "transcribe_audio",
        "Transcribe a voice note or audio file from the workspace into text, via the provider's /audio/transcriptions endpoint. Use when the user sends a voice message and just wants the words written out. Returns the transcript directly — no file is created.",
        {
            "type": "object",
            "properties": {
                "audio": {"type": "string", "description": "Audio file path in the workspace (.ogg/.mp3/.m4a/.wav/...)"},
                "language": {"type": "string", "description": "Optional ISO language code hint, e.g. id, en."},
                "prompt": {"type": "string", "description": "Optional hint: names or jargon likely spoken in the audio."},
            },
            "required": ["audio"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "pdf_tool",
        "Work with PDF files in the workspace: merge (join several PDFs into one), split (extract pages like '1-3,5' from one PDF), info (report page count). Returns the saved file path for merge/split — then call send_file with that path so the user actually GETS the PDF instead of a filename.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "merge, split, or info"},
                "pdfs": {"type": "string", "description": "Comma-separated PDF paths in the workspace (one for split/info, several for merge)"},
                "path": {"type": "string", "description": "Output file path in the workspace, ending in .pdf (merge/split)"},
                "pages": {"type": "string", "description": "Pages to extract for split, e.g. '1-3,5'"},
            },
            "required": ["action", "pdfs"],
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
        "browser",
        (
            "Drive a real headless browser for pages web_fetch cannot handle: "
            "JavaScript-rendered content, anything behind a login, or reachable only "
            "by clicking. Actions: open (navigate to url), text (read rendered text, "
            "optional css selector), click (css selector), type (css selector + text, "
            "set submit=true to press Enter), screenshot (saves a png to path), links "
            "(list page links), eval (run JavaScript and return the value), close (free "
            "the browser). The page stays open between calls, so open once then act."
        ),
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "open, text, click, type, screenshot, links, eval, or close",
                },
                "url": {"type": "string", "description": "For open. http/https URL."},
                "selector": {"type": "string", "description": "CSS selector for text/click/type."},
                "text": {"type": "string", "description": "For type: the text to enter."},
                "submit": {"type": "boolean", "description": "For type: press Enter afterwards."},
                "path": {"type": "string", "description": "For screenshot: workspace path for the png."},
                "script": {"type": "string", "description": "For eval: the JavaScript expression."},
            },
            "required": ["action"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "code_intel",
        (
            "Ask a real language server about the code, which grep cannot do: it "
            "knows a definition from a mention in a comment, follows symbols through "
            "imports, and type-checks. Actions: diagnostics (errors and warnings in a "
            "file), definition (where a symbol is defined), references (everywhere it "
            "is used), hover (type and docstring), symbols (outline of a file), servers "
            "(which language servers are installed). Positions use 1-based line and "
            "0-based character, matching what read_file shows."
        ),
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "diagnostics, definition, references, hover, symbols, or servers",
                },
                "path": {"type": "string", "description": "Workspace file to inspect."},
                "line": {"type": "integer", "description": "1-based line, for definition/references/hover."},
                "character": {"type": "integer", "description": "0-based column, for definition/references/hover."},
            },
            "required": ["action"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "system_env",
        "Show environment info: OS/arch/CPU, installed runtimes & tools (python/node/go/git/docker/ffmpeg), and active local ports. Call before running commands to see which tools are available.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
    ),
    ToolDef(
        "read_file",
        "Read a text file inside the allowed workspace. Use offset/limit to page "
        "through a large file or an offloaded tool result instead of re-running work.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path in the workspace"},
                "offset": {
                    "type": "integer",
                    "description": "1-based first line to read (default 1)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum lines to read; 0 or omitted reads to the end",
                },
            },
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
        "undo_file",
        (
            "Put a workspace file back to the content it had before you wrote to "
            "it. write_file and edit_file automatically snapshot the previous "
            "bytes, and this reads those snapshots. Use it the moment you realise "
            "an edit was wrong, damaged a file, or hit the wrong path — restoring "
            "the recorded bytes is exact, whereas retyping what you think the file "
            "used to contain is a guess. action='list' shows the checkpoints "
            "(newest first, with ids and ages), 'diff' previews what a restore "
            "would change, 'restore' performs it. A restore is itself snapshotted "
            "first, so it can be undone too."
        ),
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "diff", "restore"],
                    "description": "'list' first — 'diff' and 'restore' need a checkpoint_id from it.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional for 'list': only checkpoints of this workspace file.",
                },
                "checkpoint_id": {
                    "type": "string",
                    "description": "Required for 'diff' and 'restore': the id shown by 'list'.",
                },
            },
            "required": ["action"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "update_task",
        (
            "Track a multi-step plan on a persistent board. Call when a task starts, "
            "finishes, is cancelled, or is replaced — one call per task. The board is "
            "saved to disk and read back to you, so it survives context compaction "
            "and a gateway restart; re-calling with the same description updates that "
            "item instead of adding a duplicate. Returns the whole board, so use the "
            "reply to see what is still open."
        ),
        {"type": "object", "properties": {"task": {"type": "string", "description": "Short task description. Reuse the same wording to update an existing item."}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]}}, "required": ["task", "status"]},
        frozenset({"full"}),
    ),
    ToolDef(
        "manage_skill",
        "Author and maintain the operator's skills (procedural memory). action='create' writes a folder skill with SKILL.md; 'write_file' adds references/, templates/, scripts/ or assets/ files; 'patch' edits SKILL.md or any supporting file (a bundled skill is copied into private scope first, so the repair survives updates); existing files are checkpointed before patch/write/delete so reflection edits can be restored with zeline undo; 'delete' removes a private skill, passing absorbed_into=<other skill> when its content was merged there; 'list' shows every skill and its shape so you can patch a near-duplicate instead of saving a new one.",
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "patch", "write_file", "delete", "list"]},
                "name": {"type": "string", "description": "Skill name (lowercase, hyphens). Not needed for 'list'."},
                "content": {"type": "string", "description": "For 'create': skill markdown ('# Title', '> when to use', numbered steps, pitfalls). For 'write_file': the file body."},
                "old_text": {"type": "string", "description": "For 'patch': unique text to replace."},
                "new_text": {"type": "string", "description": "For 'patch': replacement text."},
                "file_path": {"type": "string", "description": "For 'write_file' (required) and 'patch' (optional, defaults to SKILL.md): path inside the skill, e.g. references/api.md."},
                "category": {"type": "string", "description": "Optional grouping for 'create', e.g. 'devops'."},
                "absorbed_into": {"type": "string", "description": "For 'delete': the skill that now carries this content, or empty when simply pruning."},
            },
            "required": ["action"],
        },
        frozenset({"full"}),
    ),
    ToolDef(
        "resolve_lesson",
        (
            "Mark a recorded tool failure as resolved with the concrete fix that "
            "worked. Use during self-reflection only after verifying a different "
            "approach succeeded. Pass the exact tool name and a distinctive literal "
            "substring from the failed args_sig shown in the reflection context; "
            "never store secrets in the fix."
        ),
        {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "The failed tool name."},
                "args_sig_contains": {
                    "type": "string",
                    "description": "Literal distinctive substring from the failed args signature, for example 'path=src/missing.py'.",
                },
                "fix": {"type": "string", "description": "Short reusable correction: what failed and what worked instead."},
            },
            "required": ["tool", "args_sig_contains", "fix"],
        },
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
        (
            "Delegate work to sub-agent(s) that run in their own isolated context and "
            "return only concise summaries — keeping this conversation's context clean. "
            "Pass 'goal' for one task, or 'tasks' (a list) to run several INDEPENDENT "
            "tasks in PARALLEL, which is much faster than calling this tool repeatedly. "
            "Give each task a 'role' to shape how it works: coder (reads code first and "
            "verifies its change runs), researcher (multi-source, attributes claims), "
            "reviewer (judges work instead of rewriting it), writer (turns material into "
            "one answer), or worker (default). Set verify=true to add a final checking "
            "pass that reports what is wrong or unproven — worth it for work you will act "
            "on. Sub-agents know NOTHING about this chat, so put ALL needed info (paths, "
            "constraints, error text, desired output language) in 'context'. They inherit "
            "the same tools/workspace under the same profile but cannot delegate further."
        ),
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "Single task: what the sub-agent should accomplish (specific, self-contained)."},
                "context": {"type": "string", "description": "All background the sub-agent needs: file paths, error messages, constraints, output language. Optional but recommended."},
                "role": {"type": "string", "description": "Single task role: worker, coder, researcher, reviewer, or writer."},
                "tasks": {
                    "type": "array",
                    "description": "Several independent tasks to run in parallel. Each item: {goal, context, role}. Use instead of 'goal'.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string"},
                            "context": {"type": "string"},
                            "role": {"type": "string"},
                        },
                        "required": ["goal"],
                    },
                },
                "verify": {"type": "boolean", "description": "Run a verifier sub-agent over the results and report what is wrong or unproven."},
            },
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
    ToolDef(
        "github_repos",
        (
            "List the operator's GitHub repositories (most recently updated first). "
            "Requires the GitHub connector: the owner links it once with "
            "`zeline connect github`."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many repos (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "github_issues",
        (
            "List issues of a GitHub repository. Pull requests are skipped. "
            "Requires the GitHub connector (`zeline connect github`)."
        ),
        {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner."},
                "repo": {"type": "string", "description": "Repository name."},
                "state": {"type": "string", "description": "'open', 'closed', or 'all' (default 'open')."},
                "limit": {"type": "integer", "description": "How many issues (default 10)."},
            },
            "required": ["owner", "repo"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "github_create_issue",
        (
            "Create a GitHub issue in a repository. Requires the GitHub connector "
            "(`zeline connect github`)."
        ),
        {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner."},
                "repo": {"type": "string", "description": "Repository name."},
                "title": {"type": "string", "description": "Issue title."},
                "body": {"type": "string", "description": "Optional issue body (Markdown)."},
            },
            "required": ["owner", "repo", "title"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "github_issue_comment",
        (
            "Post a comment on a GitHub issue (or pull request). Requires the GitHub "
            "connector (`zeline connect github`)."
        ),
        {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner."},
                "repo": {"type": "string", "description": "Repository name."},
                "number": {"type": "integer", "description": "Issue/PR number."},
                "body": {"type": "string", "description": "Comment body (Markdown)."},
            },
            "required": ["owner", "repo", "number", "body"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "github_prs",
        (
            "List pull requests of a GitHub repository. Requires the GitHub connector "
            "(`zeline connect github`)."
        ),
        {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner."},
                "repo": {"type": "string", "description": "Repository name."},
                "state": {"type": "string", "description": "'open', 'closed', or 'all' (default 'open')."},
                "limit": {"type": "integer", "description": "How many PRs (default 10)."},
            },
            "required": ["owner", "repo"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "gmail_search",
        (
            "Search the operator's Gmail. Returns one line per message: "
            "message-id | date | from | subject. Requires the Google connector "
            "(`zeline connect google`)."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query, e.g. 'from:bank subject:otp newer_than:7d'."},
                "limit": {"type": "integer", "description": "How many messages (default 10)."},
            },
            "required": ["query"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "gmail_read",
        (
            "Read one Gmail message (Subject/From/Date + first 2000 characters of "
            "the text body). Requires the Google connector (`zeline connect google`)."
        ),
        {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message id from gmail_search."},
            },
            "required": ["message_id"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "gmail_send",
        (
            "Send a plain-text email from the operator's Gmail account. Requires "
            "the Google connector (`zeline connect google`)."
        ),
        {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Plain-text body."},
            },
            "required": ["to", "subject", "body"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "google_calendar",
        (
            "List upcoming events on the operator's primary Google Calendar. "
            "Requires the Google connector (`zeline connect google`)."
        ),
        {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "ISO start bound (default: now)."},
                "time_max": {"type": "string", "description": "Optional ISO end bound."},
                "limit": {"type": "integer", "description": "How many events (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "sheets_read",
        (
            "Read a range from a Google Sheet, returned as compact TSV. Requires "
            "the Google connector (`zeline connect google`)."
        ),
        {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string", "description": "The spreadsheet id from its URL."},
                "range_name": {"type": "string", "description": "A1 notation, e.g. 'Sheet1!A1:D20'."},
            },
            "required": ["spreadsheet_id", "range_name"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "drive_list",
        (
            "List files in the operator's Google Drive (most recently modified "
            "first). Requires the Google connector (`zeline connect google`)."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional Drive search query."},
                "limit": {"type": "integer", "description": "How many files (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "whatsapp_send",
        (
            "Send a WhatsApp text message from the operator's business number. "
            "Requires the WhatsApp connector (`zeline connect whatsapp`)."
        ),
        {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient phone number (digits, may start with +)."},
                "text": {"type": "string", "description": "Message text."},
            },
            "required": ["to", "text"],
        },
        frozenset({"workspace", "full"}),
    ),
    ToolDef(
        "whatsapp_template",
        (
            "Send an approved WhatsApp message template (needed for contacting "
            "numbers outside the 24h conversation window). Requires the WhatsApp "
            "connector (`zeline connect whatsapp`)."
        ),
        {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient phone number (digits, may start with +)."},
                "template": {"type": "string", "description": "Approved template name."},
                "language": {"type": "string", "description": "Template language code (default en_US)."},
            },
            "required": ["to", "template"],
        },
        frozenset({"workspace", "full"}),
    ),
]


def _connector_tool(cid: str, method: str, **kwargs) -> str:
    """Call a connector operation; the import stays lazy so startup stays light.

    Returns a plain "ERROR: ..." string when the connector is unknown or not
    linked, so the model knows to ask the owner to run `zeline connect <id>`.
    """
    from zeline import connectors as connectors_pkg

    conn = connectors_pkg.get(cid)
    label = conn.name if conn is not None else cid
    if conn is None or not conn.is_connected():
        return f"ERROR: {label} not connected. The owner can run `zeline connect {cid}` to link it."
    try:
        return str(getattr(conn, method)(**kwargs))
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:  # never leak tracebacks to the model
        return f"ERROR: {label} {method} failed ({exc})."


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
        # Operator-supplied Python files in ~/.zeline/tools/. Same reasoning as
        # MCP stdio: arbitrary local code, so never exposed to a public gateway.
        # Construction is guarded because a broken tools directory must not stop
        # the agent from starting with its native tools.
        self.custom: custom_tools.CustomToolRegistry | None = None
        with contextlib.suppress(Exception):
            registry = custom_tools.CustomToolRegistry(profile)
            if registry.tools or registry.errors:
                self.custom = registry
        # Operator-owned OpenAPI specs describe remote HTTP operations. Keep them
        # on operator profiles because their authentication comes from local
        # configuration, and isolate loader failures exactly like custom Python.
        self.openapi: openapi_tools.OpenApiRegistry | None = None
        with contextlib.suppress(Exception):
            registry = openapi_tools.OpenApiRegistry(profile)
            if registry.tools or registry.errors:
                self.openapi = registry
        # Plugin hooks wrap every tool call, so a failure while loading them
        # must leave the agent fully functional and simply unhooked.
        self.plugins: plugin_bus.PluginBus | None = None
        with contextlib.suppress(Exception):
            bus = plugin_bus.PluginBus(profile)
            if bus.active:
                self.plugins = bus
        # Built lazily on first use so constructing an executor stays cheap, and
        # kept for the executor's lifetime so a revealed tool stays revealed.
        self._lazy_index: tool_index.LazySchemaIndex | None = None
        # Started on the first browser call, reused after that.
        self._browser_session: Any | None = None
        # Language servers, started on the first code_intel call. Initialization
        # is the expensive part (the server indexes the project), so they are
        # kept for the executor's lifetime.
        self._lsp: Any | None = None
        self._handlers: dict[str, ToolFunction] = {
            "runtime_info": self._runtime_info,
            "add_memory": self.memory.add,
            "remove_memory": self.memory.remove,
            "list_memory": self.memory.formatted,
            "consolidate_memory": lambda: self._consolidate_memory(),
            "load_skill": lambda name: skills.load_skill(name, include_private=self._can_read_private_skills),
            "web_search": lambda query: _web_search(query),
            "web_fetch": lambda url: _web_fetch(url, use_private_routes=self.profile == "full"),
            "network_route": network_routes.tool,
            "deep_research": lambda query: _deep_research(query),
            "analyze_media": lambda path_or_url, question="": _analyze_media(path_or_url, question, self.workspace),
            "generate_image": lambda prompt, path, size="1024x1024": _generate_image(prompt, path, self.workspace, size),
            "edit_image": lambda image, prompt, path, mask="", size="1024x1024": _edit_image(
                image, prompt, path, self.workspace, mask, size
            ),
            "edit_video": lambda action, path, video="", videos="", start="", duration="", text="", fontsize=48, fontcolor="white", position="bottom", audio="", volume=1.0, factor=1.0: _edit_video(
                action, video, path, self.workspace, videos, start, duration, text, fontsize, fontcolor, position, audio, volume, factor
            ),
            "text_to_speech": lambda text, path, voice="alloy", model="tts-1": _text_to_speech(
                text, path, self.workspace, voice, model
            ),
            "qr_code": lambda text, path, size=10: _qr_code(text, path, self.workspace, size),
            "transcribe_audio": lambda audio, language="", prompt="": _transcribe_audio(
                audio, self.workspace, language, prompt
            ),
            "pdf_tool": lambda action, pdfs, path="", pages="": _pdf_tool(
                action, path, self.workspace, pdfs, pages
            ),
            "generate_video": lambda prompt, path, duration=8, aspect_ratio="16:9", operation="": _generate_video(
                prompt, path, self.workspace, duration, aspect_ratio, operation
            ),
            "send_file": lambda path, caption="": _send_file(path, self.workspace, self.identity, caption),
            "git": lambda action, path="", message="", ref="", staged=False, limit=10: _git(
                action, self.workspace, path=path, message=message, ref=ref, staged=staged, limit=limit
            ),
            "schedule_task": lambda action, schedule="", prompt="", job_id="", deliver="": _schedule_task(
                action, self.identity, schedule=schedule, prompt=prompt, job_id=job_id, deliver=deliver
            ),
            "http_request": lambda method, url, headers="", body="": _http_request(method, url, headers, body),
            "system_env": lambda: _system_env(),
            "code_intel": lambda action, path="", line=0, character=0: self._code_intel(
                action, path, line, character
            ),
            "browser": lambda action, url="", selector="", text="", submit=False, path="", script="": self._browser(
                action, url, selector, text, submit, path, script
            ),
            "read_file": lambda path, offset=1, limit=0: _read_file(path, self.workspace, offset, limit),
            "write_file": lambda path, content: _write_file(path, content, self.workspace),
            "edit_file": lambda path, old_text, new_text: _edit_file(path, old_text, new_text, self.workspace),
            "patch_file": lambda path, old_text, new_text: _patch_file(path, old_text, new_text, self.workspace),
            "search_files": lambda query, pattern="*": _search_files(query, self.workspace, pattern),
            "download_file": lambda url, path: _download_file(url, path, self.workspace),
            "undo_file": lambda action, path="", checkpoint_id="": _undo_file(
                action, self.workspace, path=path, checkpoint_id=checkpoint_id
            ),
            "update_task": lambda task, status: _update_task(task, status, self.identity),
            "manage_skill": lambda action, name="", content="", old_text="", new_text="", file_path="", category="", absorbed_into="": skills.manage_skill(
                action, name, content, old_text, new_text, file_path, category, absorbed_into
            ),
            "resolve_lesson": lambda tool, args_sig_contains, fix: self._resolve_lesson(
                tool, args_sig_contains, fix
            ),
            "execute_code": lambda code, timeout=None: _execute_code(code, self.workspace, timeout, self.identity),
            "run_shell": lambda command, timeout=None, background=False: _run_shell(command, self.workspace, timeout, background, self.identity),
            "process_control": lambda action, job_id="", lines=None: _process_control(action, job_id, lines),
            "delegate_task": lambda goal="", context="", role="", tasks=None, verify=False: self._delegate_task(
                goal, context, role, tasks, verify
            ),
            "recall_history": lambda query="": self._recall_history(query),
            "ask_user": lambda question, options=None: interaction.ask(self.identity, question, options),
            "github_repos": lambda limit=10: _connector_tool("github", "list_repos", limit=limit),
            "github_issues": lambda owner, repo, state="open", limit=10: _connector_tool(
                "github", "list_issues", owner=owner, repo=repo, state=state, limit=limit
            ),
            "github_create_issue": lambda owner, repo, title, body="": _connector_tool(
                "github", "create_issue", owner=owner, repo=repo, title=title, body=body
            ),
            "github_issue_comment": lambda owner, repo, number, body: _connector_tool(
                "github", "comment_issue", owner=owner, repo=repo, number=number, body=body
            ),
            "github_prs": lambda owner, repo, state="open", limit=10: _connector_tool(
                "github", "list_prs", owner=owner, repo=repo, state=state, limit=limit
            ),
            "gmail_search": lambda query, limit=10: _connector_tool(
                "google", "gmail_search", query=query, limit=limit
            ),
            "gmail_read": lambda message_id: _connector_tool(
                "google", "gmail_read", message_id=message_id
            ),
            "gmail_send": lambda to, subject, body: _connector_tool(
                "google", "gmail_send", to=to, subject=subject, body=body
            ),
            "google_calendar": lambda time_min="", time_max="", limit=10: _connector_tool(
                "google", "calendar_list", time_min=time_min, time_max=time_max, limit=limit
            ),
            "sheets_read": lambda spreadsheet_id, range_name: _connector_tool(
                "google", "sheets_read", spreadsheet_id=spreadsheet_id, range_name=range_name
            ),
            "drive_list": lambda query="", limit=10: _connector_tool(
                "google", "drive_list", query=query, limit=limit
            ),
            "whatsapp_send": lambda to, text: _connector_tool(
                "whatsapp", "send_text", to=to, text=text
            ),
            "whatsapp_template": lambda to, template, language="en_US": _connector_tool(
                "whatsapp", "send_template", to=to, template=template, language=language
            ),
        }

    def _resolve_lesson(self, tool: str, args_sig_contains: str, fix: str) -> str:
        """Resolve one unresolved lesson with an explicit verified correction."""
        from zeline import lessons as lessons_module

        return lessons_module.resolve_lesson(
            self.identity,
            str(tool or "").strip(),
            str(args_sig_contains or "").strip(),
            str(fix or "").strip(),
        )

    def _browser(
        self,
        action: str,
        url: str = "",
        selector: str = "",
        text: str = "",
        submit: bool = False,
        path: str = "",
        script: str = "",
    ) -> str:
        """Drive a headless browser, keeping one session alive across calls.

        The session is reused because a cold start costs seconds and an agent
        normally makes several calls in a row; it is created on the first call
        rather than at construction so an executor that never browses never
        launches a browser.
        """
        from zeline import browser as browser_module

        if not browser_module.enabled():
            return "ERROR: the browser tool is disabled (tools.browser = false)."
        if self.profile not in browser_module.ALLOWED_PROFILES:
            return f"ERROR: the browser tool is not allowed for profile '{self.profile}'."

        verb = (action or "").strip().lower()
        if verb == "close":
            if self._browser_session is None:
                return "OK, no browser was open."
            self._browser_session.stop()
            self._browser_session = None
            return "OK, closed the browser."

        # Validate the request BEFORE launching anything. A malformed call should
        # say what is missing, not report that no browser is installed -- that
        # sends the model off fixing the wrong problem, and on a machine without
        # a browser it would hide the real mistake entirely.
        required = {
            "open": (url, "a url"),
            "click": (selector, "a css selector"),
            "type": (selector, "a css selector"),
            "screenshot": (path, "a path"),
            "eval": (script, "a script"),
        }
        if verb not in {"open", "text", "click", "type", "screenshot", "links", "eval"}:
            return (
                f"ERROR: unknown browser action '{action}'. Use open, text, click, "
                "type, screenshot, links, eval, or close."
            )
        if verb in required:
            value, expected = required[verb]
            if not str(value).strip():
                return f"ERROR: browser {verb} needs {expected}."

        try:
            if self._browser_session is None or not self._browser_session.running:
                # A session whose browser died is replaced rather than reused, so
                # a crashed browser does not poison every later call.
                if self._browser_session is not None:
                    self._browser_session.stop()
                self._browser_session = browser_module.BrowserSession()
            session = self._browser_session

            if verb == "open":
                return session.open(url)
            if verb == "text":
                return session.text(selector.strip() or "body")
            if verb == "click":
                return session.click(selector.strip())
            if verb == "type":
                return session.type(selector.strip(), text, bool(submit))
            if verb == "screenshot":
                return session.screenshot(path.strip(), self.workspace)
            if verb == "links":
                return session.links()
            value = session.evaluate(script)
            return json.dumps(value, ensure_ascii=False, default=str)[:8000]
        except browser_module.BrowserError as exc:
            return f"ERROR browser: {exc}"

    def _code_intel(self, action: str, path: str = "", line: int = 0, character: int = 0) -> str:
        """Answer a code question using a language server.

        Validated before any server is started, for the same reason as the
        browser tool: a malformed call must say what is missing rather than
        report that no language server is installed.
        """
        from zeline import lsp as lsp_module

        if not lsp_module.enabled():
            return "ERROR: code_intel is disabled (tools.lsp = false)."
        if self.profile not in lsp_module.ALLOWED_PROFILES:
            return f"ERROR: code_intel is not allowed for profile '{self.profile}'."

        verb = (action or "").strip().lower()
        if verb == "servers":
            found = lsp_module.available()
            lines = [
                f"  {language:<12} {Path(argv[0]).name if argv else '(not installed)'}"
                for language, argv in found.items()
            ]
            body = "Language servers on this machine:\n" + "\n".join(lines)
            if not any(found.values()):
                body += (
                    "\n\n  None installed. code_intel needs one, for example "
                    "`pip install basedpyright` for Python or `pkg install clangd` for C."
                )
            return body

        if verb not in {"diagnostics", "definition", "references", "hover", "symbols"}:
            return (
                f"ERROR: unknown code_intel action '{action}'. Use diagnostics, "
                "definition, references, hover, symbols, or servers."
            )
        if not str(path).strip():
            return f"ERROR: code_intel {verb} needs a path."
        if verb in {"definition", "references", "hover"} and int(line or 0) < 1:
            return f"ERROR: code_intel {verb} needs a 1-based line number."

        try:
            target = _resolve_workspace_path(path, self.workspace)
        except ValueError as exc:
            return f"ERROR code_intel: {exc}"
        if not target.is_file():
            return f"ERROR code_intel: not a file or not found: {target}"

        try:
            if self._lsp is None:
                self._lsp = lsp_module.LspRegistry(self.workspace)
            registry = self._lsp
            if verb == "diagnostics":
                return registry.diagnostics(target)
            if verb == "symbols":
                return registry.symbols(target)
            if verb == "definition":
                return registry.definition(target, int(line), int(character or 0))
            if verb == "references":
                return registry.references(target, int(line), int(character or 0))
            return registry.hover(target, int(line), int(character or 0))
        except lsp_module.LspError as exc:
            return f"ERROR code_intel: {exc}"

    def _consolidate_memory(self) -> str:
        """Rapikan memory jangka panjang: buang fakta duplikat & kedaluwarsa.

        Nudge deterministik murni — tidak ada LLM call, jadi aman dipanggil
        berkala via cron. Kontrak: ``MemoryStore.consolidate()`` mengembalikan
        dict dengan key ``removed_duplicates``, ``removed_expired``, ``kept``.
        """
        try:
            result = self.memory.consolidate()
        except Exception as exc:  # noqa: BLE001 — tool tidak boleh meledak
            return f"ERROR: consolidate_memory failed: {exc}"
        try:
            dup = int(result.get("removed_duplicates", 0))
            exp = int(result.get("removed_expired", 0))
            kept = int(result.get("kept", 0))
        except (AttributeError, TypeError, ValueError):
            return f"ERROR: consolidate_memory returned unexpected result: {result!r}"
        return (
            f"Consolidated memory: {dup} duplicates removed, "
            f"{exp} expired removed, {kept} kept."
        )

    def _recall_history(self, query: str = "") -> str:
        """Cari transkrip percakapan lama chat ini (archive permanen).

        Ini yang bikin Zeline tidak amnesia lintas /new: 'lanjut file tadi' →
        cari di archive, bukan nebak file workspace. Sub-agent (identity ::sub)
        tidak punya archive sendiri, jadi aman mengembalikan kosong.

        Query kontinuasi murni ("lanjut", "lanjutin", "terusin", "yang tadi")
        TIDAK dicari sebagai kata kunci. Itu bukan topik — itu rujukan ke
        pekerjaan TERAKHIR. Dicari sebagai kata kunci, ia justru mengembalikan
        topik terlama yang paling sering menyebut kata "lanjut", yang persis
        bikin bot balik ke sesi pertama. Untuk query seperti itu kita pakai
        anchor deterministik ``last_thread`` (thread terbaru, satu sesi).

        Untuk kontinuasi, thread terbaru dibatasi ``_CONTINUATION_STALE_AFTER``.
        Kalau turn terbaru pun sudah lebih tua dari itu, TIDAK ada pekerjaan
        yang wajar disebut "yang tadi" — dan menyodorkan sesi semalam sebagai
        konteks aktif jauh lebih menyesatkan daripada mengaku tidak tahu. Kita
        juga TIDAK jatuh ke ``recent_archive`` di jalur kontinuasi, karena
        fungsi itu mengabaikan batas sesi dan mengembalikan bug yang sama.
        """
        from zeline.session_store import SessionPersistence
        try:
            store = SessionPersistence()
        except Exception as exc:
            return f"ERROR: cannot open history archive: {exc}"
        q = (query or "").strip()
        continuation = not q or _is_continuation_query(q)
        if continuation:
            rows = store.last_thread(
                self.identity, stale_after=_CONTINUATION_STALE_AFTER
            )
            header = (
                "MOST RECENT thread in this chat, in order (this is what "
                "'lanjut/terusin/yang tadi' refers to — continue THIS, not an "
                "older topic):"
            )
        else:
            rows = store.search_archive(self.identity, q)
            header = f"Past conversation matching '{q}' (most relevant and most recent first):"
        if not rows:
            # Untuk query kontinuasi kita TIDAK mencari topik apa pun, jadi
            # "tidak ada yang cocok dengan 'lanjut'" akan menyesatkan. Yang
            # benar: tidak ada pekerjaan RECENT untuk dilanjutkan — dan model
            # harus BERTANYA, bukan mengarang dari sesi lama.
            if continuation:
                return (
                    "No recent work to continue in this chat. The last "
                    "archived turn is older than the continuation window, so "
                    "there is nothing that 'lanjut/terusin/yang tadi' can "
                    "safely refer to. Ask the user what they want to continue "
                    "instead of guessing from an older session."
                )
            return f"No past conversation found matching '{q}'. This chat has no earlier transcript on that topic."
        # Digest berkelompok dari FTS5 (tanpa LLM call tambahan):
        # baris dikelompokkan per thread berdasar (title, tanggal) supaya model
        # membaca konteks per topik, bukan tumpukan turn acak. Budget karakter
        # menjaga output tidak meledakkan context window.
        lines = [header, ""]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        order: list[tuple[str, str]] = []
        for r in rows:
            title = (r.get("title") or "").strip() or "(untitled)"
            date = (r.get("when") or "")[:10] or "????-??-??"
            key = (title, date)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(r)
        used_total = 0
        cut_total = False
        for title, date in order:
            if used_total >= _RECALL_TOTAL_BUDGET:
                cut_total = True
                break
            lines.append(f"### {title} — {date}")
            used_total += len(lines[-1]) + 1
            used_thread = 0
            for r in groups[(title, date)]:
                who = "User" if r["role"] == "user" else "You"
                snippet = r["content"].replace("\n", " ").strip()
                if len(snippet) > 400:
                    snippet = snippet[:400] + "…"
                line = f"[{r['when']}] {who}: {snippet}"
                if used_thread + len(line) + 1 > _RECALL_THREAD_BUDGET:
                    keep = max(0, _RECALL_THREAD_BUDGET - used_thread - len(_TRUNC_MARK))
                    line = line[:keep] + _TRUNC_MARK
                    lines.append(line)
                    used_total += len(line) + 1
                    break  # thread ini dipotong; lanjut ke thread berikut
                if used_total + len(line) + 1 > _RECALL_TOTAL_BUDGET:
                    keep = max(0, _RECALL_TOTAL_BUDGET - used_total - len(_TRUNC_MARK))
                    line = line[:keep] + _TRUNC_MARK
                    lines.append(line)
                    used_total += len(line) + 1
                    cut_total = True
                    break
                lines.append(line)
                used_total += len(line) + 1
                used_thread += len(line) + 1
            lines.append("")
            if cut_total:
                break
        if cut_total:
            lines.append(_TRUNC_MARK)
        return "\n".join(lines).rstrip("\n")

    def _spawn_subagent(self, brief: str, system_extra: str, suffix: str) -> str:
        """Run one sub-agent to completion and return its final summary.

        Each sub-agent gets a DISTINCT identity. They share nothing, but each
        constructs a MemoryStore keyed by identity, so reusing one identity
        across parallel workers would have them writing the same memory file at
        the same time.
        """
        # Import inside the function to avoid a circular import (agent → tools).
        from zeline.agent import Zeline

        sub = Zeline(
            identity=f"{self.identity}::sub{suffix}",
            tool_profile=self.profile,
            workspace=str(self.workspace),
            system_extra=system_extra,
            depth=self.depth + 1,
        )
        return sub.send(brief)

    def _delegate_task(
        self,
        goal: str = "",
        context: str = "",
        role: str = "",
        tasks: Any = None,
        verify: bool = False,
    ) -> str:
        """Run one or several sub-agents, optionally with a verifier pass.

        Each sub-agent has its own ToolExecutor and Zeline session (empty
        history, depth+1) with the same profile/workspace, but cannot call
        delegate_task again (bounded by MAX_SUBAGENT_DEPTH). Only final answers
        return to the parent — intermediate steps never pollute its context.
        """
        from zeline import delegation
        from zeline.agent import ZelineError as _ZErr

        if tasks is not None:
            parsed, error = delegation.parse_tasks(tasks)
            if error:
                return f"ERROR: {error}"
            plan = parsed
            overall_goal = goal.strip() or "; ".join(item.goal for item in plan)
        else:
            if not (goal or "").strip():
                return "ERROR: delegate_task needs a non-empty goal (or a 'tasks' list)."
            plan = [delegation.Task(goal=goal.strip(), context=context, role=role or delegation.DEFAULT_ROLE)]
            overall_goal = goal.strip()

        def spawn(task: delegation.Task, index: int) -> str:
            try:
                return self._spawn_subagent(
                    delegation.build_brief(task),
                    delegation.system_extra_for(task.clean_role),
                    "" if len(plan) == 1 else f"{index + 1}",
                )
            except _ZErr as exc:
                raise RuntimeError(str(exc)) from exc

        results = delegation.run_tasks(plan, spawn=spawn)

        if not verify:
            return delegation.render(results)

        # Verification improves an answer; it must never be able to destroy one.
        # If it cannot run, the work is returned labelled as unchecked.
        if not any(item.ok for item in results):
            return delegation.render(results)
        try:
            verdict = self._spawn_subagent(
                delegation.verification_material(results, overall_goal),
                delegation.system_extra_for("reviewer", verifier=True),
                "-verify",
            )
        except Exception:  # noqa: BLE001 — a failed check must not lose the work
            return delegation.render(results, verified=False)
        verdict = (verdict or "").strip()
        if not verdict:
            return delegation.render(results, verified=False)
        return delegation.render(results, verification=verdict)

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
    def all_schemas(self) -> list[dict[str, Any]]:
        """Every schema this executor could offer, before any lazy filtering."""
        native = [definition.schema() for definition in self._enabled_native_defs()]
        if self.mcp is not None:
            try:
                native.extend(self.mcp.schemas())
            except Exception:
                pass
        if self.custom is not None:
            # A schema failure must not blank the native tool list with it.
            with contextlib.suppress(Exception):
                native.extend(self.custom.schemas())
        if self.openapi is not None:
            with contextlib.suppress(Exception):
                native.extend(self.openapi.schemas())
        return native

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """What is actually sent to the provider this round.

        With tool_search off this is every schema. With it on, a core set plus
        anything already revealed, plus tool_search carrying the catalogue of
        the rest. The index is rebuilt from all_schemas each time so a newly
        loaded MCP or custom tool appears, while revelations persist.
        """
        index = self._index()
        if not index.applicable:
            return index.all
        return index.visible()

    def _index(self) -> tool_index.LazySchemaIndex:
        """The lazy index, refreshed from the current tool set.

        Built on demand rather than in __init__ so a tool call can never depend
        on schemas having been read first, and refreshed every time so a
        late-loading MCP or custom tool is never missing from the catalogue.
        """
        current = self.all_schemas
        if self._lazy_index is None:
            self._lazy_index = tool_index.LazySchemaIndex(current)
        else:
            self._lazy_index.all = current
        return self._lazy_index

    def run(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool, wrapped in the operator's plugin hooks if any.

        The hooks are deliberately outside _dispatch so that every kind of tool
        -- native, MCP and custom -- passes through the same governance point.
        The same point records an audit event for every MUTATING call, so a side
        effect is on record even if the turn later fails (session history is only
        saved after a successful turn; the audit row is written at tool time).
        """
        if self.plugins is None:
            result = self._dispatch(name, args)
            self._audit(name, args, result)
            return result
        outcome = self.plugins.before(name, args)
        if outcome.blocked:
            return plugin_bus.denial_message(name, outcome)
        result = self._dispatch(name, outcome.args)
        # Redaction/rewriting hooks must run before audit and lessons capture;
        # otherwise a plugin can hide a secret from the model while the raw
        # result is still persisted in the audit/learning stores.
        result = self.plugins.after(name, outcome.args, result)
        self._audit(name, outcome.args, result)
        return result

    def _audit(self, name: str, args: dict[str, Any], result: str) -> None:
        """Record a mutating tool call to the append-only event log.

        Also auto-captures failures into the lessons store so the agent
        learns from its mistakes without needing the model to elect to save.
        Auto-resolves prior failures when the same tool succeeds on retry,
        closing the learning loop.

        Best-effort and swallowed: an audit failure must never turn a successful
        tool call into a failed one. Read-only tools are skipped inside
        ``log_tool_call`` so this stays a side-effect index, not an activity log.
        """
        try:
            events_module.log_tool_call(self.identity, name, args if isinstance(args, dict) else {}, result)
        except Exception:
            pass
        # Auto-capture tool failures for the lessons store. Unlike the audit
        # trail (which only logs mutating tools), lessons capture ALL errors —
        # a read_file failure teaches "this path doesn't exist" too.
        # Auto-resolve prior failures when the same tool succeeds on retry,
        # closing the learning loop: failure → unresolved → success → resolved
        # → prompt_block injects the correction into the next session.
        try:
            from zeline import lessons as lessons_module
            safe_args = args if isinstance(args, dict) else {}
            if str(result).startswith("ERROR"):
                lessons_module.log_failure(self.identity, name, safe_args, result)
            else:
                lessons_module.log_success(self.identity, name, safe_args, result)
        except Exception:
            pass

    def _dispatch(self, name: str, args: dict[str, Any]) -> str:
        # tool_search is a discovery tool, not a capability: it only exists while
        # schemas are being withheld, and it hands them over.
        if name == tool_index.TOOL_NAME:
            index = self._index()
            if not index.applicable:
                return (
                    f"ERROR: '{name}' is not needed — every tool schema is already "
                    "loaded, so call the tool you want directly."
                )
            return index.search(str(args.get("query", "")))
        # A hidden tool called directly still runs, and stays visible afterwards.
        # Without this the model could see a name in the catalogue and have no way
        # to use it without a lookup round trip it does not need.
        if self._lazy_index is not None and self._lazy_index.knows(name):
            self._lazy_index.reveal(name)
        # Custom tools are checked first, but only ever match the custom_ prefix,
        # so a file can never shadow a native tool.
        if name.startswith(custom_tools.TOOL_PREFIX):
            if self.custom is None or not self.custom.has_tool(name):
                return f"ERROR: custom tool '{name}' is not registered."
            return self.custom.call(name, args)
        if name.startswith(openapi_tools.TOOL_PREFIX):
            if self.openapi is None or not self.openapi.has_tool(name):
                return f"ERROR: OpenAPI tool '{name}' is not registered."
            return self.openapi.call(name, args)
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
