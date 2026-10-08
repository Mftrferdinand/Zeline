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
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from zeline import config
from zeline import compaction
from zeline import goals
from zeline import injection_filter
from zeline import memory
from zeline import offload
from zeline import skills
from zeline import tasks
from zeline import transcribe
from zeline import vcs
from zeline import network_routes
from zeline import interaction
from zeline import approvals
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
    risk: str

    def __post_init__(self) -> None:
        if self.risk not in TOOL_RISKS:
            raise ValueError(f"unknown tool risk {self.risk!r} for tool {self.name!r}")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRisk:
    """Risk classes for native tools.

    Every native tool carries exactly one class, chosen for its most
    privileged capability ("when in doubt, stricter"): a tool that *can*
    delete is Destructive even if it usually only reads.

    Risk is judged by *effect*, not by channel: a GET over the network has
    the same effect as reading a local file (none), while a POST that sends
    a message has an effect no local read can produce. That is why the
    network classes split on mutation, not on "uses the network":

    - ``READ``: no side effects. A pure read-only network fetch (web search,
      page fetch, a transcription/vision inference call against the
      already-trusted provider) is a READ — the effect is a read, even
      though bytes cross the network. Never asks.
    - ``WRITE``: mutates local state (files, memory, task board, skills).
      Asks only when the write escapes the session workspace. A download
      (GET + workspace write) is a WRITE: the network leg is a read, the
      effect is a local file.
    - ``NETWORK``: a network call that *mutates* — it sends data out to
      change state somewhere else (sending a message/email, uploading a
      file, posting a comment, calling a state-changing API). Always asks,
      because the channel crossing is the point of no return: the operator
      cannot undo a send.
    - ``INSTALL``: installs something persistent — software via shell aside,
      here: scheduled jobs, proxy routes, agent skills. Always asks.
    - ``DESTRUCTIVE``: can irreversibly destroy data (arbitrary code/shell
      trivially can), or performs network mutations too broad to scope
      (raw HTTP with any method incl. DELETE, full browser control).
      Always asks. Scoped, single-purpose sends (one email, one comment,
      one issue) are ``NETWORK`` instead — still asking, but labelled by
      their actual effect.

    Non-native tools (MCP ``mcp__*``, custom ``custom_*``, OpenAPI ``api_*``)
    are not classified per tool: anything not explicitly trusted by the
    operator in the config file defaults to ``DESTRUCTIVE`` (fail closed —
    a tool whose effects we cannot audit must never run silently). A per-MCP-
    server ``trust.risk_cap`` in the config file can lower that default, and
    only from the config file, never from chat.

    Approval is enforced by the agent loop through the ``ask_user`` picker:
    Install/Destructive/Network calls, and Write calls that escape the
    workspace, do not run on the model's authority alone. The legacy
    ``SAFE_PROFILES`` are unchanged and still gate *visibility*:

    - ``safe`` (public gateways): Read tools plus scoped in-profile writes
      (memory); the odd dangerous tool exposed here (``http_request``)
      still asks first — visibility never implies trust.
    - ``workspace``: safe + Read/Write inside the operator workspace; a
      Write that escapes the workspace triggers approval.
    - ``full``: everything, including Install/Destructive/Network — each
      dangerous call asks first. Default only for the owner's local CLI.
    """

    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    INSTALL = "install"
    DESTRUCTIVE = "destructive"


#: All valid risk classes, for validation and tests.
TOOL_RISKS = frozenset(
    {ToolRisk.READ, ToolRisk.WRITE, ToolRisk.NETWORK, ToolRisk.INSTALL, ToolRisk.DESTRUCTIVE}
)


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


#: Above this many characters, a tool result is no longer passed through
#: verbatim: it is extractively compressed (deterministic, zero-token, no LLM)
#: and the full text is offloaded to disk for recovery. Mirrors the philosophy
#: of ``zeline.compaction``: keep what matters (numbers, error lines, file
#: paths, the beginning and the end), drop the rest, never reword anything.
TOOL_OUTPUT_COMPRESS_THRESHOLD = 12_000

#: Target size of the compressed summary, as a fraction of the threshold.
#: 12_000 chars in -> at most ~4_000 chars shown inline.
TOOL_OUTPUT_TARGET_RATIO = 1 / 3

#: Head/tail lines always kept as context, even when they score low.
TOOL_OUTPUT_CONTEXT_LINES = 8

#: Substrings marking a line as carrying failure information. Matched
#: case-insensitively and deliberately broad: a missed error line is worse
#: than a kept noisy one, because the summary is extractive (lines are never
#: reworded, so a false positive costs a few chars, not correctness).
_ERROR_LINE_HINTS = (
    "error", "err:", "failed", "failure", "fatal", "panic", "exception",
    "traceback", "denied", "refused", "timeout", "timed out", "invalid",
    "unable to", "cannot", "could not", "couldn't", "not found", "no such",
    "missing", "abort", "killed", "crash", "warning",
)

#: Heuristic for "this line mentions a file path or a dotted test id".
_PATH_HINT_RE = re.compile(r"[A-Za-z]:[\\/]|[\w.~$-]+/[\w.~$-]+|\.\w{1,5}\b")

#: Pytest prints assertion context as lines starting with a bare "E ".
_PYTEST_ERROR_RE = re.compile(r"^E\s")

#: Masks digits so "test 12 passed" and "test 34 passed" share one shape.
_SHAPE_DIGIT_RE = re.compile(r"\d+")


def _line_shape(line: str) -> str:
    """Structural shape of a line, digits masked. Boilerplate repeats it."""
    return _SHAPE_DIGIT_RE.sub("#", line.strip())


def _score_output_line(line: str) -> int:
    """Information score for one output line. Deterministic; higher = keep first."""
    stripped = line.strip()
    if not stripped:
        return -100  # blank lines never win a budget fight
    score = 0
    lowered = stripped.lower()
    if any(hint in lowered for hint in _ERROR_LINE_HINTS):
        score += 6
    if _PYTEST_ERROR_RE.match(stripped):
        score += 4  # pytest assertion detail ("E assert ...")
    if re.search(r"\d", stripped):
        score += 3  # counts, ports, sizes, durations, line numbers
    if _PATH_HINT_RE.search(stripped):
        score += 2  # file paths, test ids like test_x.py::test_y
    if "=" in stripped and len(stripped) < 300:
        score += 1  # key=value settings, env, asserts
    if len(stripped) > 600:
        score -= 4  # minified blobs / base64 noise
    return score


def _extractive_summary(text: str, target_chars: int) -> str:
    """Deterministic extractive compression: most informative lines, in order.

    Pure function — text in, text out, no state, no I/O, no LLM — so it stays
    correct when a tool result arrives from a background worker instead of a
    synchronous call site. Always keeps the head and tail of the output as
    context, then fills the remaining budget with the highest-scoring middle
    lines (error lines, numbers, file paths first). Lines are never reworded;
    dropped spans are marked with an explicit omission note.

    When the selection still exceeds the budget (omission markers cost chars
    too), lines are shed least-important-first: weak middle lines, then head
    lines, then tail lines — the very last line (the final result) is shed
    absolutely last.
    """
    lines = text.splitlines()
    if not lines:
        return ""
    # Boilerplate that repeats hundreds of times (progress lines, heartbeats)
    # is less informative than a line with a unique shape: penalize frequent
    # shapes so one-off error lines win budget fights against log spam.
    shape_counts: dict[str, int] = {}
    for line in lines:
        shape = _line_shape(line)
        shape_counts[shape] = shape_counts.get(shape, 0) + 1

    def _effective_score(index: int) -> int:
        base = _score_output_line(lines[index])
        repetitions = shape_counts[_line_shape(lines[index])]
        if repetitions > 20:
            base -= 4
        elif repetitions > 5:
            base -= 2
        return base

    context = TOOL_OUTPUT_CONTEXT_LINES
    head: set[int] = set(range(min(context, len(lines))))
    tail: set[int] = set(range(max(len(lines) - context, 0), len(lines)))
    keep = set(head | tail)
    scored = sorted(
        ((_effective_score(i), i) for i in range(len(lines)) if i not in keep),
        key=lambda item: (-item[0], item[1]),
    )
    budget = target_chars - sum(len(lines[i]) + 1 for i in keep)
    for _, index in scored:
        cost = len(lines[index]) + 1
        if cost > budget:
            continue
        keep.add(index)
        budget -= cost

    def _render(kept: set[int]) -> str:
        parts: list[str] = []
        previous = -1
        for index in sorted(kept):
            if index - previous > 1:
                parts.append(f"... [{index - previous - 1} lines omitted] ...")
            parts.append(lines[index])
            previous = index
        return "\n".join(parts)

    rendered = _render(keep)
    if len(rendered) > target_chars and keep:
        scores = {i: _effective_score(i) for i in keep - head - tail}
        # Shed order: weakest middle lines first, then head (line 0 survives
        # longest), then tail (the final line is shed absolutely last).
        shed_order = sorted(scores, key=lambda i: (scores[i], -i))
        shed_order += sorted(head & keep, reverse=True)
        shed_order += sorted(tail & keep)
        for index in shed_order:
            keep.discard(index)
            if len(keep) == 1:
                only = next(iter(keep))
                if len(lines[only]) > target_chars:
                    rendered = (
                        lines[only][:target_chars].rstrip()
                        + "\n... [single line cut to fit budget]"
                    )
                    keep = set()
                    break
            rendered = _render(keep)
            if len(rendered) <= target_chars:
                break
    return rendered


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
            return injection_filter.filter_tool_result(f"{note}\n{shown}")
        if len(content) > 20_000:
            return injection_filter.filter_tool_result(
                offload.maybe_offload(content, 20_000))
        return injection_filter.filter_tool_result(content)
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


def _truncate_output(text: str, limit: int = TOOL_OUTPUT_COMPRESS_THRESHOLD) -> str:
    """Bound a tool result for the context window.

    Small results pass through untouched. Large results are extractively
    compressed (see :func:`_extractive_summary`) and the full text is offloaded
    to disk, so the model gets the informative core inline and can
    ``read_file`` the rest instead of re-running the work.
    """
    text = (text or "").strip()
    if not text:
        return "(no output)"
    if len(text) <= limit:
        return text
    target_chars = max(1_000, int(limit * TOOL_OUTPUT_TARGET_RATIO))
    summary = _extractive_summary(text, target_chars)
    line_count = text.count("\n") + 1
    header = (
        f"Output too large for context ({len(text):,} chars, {line_count:,} lines). "
        "Extractive summary below — key numbers, error lines, file paths and the "
        "start/end of the output are preserved verbatim; nothing was reworded."
    )
    target = offload.store(text)
    if target is None:
        return f"{header}\n(full text could not be saved to disk)\n\n{summary}"
    return (
        f"{header} The full text was saved to:\n{target}\n\n"
        "Read the parts you need with "
        f'read_file(path="{target}", offset=1, limit=200) — offset is a 1-based '
        "line number. Do not re-run the command to see the rest.\n\n"
        f"{summary}"
    )


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
    # Route through configured execution backend (docker/ssh/sandbox) if not local.
    # Background jobs always run locally (need process handles).
    # F1 fix: only catch ImportError/AttributeError here. ValueError from
    # get_backend() (unknown backend name) must propagate as loud error,
    # not silent fallback to local (that would neutralize the S3 fix).
    try:
        from zeline import backends as _backends
        _backend = _backends.get_backend()
    except ValueError as _ve:
        # Unknown backend name - fail LOUD, don't run locally
        return f"ERROR: {_ve}. Command NOT executed."
    except (ImportError, AttributeError):
        _backend = None  # config not available, use local
    except Exception:
        _backend = None  # unexpected, fall through to local
    if _backend is not None and not isinstance(_backend, _backends.LocalBackend):
        try:
            _result = _backend.run(command, str(workspace), seconds, shell=True)
        except Exception as _be:
            # Fail-closed: do NOT silently fall back to local.
            # User configured docker/ssh/sandbox expecting isolation.
            return (
                f"ERROR: {type(_backend).__name__} failed: {_be}. "
                f"Fail-closed: command NOT executed locally. "
                f"Fix the backend config or switch to local backend explicitly."
            )
        if _result.timed_out:
            return (
                f"ERROR: command timed out (>{seconds} seconds) on "
                f"{type(_backend).__name__}."
            )
        return f"exit={_result.exit_code}\n{_truncate_output(_result.output)}"
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
        response = _safe_request(
            method, url, headers=hdrs, data=data, timeout=WEB_TIMEOUT,
        )
    except requests.RequestException as exc:
        return f"ERROR request: {exc.__class__.__name__}: {exc}"
    except ValueError as exc:
        return f"ERROR request blocked: {exc}"
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
        # Note: redirects disabled for downloads (SSRF safety).
        # If a URL redirects, the download will fail safely.
        with requests.get(url, headers={"User-Agent": _UA}, timeout=WEB_TIMEOUT, stream=True, allow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                return f"ERROR: download URL redirects (blocked for safety). Use the final URL directly."
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
    grants: object = None,
    ask: Callable[[str, object], str] | None = None,
) -> str:
    """Let the agent manage its own scheduled jobs.

    Zeline's scheduler already ran inside the gateway process, but the only way to
    reach it was `zeline cron` on a terminal. On a phone that means "remind me every
    morning" could not be set up in the conversation where it was asked for.

    Delivery defaults to the CALLING session, which is the whole point: a job created
    from a Telegram chat should report back into that chat. `local` is honoured when
    asked for explicitly, since a job whose work is a file or a commit does not need
    to say anything.

    Capability pre-authorization: a new job is created PAUSED and is
    only armed after the operator approves its capability grants once, through
    the ``ask`` callback (the executor's ask_operator, so the Telegram picker
    renders). A denial discards the job instead of leaving a dead entry behind.
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
            f"  capabilities: {cron.describe_grants(job)}",
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
            # Created paused: the capability grant below decides whether this
            # job ever arms. A job that cannot be approved must not exist as a
            # half-alive entry the scheduler could fire.
            job = cron.add_job(schedule, prompt, target, grants=grants, enabled=False)
        except cron.CronError as exc:
            return f"ERROR schedule_task: {exc}"
        # One-time pre-authorization: the operator approves the
        # job's capabilities ONCE, now, instead of per tool call at 3 AM.
        # No way to ask (ask=None), or a failed ask, fails closed: the job is
        # discarded. A failed ask is reported as such — it is not a denial.
        approved = False
        ask_failed = ask is None
        if ask is not None:
            try:
                verdict = ask(cron.capability_question(job), ["Allow", "Deny"])
            except Exception:
                ask_failed = True
            else:
                # _dispatch turns an ask_user crash into an "ERROR ..." string
                # rather than raising: the question never reached the
                # operator, so this is not a denial. (Timeouts and
                # cancellations are NOT errors — they parse to deny, as the
                # answer-parsing contract requires.)
                if isinstance(verdict, str) and verdict.startswith("ERROR"):
                    ask_failed = True
                else:
                    approved = approvals.parse_verdict(verdict) != "deny"
        if not approved:
            cron.remove_job(job.id)
            reason = (
                "the approval question itself could not be asked"
                if ask_failed
                else "the capability grant was denied"
            )
            return (
                f"Not created: {reason} for {job.id}, "
                "so the job was discarded. Nothing was scheduled."
            )
        cron.set_enabled(job.id, True)
        where = (
            "results will be sent to this chat"
            if job.deliver.startswith("telegram:")
            else f"results are saved in {cron.output_dir()}"
        )
        return (
            f"Created {job.id}: {job.parsed().describe()}, first run "
            f"{cron.describe_next_run(job)} — {where}. "
            f"Capabilities granted (approved once): {cron.describe_grants(job)}."
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


def _safe_request(method: str, url: str, **kwargs) -> "requests.Response":
    """HTTP request with SSRF-safe manual redirect following.

    Validates each redirect target with _is_internal_ip (max 5 hops).
    Prevents SSRF bypass via malicious redirects.
    """
    import requests
    from urllib.parse import urlparse, urljoin
    kwargs["allow_redirects"] = False
    max_hops = 5
    current_url = url
    for _ in range(max_hops + 1):
        # Validate current URL host
        host = urlparse(current_url).hostname or ""
        if _is_internal_ip(host):
            raise ValueError(f"Blocked internal host: {host}")
        resp = requests.request(method, current_url, **kwargs)
        # Check for redirect
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            current_url = urljoin(current_url, location)
            resp.close()
            continue
        return resp
    raise ValueError("Too many redirects (max 5)")


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
            response = _safe_request(
                "GET",
                url,
                headers={"User-Agent": _UA},
                proxies=network_routes.proxies(str(route["proxy_url"])),
                timeout=WEB_TIMEOUT,
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
        response = _safe_request(
            "GET",
            url,
            headers={"User-Agent": _UA},
            timeout=WEB_TIMEOUT,
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
                return injection_filter.filter_tool_result(
                    offload.maybe_offload(text, 12_000))
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
        return injection_filter.filter_tool_result(archived)
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


def _extract_keywords(text: str, limit: int = 5) -> list[str]:
    """Extract key phrases from text for follow-up searches (no model needed).

    Uses simple frequency analysis on capitalized phrases and significant
    terms. Returns up to ``limit`` phrases.
    """
    import re
    from collections import Counter

    # Capitalized phrases (likely entities/topics).
    phrases = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", text)
    # Significant lowercase terms (4+ chars, not stopwords).
    stopwords = {
        "yang", "dan", "untuk", "dengan", "dari", "pada", "adalah", "ini",
        "itu", "the", "and", "for", "with", "from", "that", "this",
    }
    words = re.findall(r"\b[a-z]{4,}\b", text.lower())
    words = [w for w in words if w not in stopwords]

    counter = Counter(phrases + words)
    # Filter: must appear at least twice, or be a multi-word phrase.
    keywords = [
        kw for kw, cnt in counter.most_common(limit * 2)
        if cnt >= 2 or " " in kw
    ]
    return keywords[:limit]


def _deep_research(query: str, max_hops: int = 2) -> str:
    """Riset multi-sumber multi-hop: cari, baca, gali lebih dalam.

    Hop 1: cari URL teratas untuk query, baca 2-3 sumber paralel.
    Hop 2+: ekstrak keyword dari hasil hop sebelumnya, cari + baca
    sumber tambahan. Deduplikasi URL antar hop.
    """
    query = query.strip()
    if not query:
        return "ERROR: empty query."

    import concurrent.futures

    seen_urls: set[str] = set()
    all_sections: list[str] = [f"Riset untuk: {query}", ""]
    current_queries = [query]

    def fetch_urls(urls: list[str]) -> dict[str, str]:
        bodies: dict[str, str] = {}
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
                pass
        return bodies

    for hop in range(max_hops):
        hop_urls: list[str] = []
        for q in current_queries:
            for url in _search_result_urls(q, limit=3):
                if url not in seen_urls:
                    seen_urls.add(url)
                    hop_urls.append(url)
                if len(hop_urls) >= 3:
                    break
            if len(hop_urls) >= 3:
                break

        if not hop_urls:
            break

        bodies = fetch_urls(hop_urls)
        hop_text = ""
        read = 0
        for url in hop_urls:
            body = bodies.get(url, "")
            if not body or body.startswith("ERROR") or body.startswith("(halaman"):
                continue
            all_sections.append(f"### Sumber (hop {hop + 1}): {url}\n{body[:1_800].strip()}")
            all_sections.append("")
            hop_text += " " + body[:2_000]
            read += 1

        if read == 0 or hop == max_hops - 1:
            break

        # Next hop: follow-up queries from extracted keywords.
        keywords = _extract_keywords(hop_text)
        current_queries = [f"{query} {kw}" for kw in keywords[:2]]
        if not current_queries:
            break

    if len(all_sections) <= 2:  # only header, no sources read
        return _web_search(query)

    all_sections.append(
        "Instruksi: sintesis poin-poin di atas menjadi jawaban ringkas & "
        "berbukti. Sebutkan sumber (URL) untuk klaim penting. Jangan mengarang "
        "fakta yang tidak ada di sumber. Jangan panggil tool lagi bila cukup."
    )
    return "\n".join(all_sections)[:20_000]


#: Shared help text for goal tool schemas. Defined here because TOOL_DEFS
#: is built at import time, before the wrapper section further below.
_GOAL_ID_HELP = "Goal id (from goal_list)."


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
        risk=ToolRisk.READ,
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
        risk=ToolRisk.WRITE,  # add/commit mutate; worst case wins
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
                "grants": {
                    "type": "object",
                    "description": (
                        "For 'add': the capabilities the job may use while nobody is "
                        "watching, approved once by the operator at creation. "
                        "Omit for the minimal default (read anything + write only "
                        "inside the workspace). Declare more only when the task "
                        "truly needs it — e.g. {\"tools\": [\"run_shell\"]} or "
                        "{\"risk\": [\"read\", \"network\"]}. Anything not granted "
                        "is denied at run time, loudly."
                    ),
                    "properties": {
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Exact tool names the job may call.",
                        },
                        "risk": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Risk classes the job may use: read, write "
                                "(workspace-confined), network, install, destructive."
                            ),
                        },
                    },
                },
            },
            "required": ["action"],
        },
        frozenset({"full"}),
        risk=ToolRisk.INSTALL,  # installs persistent unattended jobs
    ),
    ToolDef(
        "runtime_info",
        "Show Zeline runtime identity, model, provider, protocol, profile, and tools without leaking the API key or token.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
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
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "remove_memory",
        "Remove a fact in this conversation's memory containing a given substring. "
        "The fact is moved to a per-conversation trash (not deleted permanently) "
        "and can be brought back with restore_memory.",
        {
            "type": "object",
            "properties": {"substring": {"type": "string", "description": "Substring of the fact to remove"}},
            "required": ["substring"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,  # scoped store maintenance, not fs destruction
    ),
    ToolDef(
        "restore_memory",
        "Bring back facts previously removed from this conversation's memory "
        "(they wait in a per-conversation trash). Match by substring of the fact text.",
        {
            "type": "object",
            "properties": {"substring": {"type": "string", "description": "Substring of the trashed fact to restore"}},
            "required": ["substring"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,  # scoped store maintenance, not fs destruction
    ),
    ToolDef(
        "list_memory",
        "Show all facts stored for this user/conversation.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "episode_add",
        "Record an episodic memory: a titled sequence of events (what happened, in order). Use for narratives like 'yesterday we debugged X, then deployed Y'.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Episode title."},
                "events": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered list of what happened.",
                },
            },
            "required": ["title", "events"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "episode_list",
        "List recent episodic memories (event sequences).",
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max episodes.", "default": 10},
            },
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "search_sessions",
        "Full-text search across ALL past sessions (conversations + episodes). "
        "Use when you need to recall something from a previous session.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords."},
                "limit": {"type": "integer", "description": "Max results.", "default": 5},
            },
            "required": ["query"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "consolidate_memory",
        "Tidy this conversation's long-term memory: drop duplicate and expired "
        "facts, keep the rest. Deterministic nudge (no LLM call) — safe to run "
        "periodically via cron to stop memory bloat.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,  # scoped store maintenance, not fs destruction
    ),
    ToolDef(
        "learn_skill",
        "Distill this task's experience into a reusable skill. Call after "
        "completing a complex, repeatable task so future sessions can reuse "
        "what you learned. This is the self-improving learning loop.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short skill name, e.g. 'deploy-to-vps'."},
                "description": {"type": "string", "description": "One-line summary."},
                "content": {"type": "string", "description": "Full Markdown: steps, examples, gotchas."},
            },
            "required": ["name", "description", "content"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "list_learned_skills",
        "List skills the agent learned from past experience.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "improve_skill",
        "Append an improvement or gotcha to an existing learned skill.",
        {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Skill filename slug."},
                "addition": {"type": "string", "description": "Markdown to append."},
            },
            "required": ["slug", "addition"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "user_model_set",
        "Record/update a trait in the dialectic user model. Use when you learn "
        "something durable about the user (style, preferences, goals, constraints). "
        "Confidence 0.0-1.0; include evidence.",
        {
            "type": "object",
            "properties": {
                "dimension": {"type": "string", "description": "One of: communication, technical, goals, preferences, constraints, relationships."},
                "key": {"type": "string", "description": "Trait name, e.g. 'verbosity'."},
                "value": {"type": "string", "description": "Trait value."},
                "confidence": {"type": "number", "description": "0.0-1.0."},
                "evidence": {"type": "string", "description": "What supports this."},
            },
            "required": ["dimension", "key", "value", "confidence"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "user_model_get",
        "Read the dialectic user model (or one dimension) to personalize your behavior.",
        {
            "type": "object",
            "properties": {
                "dimension": {"type": "string", "description": "Optional: filter to one dimension."},
            },
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "skill_pack",
        "Package a local skill as a shareable .zip file.",
        {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Name of the skill to package."},
            },
            "required": ["skill_name"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "skill_install",
        "Install a skill from a URL or local .zip. Content is safety-scanned; "
        "suspicious packages are quarantined, not installed.",
        {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "URL or local path to .skill.zip."},
            },
            "required": ["source"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "clawhub_search",
        "Search ClawHub (5000+ community skills). Returns slug, name, summary, installs.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "Max results (1-50).", "default": 10},
            },
            "required": ["query"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "clawhub_install",
        "Install a skill from ClawHub by slug. Safety-scanned before install.",
        {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "ClawHub skill slug."},
            },
            "required": ["slug"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "workflow_execute",
        "Start a saved visual workflow (DAG of task/approval/note nodes) in "
        "the background. Task nodes run as unattended agent turns; approval "
        "nodes pause the run until workflow_resume approves/denies. Returns "
        "an execution id; poll workflow_status for progress.",
        {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow id from the visual builder."},
                "node_timeout": {"type": "number", "description": "Max seconds per task node (default 300)."},
                "approval_timeout": {"type": "number", "description": "Max seconds to wait at an approval gate (default 1800)."},
            },
            "required": ["workflow_id"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.INSTALL,  # persistent background agent work running unattended
    ),
    ToolDef(
        "workflow_pause",
        "Pause a running workflow execution between nodes.",
        {
            "type": "object",
            "properties": {
                "exec_id": {"type": "string", "description": "Execution id from workflow_execute."},
            },
            "required": ["exec_id"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "workflow_resume",
        "Resume a paused workflow execution, or resolve a waiting approval "
        "gate (approved=false cancels the run).",
        {
            "type": "object",
            "properties": {
                "exec_id": {"type": "string", "description": "Execution id from workflow_execute."},
                "approved": {"type": "boolean", "description": "Approve (true) or deny (false) a waiting approval gate. Ignored when resuming a pause."},
            },
            "required": ["exec_id"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "workflow_status",
        "Snapshot of a workflow execution: overall status plus per-node "
        "statuses, results and errors.",
        {
            "type": "object",
            "properties": {
                "exec_id": {"type": "string", "description": "Execution id from workflow_execute."},
            },
            "required": ["exec_id"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "peer_send",
        "Send a message to a configured peer Zeline instance and get its reply. "
        "The peer runs the message through its own agent loop. "
        "Peers are configured in config.json under peer.peers as "
        "{name: {url, secret}}. Use peer name, not a raw URL.",
        {
            "type": "object",
            "properties": {
                "peer": {"type": "string", "description": "Configured peer name (from peer.peers)."},
                "message": {"type": "string", "description": "Message text to send."},
            },
            "required": ["peer", "message"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "email_send",
        (
            "Send an email from the operator's configured mailbox. "
            "Requires the email gateway (`~/.zeline/gateways/email.json`)."
        ),
        {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Plain-text email body."},
            },
            "required": ["to", "subject", "body"],
        },
        frozenset({"workspace", "full"}),
        # Sends an email: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "gepa_drafts",
        "List auto-learned skill drafts (GEPA). Drafts are patterns the agent "
        "detected automatically; they become permanent skills only after "
        "verified successful uses.",
        {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter: draft, permanent, deprecated."},
            },
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "gepa_learn",
        "Manually trigger automatic pattern detection. Scans recent tool "
        "calls for repeatable successful patterns and creates skill drafts.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "goal_add",
        "Create a long-term goal for the user (survives across sessions, unlike "
        "tasks which /new wipes). Use for commitments like 'pass the $100k eval'.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short goal title."},
                "target": {"type": "string", "description": "Concrete measurable target."},
                "deadline": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD, optional.",
                },
                "milestones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Milestone titles, optional.",
                },
                "parent_id": {
                    "type": "string",
                    "description": "Parent goal ID to create a sub-goal. Optional.",
                },
            },
            "required": ["title", "target"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,  # scoped store maintenance, not fs destruction
    ),
    ToolDef(
        "goal_update",
        "Update a long-term goal: progress 0-100, status, one milestone, title, "
        "target, or deadline. Progress 100 always flips status to done.",
        {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": _GOAL_ID_HELP},
                "progress": {
                    "type": "integer",
                    "description": "New progress 0-100.",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "done"],
                },
                "milestone": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "description": "Milestone index (0-based) or title."
                        },
                        "done": {"type": "boolean"},
                    },
                    "description": "Mark one milestone done/not done.",
                },
                "title": {"type": "string"},
                "target": {"type": "string"},
                "deadline": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD; empty string clears it.",
                },
            },
            "required": ["goal_id"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,  # scoped store maintenance, not fs destruction
    ),
    ToolDef(
        "goal_list",
        "List the user's long-term goals with progress. Filter by status if needed.",
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "done"],
                    "description": "Filter by status; omit for all.",
                },
            },
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "goal_get",
        "Show one long-term goal in detail, including its milestones.",
        {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": _GOAL_ID_HELP},
            },
            "required": ["goal_id"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "sync_memory",
        "Pull recent Gmail/Calendar/GitHub activity into long-term memory. "
        "Deterministic and idempotent (watermark per source, no re-pull). "
        "Safe to run periodically via cron to keep memory fresh.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,  # scoped store maintenance, not fs destruction
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
        risk=ToolRisk.READ,
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
        risk=ToolRisk.READ,  # pure search; effect is a read, not a mutation
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
        risk=ToolRisk.READ,  # pure GET; effect is a read, not a mutation
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
        risk=ToolRisk.INSTALL,  # installs proxy routes + stores credentials
    ),
    ToolDef(
        "deep_research",
        "In-depth multi-source multi-hop research: search the web, open top pages, extract keywords for follow-up searches, and gather evidence-backed quotes to synthesize. Use when the user asks for research, comparison, or an answer needing several sources — not just one quick fact. For the full 7-step playbook (local memory first, multi-source cross-check, certainty split), load the `deep-research` skill instead.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Research topic or question"},
                "max_hops": {
                    "type": "integer",
                    "description": "Research iterations (1-3). Default 2.",
                    "minimum": 1,
                    "maximum": 3,
                },
            },
            "required": ["query"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,  # orchestrates search+fetch; read-only end to end
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
        # Inference against the already-trusted provider: the effect is a
        # read (a description/transcript), not an external state change.
        # Must stay ask-free — voice messages transcribe through here.
        risk=ToolRisk.READ,
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
        risk=ToolRisk.WRITE,
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
        risk=ToolRisk.WRITE,
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
        risk=ToolRisk.WRITE,
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
        risk=ToolRisk.WRITE,
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
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "voice_transcribe",
        "Transcribe an audio file to text using local faster-whisper (offline, no API cost). Use when the user sends a voice note/audio file and asks what was said. Returns the transcribed text. Requires faster-whisper installed and the model cached (`zeline voice download-model`).",
        {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "Audio file path in the workspace (wav/mp3/ogg/m4a)."},
                "model": {"type": "string", "description": "Whisper model size: tiny, base, small, medium, large-v3, turbo. Optional (default tiny)."},
                "language": {"type": "string", "description": "Language code, e.g. 'id' or 'en'. Optional (auto-detect)."},
            },
            "required": ["audio_path"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "voice_speak",
        "Convert text to spoken audio as a WAV file using a local offline TTS engine (piper, espeak-ng, or espeak — no API cost, no internet needed). Use when the user asks for an audio version of text and the provider TTS is unavailable. Returns the saved file path — then call send_file with that path so the user actually HEARS the audio instead of a filename.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to speak (max 5000 chars)."},
                "path": {"type": "string", "description": "Output file path in the workspace, ending in .wav. Optional (auto-named if omitted)."},
                "voice": {"type": "string", "description": "espeak voice code, e.g. 'id' for Indonesian. Optional (default id)."},
            },
            "required": ["text"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
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
        risk=ToolRisk.WRITE,
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
        # Same reasoning as analyze_media: remote inference, read effect,
        # no file written, no external state changed. The voice-message loop
        # must never block on an approval picker.
        risk=ToolRisk.READ,
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
        risk=ToolRisk.WRITE,
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
        risk=ToolRisk.DESTRUCTIVE,  # arbitrary methods incl. DELETE + bodies
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
        risk=ToolRisk.DESTRUCTIVE,  # JS eval + clicks/types/form submits
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
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "system_env",
        "Show environment info: OS/arch/CPU, installed runtimes & tools (python/node/go/git/docker/ffmpeg), and active local ports. Call before running commands to see which tools are available.",
        {"type": "object", "properties": {}},
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.READ,
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
        risk=ToolRisk.READ,
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
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "edit_file",
        "Edit one unique section of a text file in the workspace.",
        {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
        frozenset({"workspace", "full"}),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "patch_file",
        "Apply a unique replace patch to one workspace file.",
        {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
        frozenset({"workspace", "full"}),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "search_files",
        "Search text within workspace files.",
        {"type": "object", "properties": {"query": {"type": "string"}, "pattern": {"type": "string", "description": "File glob, default *"}}, "required": ["query"]},
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
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
        # GET + workspace-confined write: the network leg is a read, the
        # effect is a local file — so this is a WRITE, gated by the
        # workspace-escape check like any other file write.
        risk=ToolRisk.WRITE,
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
        risk=ToolRisk.WRITE,  # restores from snapshot; itself snapshotted
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
        risk=ToolRisk.WRITE,
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
        risk=ToolRisk.INSTALL,  # installs/modifies the agent's own procedures
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
        risk=ToolRisk.WRITE,
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
        risk=ToolRisk.DESTRUCTIVE,  # arbitrary code can delete/install
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
        risk=ToolRisk.DESTRUCTIVE,  # arbitrary shell can delete/install
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
        risk=ToolRisk.DESTRUCTIVE,  # kill terminates process groups
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
        risk=ToolRisk.WRITE,  # workers' dangerous tools are gated individually
    ),
    ToolDef(
        "spawn_worker",
        (
            "Start a background worker sub-agent that keeps working AFTER this turn "
            "without blocking the chat — the call returns immediately with a worker "
            "id. Use for long independent jobs (research, multi-step investigation) "
            "whose result is not needed right now. The worker runs unattended under "
            "the 'grants' you declare (default: read-only); anything outside the "
            "grant is denied, and it can never ask the operator. When it finishes "
            "or fails, a short summary is injected automatically at the start of a "
            "later turn — do NOT poll worker_status repeatedly; call it (or "
            "worker_result) only if you need the outcome inside THIS turn. "
            "'accept_if' is an optional phrase the result must mention, otherwise "
            "the run is rejected and retried once, then fails loudly."
        ),
        {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Self-contained task for the worker: goal, file paths, constraints, desired output language. It knows nothing about this chat.",
                },
                "grants": {
                    "type": "object",
                    "description": (
                        "Capabilities the worker may use unattended. The spawn "
                        "itself always asks the operator (Install-class), and "
                        "the approval question shows this exact declaration. "
                        "Under grant-based contexts (cron jobs, workers) the "
                        "declaration must additionally fit inside the caller's "
                        "own grants — a worker can never exceed its spawner's "
                        "capability. Omit for the read-only default. Declare "
                        "more only when the task truly needs it — e.g. "
                        "{\"tools\": [\"run_shell\"]} or "
                        "{\"risk\": [\"read\", \"write\"]}. Anything not granted "
                        "is denied at run time, loudly."
                    ),
                    "properties": {
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Exact tool names the worker may call.",
                        },
                        "risk": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Risk classes the worker may use: read, write, network, install, destructive.",
                        },
                    },
                },
                "accept_if": {
                    "type": "string",
                    "description": "Optional acceptance phrase: the worker's result must mention it (case-insensitive), otherwise the run is rejected and retried once.",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Worker IDs that must complete successfully before this worker starts. For dependent tasks (B needs A's output).",
                },
            },
            "required": ["task"],
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.INSTALL,  # persistent background work running unattended:
        # spawning always asks, and the declared worker grants are shown in
        # the approval question; the worker's own tools are gated by its grants
    ),
    ToolDef(
        "steer_worker",
        "Send a mid-flight instruction to a RUNNING worker without terminating "
        "it. The worker picks up the instruction at its next iteration and "
        "adjusts course. Use to redirect, add context, or correct a worker "
        "that's going the wrong way — no restart needed .",
        {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID from spawn_worker."},
                "instruction": {"type": "string", "description": "New instruction/direction for the worker."},
            },
            "required": ["worker_id", "instruction"],
        },
        frozenset(SAFE_PROFILES),
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "worker_status",
        (
            "Check background worker(s) started by spawn_worker. Pass a worker id "
            "for one worker, or leave empty to list all. Prefer waiting for the "
            "automatic completion report at the start of a later turn over polling "
            "this."
        ),
        {
            "type": "object",
            "properties": {
                "worker_id": {
                    "type": "string",
                    "description": "Worker id from spawn_worker. Empty = list all workers.",
                },
            },
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "worker_result",
        (
            "Read the full result of a finished background worker. Reports loudly "
            "when the worker is still running, unknown, or failed (the error is "
            "shown instead of a result)."
        ),
        {
            "type": "object",
            "properties": {
                "worker_id": {
                    "type": "string",
                    "description": "Worker id from spawn_worker.",
                },
            },
            "required": ["worker_id"],
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
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
        risk=ToolRisk.READ,
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
        risk=ToolRisk.READ,  # the approval mechanism itself; never gated
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
        risk=ToolRisk.READ,
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
        risk=ToolRisk.READ,
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
        # Posts an issue via the GitHub API: a mutating network action.
        risk=ToolRisk.NETWORK,
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
        # Posts a comment via the GitHub API: a mutating network action.
        risk=ToolRisk.NETWORK,
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
        risk=ToolRisk.READ,
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
        risk=ToolRisk.READ,
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
        risk=ToolRisk.READ,
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
        # Sends an email: the canonical mutating network action.
        risk=ToolRisk.NETWORK,
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
        risk=ToolRisk.READ,
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
        risk=ToolRisk.READ,
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
        risk=ToolRisk.READ,
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
        # Sends a WhatsApp message: a mutating network action.
        risk=ToolRisk.NETWORK,
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
        # Sends a WhatsApp template message: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    # ---- Wave 1 connectors (slack, notion, linear, gitlab, trello, todoist,
    # airtable, jira, discord, telegram_bot) ----
    ToolDef(
        "slack_list_channels",
        (
            "List the operator's Slack channels (most active first). "
            "Requires the Slack connector (`zeline connect slack`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many channels (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "slack_send_message",
        (
            "Send a message to a Slack channel. Requires the Slack connector "
            "(`zeline connect slack`)."
        ),
        {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID or name (e.g. 'C12345' or '#general')."},
                "text": {"type": "string", "description": "Message text."},
            },
            "required": ["channel", "text"],
        },
        frozenset({"workspace", "full"}),
        # Posts a message via the Slack API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "slack_read_history",
        (
            "Read recent messages from a Slack channel. Requires the Slack "
            "connector (`zeline connect slack`)."
        ),
        {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID or name (e.g. 'C12345' or '#general')."},
                "limit": {"type": "integer", "description": "How many messages (default 10)."},
            },
            "required": ["channel"],
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "notion_search",
        (
            "Search the operator's Notion workspace (pages and databases). "
            "Requires the Notion connector (`zeline connect notion`)."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text."},
                "limit": {"type": "integer", "description": "How many results (default 10)."},
            },
            "required": ["query"],
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "notion_query_database",
        (
            "Query a Notion database and list its rows. Requires the Notion "
            "connector (`zeline connect notion`)."
        ),
        {
            "type": "object",
            "properties": {
                "database_id": {"type": "string", "description": "Notion database ID."},
                "limit": {"type": "integer", "description": "How many rows (default 10)."},
            },
            "required": ["database_id"],
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "notion_create_page",
        (
            "Create a Notion page under a parent page. Requires the Notion "
            "connector (`zeline connect notion`)."
        ),
        {
            "type": "object",
            "properties": {
                "parent_page_id": {"type": "string", "description": "Parent page ID."},
                "title": {"type": "string", "description": "Page title."},
                "content": {"type": "string", "description": "Optional page body text."},
            },
            "required": ["parent_page_id", "title"],
        },
        frozenset({"workspace", "full"}),
        # Creates a page via the Notion API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "linear_list_issues",
        (
            "List issues from the operator's Linear workspace (most recently "
            "updated first). Requires the Linear connector (`zeline connect linear`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many issues (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "linear_create_issue",
        (
            "Create an issue in a Linear team. Requires the Linear connector "
            "(`zeline connect linear`)."
        ),
        {
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Linear team ID."},
                "title": {"type": "string", "description": "Issue title."},
                "description": {"type": "string", "description": "Optional issue description."},
            },
            "required": ["team_id", "title"],
        },
        frozenset({"workspace", "full"}),
        # Creates an issue via the Linear API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "gitlab_list_projects",
        (
            "List the operator's GitLab projects (most recently active first). "
            "Requires the GitLab connector (`zeline connect gitlab`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many projects (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "gitlab_list_mrs",
        (
            "List merge requests across the operator's GitLab projects. "
            "Requires the GitLab connector (`zeline connect gitlab`)."
        ),
        {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "'opened', 'closed', or 'merged' (default 'opened')."},
                "limit": {"type": "integer", "description": "How many merge requests (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "gitlab_list_issues",
        (
            "List issues across the operator's GitLab projects. Requires the "
            "GitLab connector (`zeline connect gitlab`)."
        ),
        {
            "type": "object",
            "properties": {
                "state": {"type": "string", "description": "'opened' or 'closed' (default 'opened')."},
                "limit": {"type": "integer", "description": "How many issues (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "trello_list_boards",
        (
            "List the operator's Trello boards. Requires the Trello connector "
            "(`zeline connect trello`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many boards (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "trello_list_cards",
        (
            "List cards on a Trello board. Requires the Trello connector "
            "(`zeline connect trello`)."
        ),
        {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "Trello board ID."},
                "limit": {"type": "integer", "description": "How many cards (default 20)."},
            },
            "required": ["board_id"],
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "trello_create_card",
        (
            "Create a card in a Trello list. Requires the Trello connector "
            "(`zeline connect trello`)."
        ),
        {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "Trello list ID."},
                "name": {"type": "string", "description": "Card name."},
                "desc": {"type": "string", "description": "Optional card description."},
            },
            "required": ["list_id", "name"],
        },
        frozenset({"workspace", "full"}),
        # Creates a card via the Trello API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "todoist_list_tasks",
        (
            "List the operator's Todoist tasks (due soonest first). Requires "
            "the Todoist connector (`zeline connect todoist`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many tasks (default 10)."},
            },
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "todoist_add_task",
        (
            "Add a task to the operator's Todoist inbox. Requires the Todoist "
            "connector (`zeline connect todoist`)."
        ),
        {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Task content."},
                "description": {"type": "string", "description": "Optional task description."},
                "priority": {"type": "integer", "description": "Priority 1 (normal) to 4 (urgent, default 1)."},
            },
            "required": ["content"],
        },
        frozenset({"workspace", "full"}),
        # Creates a task via the Todoist API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "airtable_list_records",
        (
            "List records from an Airtable table. Requires the Airtable "
            "connector (`zeline connect airtable`)."
        ),
        {
            "type": "object",
            "properties": {
                "base_id": {"type": "string", "description": "Airtable base ID."},
                "table_id": {"type": "string", "description": "Table ID or name."},
                "limit": {"type": "integer", "description": "How many records (default 10)."},
            },
            "required": ["base_id", "table_id"],
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "airtable_create_record",
        (
            "Create a record in an Airtable table. Requires the Airtable "
            "connector (`zeline connect airtable`)."
        ),
        {
            "type": "object",
            "properties": {
                "base_id": {"type": "string", "description": "Airtable base ID."},
                "table_id": {"type": "string", "description": "Table ID or name."},
                "fields": {"type": "object", "description": "Field values for the new record."},
            },
            "required": ["base_id", "table_id", "fields"],
        },
        frozenset({"workspace", "full"}),
        # Creates a record via the Airtable API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "jira_search",
        (
            "Search the operator's Jira with JQL. Requires the Jira connector "
            "(`zeline connect jira`)."
        ),
        {
            "type": "object",
            "properties": {
                "jql": {"type": "string", "description": "JQL query, e.g. 'project = ENG AND status = \"In Progress\"'."},
                "limit": {"type": "integer", "description": "How many issues (default 10)."},
            },
            "required": ["jql"],
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "jira_create_issue",
        (
            "Create an issue in a Jira project. Requires the Jira connector "
            "(`zeline connect jira`)."
        ),
        {
            "type": "object",
            "properties": {
                "project_key": {"type": "string", "description": "Jira project key (e.g. 'ENG')."},
                "summary": {"type": "string", "description": "Issue summary."},
                "description": {"type": "string", "description": "Optional issue description."},
                "issue_type": {"type": "string", "description": "Issue type name (default 'Task')."},
            },
            "required": ["project_key", "summary"],
        },
        frozenset({"workspace", "full"}),
        # Creates an issue via the Jira API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "discord_list_channels",
        (
            "List channels of a Discord server (guild). Requires the Discord "
            "connector (`zeline connect discord`)."
        ),
        {
            "type": "object",
            "properties": {
                "guild_id": {"type": "string", "description": "Discord server (guild) ID."},
                "limit": {"type": "integer", "description": "How many channels (default 20)."},
            },
            "required": ["guild_id"],
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "discord_send_message",
        (
            "Send a message to a Discord channel. Requires the Discord "
            "connector (`zeline connect discord`)."
        ),
        {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Discord channel ID."},
                "content": {"type": "string", "description": "Message content."},
            },
            "required": ["channel_id", "content"],
        },
        frozenset({"workspace", "full"}),
        # Posts a message via the Discord API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "telegram_bot_get_me",
        (
            "Show the linked Telegram bot's identity (username, id). Requires "
            "the Telegram Bot connector (`zeline connect telegram_bot`)."
        ),
        {
            "type": "object",
            "properties": {},
        },
        frozenset({"workspace", "full"}),
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "telegram_bot_send_message",
        (
            "Send a message as the linked Telegram bot. Requires the Telegram "
            "Bot connector (`zeline connect telegram_bot`)."
        ),
        {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "Recipient chat ID."},
                "text": {"type": "string", "description": "Message text."},
            },
            "required": ["chat_id", "text"],
        },
        frozenset({"workspace", "full"}),
        # Sends a message via the Telegram Bot API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    # ---- Wave 2 connectors (teams, twilio, sendgrid, pushover, asana,
    # clickup, monday, bitbucket, sentry, pagerduty, vercel, cloudflare,
    # datadog, confluence, dropbox, hubspot, zendesk, intercom, calendly,
    # stripe) ----
    ToolDef(
        "teams_send_message",
        (
            "Send a message to a Microsoft Teams channel via an incoming webhook. "
            "Requires the Teams connector (`zeline connect teams`)."
        ),
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Message text."}
            },
            "required": ["text"],
        },
        frozenset({"workspace", "full"}),
        # Sends a Teams message: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "twilio_send_sms",
        (
            "Send an SMS via Twilio. Requires the Twilio connector (`zeline connect "
            "twilio`)."
        ),
        {
            "type": "object",
            "properties": {
                "from_number": {"type": "string", "description": "Sender phone number (Twilio number)."},
                "to_number": {"type": "string", "description": "Recipient phone number."},
                "body": {"type": "string", "description": "Message body."}
            },
            "required": ["from_number", "to_number", "body"],
        },
        frozenset({"workspace", "full"}),
        # Sends an SMS via Twilio: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "twilio_list_messages",
        (
            "List recent Twilio messages. Requires the Twilio connector (`zeline connect "
            "twilio`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Twilio messages: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "sendgrid_send_email",
        (
            "Send an email via SendGrid. Requires the SendGrid connector (`zeline connect "
            "sendgrid`)."
        ),
        {
            "type": "object",
            "properties": {
                "to_email": {"type": "string", "description": "Recipient email."},
                "subject": {"type": "string", "description": "Email subject."},
                "body": {"type": "string", "description": "Plain-text body."},
                "from_email": {"type": "string", "description": "Sender email."}
            },
            "required": ["to_email", "subject", "body", "from_email"],
        },
        frozenset({"workspace", "full"}),
        # Sends an email via SendGrid: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "pushover_send_notification",
        (
            "Send a push notification via Pushover. Requires the Pushover connector "
            "(`zeline connect pushover`)."
        ),
        {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Notification message."},
                "title": {"type": "string", "description": "Notification title (optional)."},
                "priority": {"type": "integer", "description": "Priority -2..2 (default 0)."}
            },
            "required": ["message"],
        },
        frozenset({"workspace", "full"}),
        # Sends a Pushover notification: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "asana_list_tasks",
        (
            "List Asana tasks. Requires the Asana connector (`zeline connect asana`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
                "assignee": {"type": "string", "description": "Assignee filter (default 'me')."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Asana tasks: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "asana_create_task",
        (
            "Create an Asana task. Requires the Asana connector (`zeline connect asana`)."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Task name."},
                "notes": {"type": "string", "description": "Task notes (optional)."},
                "workspace": {"type": "string", "description": "Workspace gid (optional)."}
            },
            "required": ["name"],
        },
        frozenset({"workspace", "full"}),
        # Creates an Asana task: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "clickup_list_tasks",
        (
            "List tasks in a ClickUp list. Requires the ClickUp connector (`zeline "
            "connect clickup`)."
        ),
        {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "ClickUp list ID."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["list_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads ClickUp tasks: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "clickup_create_task",
        (
            "Create a task in a ClickUp list. Requires the ClickUp connector (`zeline "
            "connect clickup`)."
        ),
        {
            "type": "object",
            "properties": {
                "list_id": {"type": "string", "description": "ClickUp list ID."},
                "name": {"type": "string", "description": "Task name."},
                "description": {"type": "string", "description": "Task description (optional)."}
            },
            "required": ["list_id", "name"],
        },
        frozenset({"workspace", "full"}),
        # Creates a ClickUp task: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "monday_list_boards",
        (
            "List monday.com boards. Requires the monday connector (`zeline connect "
            "monday`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads monday boards: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "monday_list_items",
        (
            "List items on a monday.com board. Requires the monday connector (`zeline "
            "connect monday`)."
        ),
        {
            "type": "object",
            "properties": {
                "board_id": {"type": "string", "description": "Board ID."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["board_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads monday board items: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "bitbucket_list_repos",
        (
            "List Bitbucket repositories. Requires the Bitbucket connector (`zeline "
            "connect bitbucket`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Bitbucket repos: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "bitbucket_list_prs",
        (
            "List pull requests in a Bitbucket repo. Requires the Bitbucket connector "
            "(`zeline connect bitbucket`)."
        ),
        {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Workspace slug."},
                "repo_slug": {"type": "string", "description": "Repository slug."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["workspace", "repo_slug"],
        },
        frozenset({"workspace", "full"}),
        # Reads Bitbucket PRs: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "sentry_list_issues",
        (
            "List Sentry issues. Requires the Sentry connector (`zeline connect sentry`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
                "project_slug": {"type": "string", "description": "Project slug filter (optional)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Sentry issues: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "pagerduty_list_incidents",
        (
            "List PagerDuty incidents. Requires the PagerDuty connector (`zeline connect "
            "pagerduty`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
                "status": {"type": "string", "description": "Incident status (default 'triggered')."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads PagerDuty incidents: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "vercel_list_deployments",
        (
            "List Vercel deployments. Requires the Vercel connector (`zeline connect "
            "vercel`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Vercel deployments: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "cloudflare_list_zones",
        (
            "List Cloudflare zones. Requires the Cloudflare connector (`zeline connect "
            "cloudflare`)."
        ),
        {
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Cloudflare zones: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "cloudflare_list_dns_records",
        (
            "List DNS records in a Cloudflare zone. Requires the Cloudflare connector "
            "(`zeline connect cloudflare`)."
        ),
        {
            "type": "object",
            "properties": {
                "zone_id": {"type": "string", "description": "Zone ID."}
            },
            "required": ["zone_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads Cloudflare DNS records: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "datadog_list_monitors",
        (
            "List Datadog monitors. Requires the Datadog connector (`zeline connect "
            "datadog`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Datadog monitors: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "confluence_search_pages",
        (
            "Search Confluence pages by CQL. Requires the Confluence connector (`zeline "
            "connect confluence`)."
        ),
        {
            "type": "object",
            "properties": {
                "cql": {"type": "string", "description": "CQL query."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["cql"],
        },
        frozenset({"workspace", "full"}),
        # Searches Confluence: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "confluence_get_page",
        (
            "Get a Confluence page's content. Requires the Confluence connector (`zeline "
            "connect confluence`)."
        ),
        {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Page ID."}
            },
            "required": ["page_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads a Confluence page: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "dropbox_list_files",
        (
            "List files in a Dropbox folder. Requires the Dropbox connector (`zeline "
            "connect dropbox`)."
        ),
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder path (empty = root)."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Dropbox files: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "dropbox_get_metadata",
        (
            "Get metadata for a Dropbox file. Requires the Dropbox connector (`zeline "
            "connect dropbox`)."
        ),
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path."}
            },
            "required": ["path"],
        },
        frozenset({"workspace", "full"}),
        # Reads Dropbox metadata: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "hubspot_list_contacts",
        (
            "List HubSpot contacts. Requires the HubSpot connector (`zeline connect "
            "hubspot`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads HubSpot contacts: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "hubspot_create_contact",
        (
            "Create a HubSpot contact. Requires the HubSpot connector (`zeline connect "
            "hubspot`)."
        ),
        {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email."},
                "firstname": {"type": "string", "description": "First name (optional)."},
                "lastname": {"type": "string", "description": "Last name (optional)."}
            },
            "required": ["email"],
        },
        frozenset({"workspace", "full"}),
        # Creates a HubSpot contact: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "zendesk_list_tickets",
        (
            "List Zendesk tickets. Requires the Zendesk connector (`zeline connect "
            "zendesk`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Zendesk tickets: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "zendesk_create_ticket",
        (
            "Create a Zendesk ticket. Requires the Zendesk connector (`zeline connect "
            "zendesk`)."
        ),
        {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Ticket subject."},
                "comment": {"type": "string", "description": "Ticket comment body."},
                "priority": {"type": "string", "description": "Priority (default 'normal')."}
            },
            "required": ["subject", "comment"],
        },
        frozenset({"workspace", "full"}),
        # Creates a Zendesk ticket: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "intercom_list_conversations",
        (
            "List Intercom conversations. Requires the Intercom connector (`zeline "
            "connect intercom`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Intercom conversations: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "calendly_list_events",
        (
            "List Calendly scheduled events. Requires the Calendly connector (`zeline "
            "connect calendly`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Calendly events: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "stripe_list_charges",
        (
            "List Stripe charges. Requires the Stripe connector (`zeline connect "
            "stripe`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Stripe charges: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "stripe_list_customers",
        (
            "List Stripe customers. Requires the Stripe connector (`zeline connect "
            "stripe`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads Stripe customers: a read-only network action.
        risk=ToolRisk.READ,
    ),
    # ---- Wave 3 connectors (x_api, reddit, hackernews, mastodon, bluesky,
    # devto, mailgun, resend, vonage, onesignal, wrike, teamwork, shortcut,
    # height, npm_registry, pypi_registry, rubygems, jenkins, opsgenie,
    # render) ----
    ToolDef(
        "x_api_post_tweet",
        (
            "Post a tweet on X. Requires the X API connector (`zeline connect x_api`)."
        ),
{
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Tweet text."}
            },
            "required": ["text"],
        },
        frozenset({"workspace", "full"}),
        # Sends via X API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "x_api_read_timeline",
        (
            "Read a user's recent tweets on X. Requires the X API connector (`zeline connect x_api`)."
        ),
{
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "X username (without @)."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["username"],
        },
        frozenset({"workspace", "full"}),
        # Reads via X API: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "reddit_list_posts",
        (
            "List hot/new/top posts of a subreddit. Requires the Reddit connector (`zeline connect reddit`)."
        ),
{
            "type": "object",
            "properties": {
                "subreddit": {"type": "string", "description": "Subreddit name (without r/)."},
                "sort": {"type": "string", "description": "'hot', 'new' or 'top' (default 'hot')."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["subreddit"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Reddit: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "reddit_search",
        (
            "Search Reddit posts. Requires the Reddit connector (`zeline connect reddit`)."
        ),
{
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "subreddit": {"type": "string", "description": "Limit to a subreddit (optional)."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Reddit: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "hackernews_top_stories",
        (
            "List Hacker News top stories. Requires the Hacker News connector (`zeline connect hackernews`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Hacker News: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "hackernews_get_item",
        (
            "Get a Hacker News item (story/comment) by id. Requires the Hacker News connector (`zeline connect hackernews`)."
        ),
{
            "type": "object",
            "properties": {
                "item_id": {"type": "integer", "description": "Hacker News item id."}
            },
            "required": ["item_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Hacker News: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "mastodon_post_toot",
        (
            "Post a toot on Mastodon. Requires the Mastodon connector (`zeline connect mastodon`)."
        ),
{
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Toot text."},
                "visibility": {"type": "string", "description": "'public', 'unlisted', 'private' or 'direct' (default 'public')."}
            },
            "required": ["text"],
        },
        frozenset({"workspace", "full"}),
        # Sends via Mastodon: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "mastodon_read_timeline",
        (
            "Read the Mastodon home timeline. Requires the Mastodon connector (`zeline connect mastodon`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Mastodon: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "bluesky_post",
        (
            "Post on Bluesky. Requires the Bluesky connector (`zeline connect bluesky`)."
        ),
{
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Post text."}
            },
            "required": ["text"],
        },
        frozenset({"workspace", "full"}),
        # Sends via Bluesky: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "bluesky_read_timeline",
        (
            "Read the Bluesky timeline. Requires the Bluesky connector (`zeline connect bluesky`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Bluesky: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "devto_list_articles",
        (
            "List dev.to articles. Requires the dev.to connector (`zeline connect devto`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
                "tag": {"type": "string", "description": "Filter by tag (optional)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via dev.to: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "devto_create_article",
        (
            "Publish an article on dev.to. Requires the dev.to connector (`zeline connect devto`)."
        ),
{
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Article title."},
                "body_markdown": {"type": "string", "description": "Article body in Markdown."},
                "published": {"type": "boolean", "description": "Publish immediately (default false, saved as draft)."}
            },
            "required": ["title", "body_markdown"],
        },
        frozenset({"workspace", "full"}),
        # Sends via dev.to: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "mailgun_send_email",
        (
            "Send an email via Mailgun. Requires the Mailgun connector (`zeline connect mailgun`)."
        ),
{
            "type": "object",
            "properties": {
                "from_addr": {"type": "string", "description": "Sender address."},
                "to": {"type": "string", "description": "Recipient address."},
                "subject": {"type": "string", "description": "Subject."},
                "text": {"type": "string", "description": "Plain-text body."}
            },
            "required": ["from_addr", "to", "subject", "text"],
        },
        frozenset({"workspace", "full"}),
        # Sends via Mailgun: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "mailgun_list_messages",
        (
            "List recent Mailgun events. Requires the Mailgun connector (`zeline connect mailgun`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Mailgun: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "resend_send_email",
        (
            "Send an email via Resend. Requires the Resend connector (`zeline connect resend`)."
        ),
{
            "type": "object",
            "properties": {
                "from_addr": {"type": "string", "description": "Sender address."},
                "to": {"type": "string", "description": "Recipient address."},
                "subject": {"type": "string", "description": "Subject."},
                "html": {"type": "string", "description": "HTML body."}
            },
            "required": ["from_addr", "to", "subject", "html"],
        },
        frozenset({"workspace", "full"}),
        # Sends via Resend: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "vonage_send_sms",
        (
            "Send an SMS via Vonage. Requires the Vonage connector (`zeline connect vonage`)."
        ),
{
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient phone number in E.164."},
                "from_name": {"type": "string", "description": "Sender name/number."},
                "text": {"type": "string", "description": "Message text."}
            },
            "required": ["to", "from_name", "text"],
        },
        frozenset({"workspace", "full"}),
        # Sends via Vonage: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "onesignal_send_push",
        (
            "Send a push notification via OneSignal. Requires the OneSignal connector (`zeline connect onesignal`)."
        ),
{
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Notification title."},
                "message": {"type": "string", "description": "Notification message."}
            },
            "required": ["title", "message"],
        },
        frozenset({"workspace", "full"}),
        # Sends via OneSignal: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "wrike_list_tasks",
        (
            "List Wrike tasks. Requires the Wrike connector (`zeline connect wrike`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Wrike: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "wrike_create_task",
        (
            "Create a Wrike task in a folder. Requires the Wrike connector (`zeline connect wrike`)."
        ),
{
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title."},
                "folder_id": {"type": "string", "description": "Wrike folder id."},
                "description": {"type": "string", "description": "Task description (optional)."}
            },
            "required": ["title", "folder_id"],
        },
        frozenset({"workspace", "full"}),
        # Sends via Wrike: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "teamwork_list_projects",
        (
            "List Teamwork projects. Requires the Teamwork connector (`zeline connect teamwork`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Teamwork: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "teamwork_list_tasks",
        (
            "List Teamwork tasks. Requires the Teamwork connector (`zeline connect teamwork`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Teamwork: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "shortcut_list_stories",
        (
            "List Shortcut stories. Requires the Shortcut connector (`zeline connect shortcut`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Shortcut: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "shortcut_create_story",
        (
            "Create a Shortcut story. Requires the Shortcut connector (`zeline connect shortcut`)."
        ),
{
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Story name."},
                "description": {"type": "string", "description": "Story description (optional)."},
                "story_type": {"type": "string", "description": "'feature', 'bug' or 'chore' (default 'feature')."}
            },
            "required": ["name"],
        },
        frozenset({"workspace", "full"}),
        # Sends via Shortcut: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "height_list_tasks",
        (
            "List Height tasks. Requires the Height connector (`zeline connect height`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Height: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "npm_registry_package_info",
        (
            "Show npm package info. Requires the npm Registry connector (`zeline connect npm_registry`)."
        ),
{
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Package name."}
            },
            "required": ["name"],
        },
        frozenset({"workspace", "full"}),
        # Reads via npm Registry: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "npm_registry_search",
        (
            "Search npm packages. Requires the npm Registry connector (`zeline connect npm_registry`)."
        ),
{
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via npm Registry: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "pypi_registry_package_info",
        (
            "Show PyPI package info. Requires the PyPI connector (`zeline connect pypi_registry`)."
        ),
{
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Package name."}
            },
            "required": ["name"],
        },
        frozenset({"workspace", "full"}),
        # Reads via PyPI: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "rubygems_package_info",
        (
            "Show RubyGem info. Requires the RubyGems connector (`zeline connect rubygems`)."
        ),
{
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Gem name."}
            },
            "required": ["name"],
        },
        frozenset({"workspace", "full"}),
        # Reads via RubyGems: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "rubygems_search",
        (
            "Search RubyGems. Requires the RubyGems connector (`zeline connect rubygems`)."
        ),
{
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via RubyGems: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "jenkins_list_jobs",
        (
            "List Jenkins jobs. Requires the Jenkins connector (`zeline connect jenkins`)."
        ),
        {
            "type": "object",
            "properties": {},
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Jenkins: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "jenkins_job_status",
        (
            "Show the last build status of a Jenkins job. Requires the Jenkins connector (`zeline connect jenkins`)."
        ),
{
            "type": "object",
            "properties": {
                "job_name": {"type": "string", "description": "Job name."}
            },
            "required": ["job_name"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Jenkins: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "opsgenie_list_alerts",
        (
            "List Opsgenie alerts. Requires the Opsgenie connector (`zeline connect opsgenie`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Opsgenie: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "render_list_services",
        (
            "List Render services. Requires the Render connector (`zeline connect render`)."
        ),
{
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Render: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "render_list_deploys",
        (
            "List deploys of a Render service. Requires the Render connector (`zeline connect render`)."
        ),
{
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "description": "Render service id."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["service_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Render: a read-only network action.
        risk=ToolRisk.READ,
    ),
    # ---- Wave 4 connectors (typeform, tally, jotform, surveymonkey,
    # openweathermap, coinbase, wise, paypal, linkedin, producthunt,
    # gitbook, ghost, zoho_crm, pipedrive, freshdesk, close, chargebee,
    # paddle, box, webflow) ----
    ToolDef(
        "typeform_list_forms",
        (
            "List Typeform forms. Requires the Typeform connector (`zeline connect typeform`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Typeform: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "typeform_get_responses",
        (
            "Get responses of a Typeform form. Requires the Typeform connector (`zeline connect typeform`)."
        ),
        {
            "type": "object",
            "properties": {
                "form_id": {"type": "string", "description": "Typeform form id."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": ["form_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Typeform: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "tally_list_forms",
        (
            "List Tally forms. Requires the Tally connector (`zeline connect tally`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Tally: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "jotform_list_forms",
        (
            "List Jotform forms. Requires the Jotform connector (`zeline connect jotform`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Jotform: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "jotform_get_submissions",
        (
            "Get submissions of a Jotform form. Requires the Jotform connector (`zeline connect jotform`)."
        ),
        {
            "type": "object",
            "properties": {
                "form_id": {"type": "string", "description": "Jotform form id."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": ["form_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Jotform: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "surveymonkey_list_surveys",
        (
            "List SurveyMonkey surveys. Requires the SurveyMonkey connector (`zeline connect surveymonkey`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via SurveyMonkey: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "openweathermap_current_weather",
        (
            "Get current weather for a city. Requires the OpenWeatherMap connector (`zeline connect openweathermap`)."
        ),
        {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. Jakarta."},
            },
            "required": ["city"],
        },
        frozenset({"workspace", "full"}),
        # Reads via OpenWeatherMap: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "openweathermap_forecast",
        (
            "Get weather forecast for a city. Requires the OpenWeatherMap connector (`zeline connect openweathermap`)."
        ),
        {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. Jakarta."},
                "limit": {"type": "integer", "description": "How many forecast entries (default 8, max 100)."},
            },
            "required": ["city"],
        },
        frozenset({"workspace", "full"}),
        # Reads via OpenWeatherMap: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "coinbase_list_accounts",
        (
            "List Coinbase accounts. Requires the Coinbase connector (`zeline connect coinbase`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Coinbase: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "coinbase_spot_price",
        (
            "Get spot price of a currency pair. Requires the Coinbase connector (`zeline connect coinbase`)."
        ),
        {
            "type": "object",
            "properties": {
                "pair": {"type": "string", "description": "Currency pair, e.g. BTC-USD (default BTC-USD)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Coinbase: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "wise_list_profiles",
        (
            "List Wise profiles. Requires the Wise connector (`zeline connect wise`)."
        ),
        {
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Wise: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "wise_get_rate",
        (
            "Get a Wise exchange rate. Requires the Wise connector (`zeline connect wise`)."
        ),
        {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source currency, e.g. USD."},
                "target": {"type": "string", "description": "Target currency, e.g. EUR."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Wise: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "paypal_list_invoices",
        (
            "List PayPal invoices. Requires the PayPal connector (`zeline connect paypal`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via PayPal: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "paypal_get_order",
        (
            "Get a PayPal order by id. Requires the PayPal connector (`zeline connect paypal`)."
        ),
        {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "PayPal order id."},
            },
            "required": ["order_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via PayPal: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "linkedin_get_profile",
        (
            "Get the linked LinkedIn profile. Requires the LinkedIn connector (`zeline connect linkedin`)."
        ),
        {
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via LinkedIn: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "linkedin_share_post",
        (
            "Share a text post on LinkedIn. Requires the LinkedIn connector (`zeline connect linkedin`)."
        ),
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Post text."},
            },
            "required": ["text"],
        },
        frozenset({"workspace", "full"}),
        # Shares via LinkedIn: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "producthunt_todays_hunts",
        (
            "List today's top Product Hunt posts. Requires the Product Hunt connector (`zeline connect producthunt`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Product Hunt: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "producthunt_search_posts",
        (
            "Search Product Hunt posts. Requires the Product Hunt connector (`zeline connect producthunt`)."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": ["query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Product Hunt: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "gitbook_list_spaces",
        (
            "List GitBook spaces. Requires the GitBook connector (`zeline connect gitbook`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via GitBook: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "gitbook_list_content",
        (
            "List content of a GitBook space. Requires the GitBook connector (`zeline connect gitbook`)."
        ),
        {
            "type": "object",
            "properties": {
                "space_id": {"type": "string", "description": "GitBook space id."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": ["space_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via GitBook: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "ghost_list_posts",
        (
            "List Ghost posts. Requires the Ghost connector (`zeline connect ghost`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Ghost: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "ghost_create_post",
        (
            "Create a Ghost draft post. Requires the Ghost connector (`zeline connect ghost`)."
        ),
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Post title."},
                "html": {"type": "string", "description": "Post HTML content."},
            },
            "required": ["title"],
        },
        frozenset({"workspace", "full"}),
        # Creates via Ghost: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "zoho_crm_list_contacts",
        (
            "List Zoho CRM contacts. Requires the Zoho CRM connector (`zeline connect zoho_crm`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Zoho CRM: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "zoho_crm_create_contact",
        (
            "Create a Zoho CRM contact. Requires the Zoho CRM connector (`zeline connect zoho_crm`)."
        ),
        {
            "type": "object",
            "properties": {
                "first_name": {"type": "string", "description": "First name."},
                "last_name": {"type": "string", "description": "Last name."},
                "email": {"type": "string", "description": "Email address."},
            },
            "required": ["first_name", "last_name"],
        },
        frozenset({"workspace", "full"}),
        # Creates via Zoho CRM: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "pipedrive_list_deals",
        (
            "List Pipedrive deals. Requires the Pipedrive connector (`zeline connect pipedrive`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Pipedrive: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "pipedrive_create_deal",
        (
            "Create a Pipedrive deal. Requires the Pipedrive connector (`zeline connect pipedrive`)."
        ),
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Deal title."},
                "value": {"type": "string", "description": "Deal value."},
            },
            "required": ["title"],
        },
        frozenset({"workspace", "full"}),
        # Creates via Pipedrive: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "freshdesk_list_tickets",
        (
            "List Freshdesk tickets. Requires the Freshdesk connector (`zeline connect freshdesk`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Freshdesk: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "freshdesk_create_ticket",
        (
            "Create a Freshdesk ticket. Requires the Freshdesk connector (`zeline connect freshdesk`)."
        ),
        {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Ticket subject."},
                "description": {"type": "string", "description": "Ticket description."},
                "email": {"type": "string", "description": "Requester email."},
                "priority": {"type": "integer", "description": "Priority 1-4 (default 1)."},
                "status": {"type": "integer", "description": "Status code (default 2)."},
            },
            "required": ["subject", "description"],
        },
        frozenset({"workspace", "full"}),
        # Creates via Freshdesk: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "close_list_leads",
        (
            "List Close leads. Requires the Close connector (`zeline connect close`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Close: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "close_create_lead",
        (
            "Create a Close lead. Requires the Close connector (`zeline connect close`)."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Lead name."},
            },
            "required": ["name"],
        },
        frozenset({"workspace", "full"}),
        # Creates via Close: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "chargebee_list_customers",
        (
            "List Chargebee customers. Requires the Chargebee connector (`zeline connect chargebee`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Chargebee: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "chargebee_list_subscriptions",
        (
            "List Chargebee subscriptions. Requires the Chargebee connector (`zeline connect chargebee`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Chargebee: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "paddle_list_customers",
        (
            "List Paddle customers. Requires the Paddle connector (`zeline connect paddle`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Paddle: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "paddle_list_transactions",
        (
            "List Paddle transactions. Requires the Paddle connector (`zeline connect paddle`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Paddle: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "box_list_files",
        (
            "List files in a Box folder. Requires the Box connector (`zeline connect box`)."
        ),
        {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "description": "Box folder id (default 0 = root)."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Box: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "box_get_file_info",
        (
            "Get Box file info. Requires the Box connector (`zeline connect box`)."
        ),
        {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "Box file id."},
            },
            "required": ["file_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Box: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "webflow_list_sites",
        (
            "List Webflow sites. Requires the Webflow connector (`zeline connect webflow`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Webflow: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "webflow_list_collections",
        (
            "List collections of a Webflow site. Requires the Webflow connector (`zeline connect webflow`)."
        ),
        {
            "type": "object",
            "properties": {
                "site_id": {"type": "string", "description": "Webflow site id."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": ["site_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Webflow: a read-only network action.
        risk=ToolRisk.READ,
    ),
    # ---- Wave 5 connectors (n8n, mailchimp, activecampaign, convertkit,
    # beehiiv, buffer, railway, flyio, heroku, digitalocean, hetzner, vultr,
    # betterstack, healthchecks, cronitor, plausible, fathom, algolia,
    # meilisearch, typesense) ----
    ToolDef(
        "n8n_list_workflows",
        (
            "List n8n workflows. Requires the n8n connector (`zeline connect n8n`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via n8n: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "n8n_get_workflow",
        (
            "Get a single n8n workflow by id. Requires the n8n connector (`zeline connect n8n`)."
        ),
        {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "n8n workflow id."},
            },
            "required": ["workflow_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via n8n: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "n8n_execute_workflow",
        (
            "Execute an n8n workflow with an optional payload. Requires the n8n connector (`zeline connect n8n`)."
        ),
        {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "n8n workflow id."},
                "data": {"type": "object", "description": "Payload sent to the workflow (optional)."},
            },
            "required": ["workflow_id"],
        },
        frozenset({"workspace", "full"}),
        # Executes a workflow via the n8n API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "mailchimp_list_audiences",
        (
            "List Mailchimp audiences. Requires the Mailchimp connector (`zeline connect mailchimp`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Mailchimp: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "mailchimp_list_campaigns",
        (
            "List Mailchimp campaigns. Requires the Mailchimp connector (`zeline connect mailchimp`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Mailchimp: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "activecampaign_list_contacts",
        (
            "List ActiveCampaign contacts. Requires the ActiveCampaign connector (`zeline connect activecampaign`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via ActiveCampaign: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "activecampaign_create_contact",
        (
            "Create an ActiveCampaign contact. Requires the ActiveCampaign connector (`zeline connect activecampaign`)."
        ),
        {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Contact email address."},
                "first_name": {"type": "string", "description": "Contact first name."},
                "last_name": {"type": "string", "description": "Contact last name."},
            },
            "required": ["email"],
        },
        frozenset({"workspace", "full"}),
        # Creates a contact via the ActiveCampaign API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "convertkit_list_subscribers",
        (
            "List ConvertKit subscribers. Requires the ConvertKit connector (`zeline connect convertkit`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via ConvertKit: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "beehiiv_list_posts",
        (
            "List Beehiiv posts. Requires the Beehiiv connector (`zeline connect beehiiv`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Beehiiv: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "buffer_list_profiles",
        (
            "List Buffer profiles. Requires the Buffer connector (`zeline connect buffer`)."
        ),
        {
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Buffer: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "buffer_create_post",
        (
            "Create a Buffer post for one or more profiles. Requires the Buffer connector (`zeline connect buffer`)."
        ),
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Post text."},
                "profile_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Buffer profile ids to post to.",
                },
            },
            "required": ["text", "profile_ids"],
        },
        frozenset({"workspace", "full"}),
        # Posts via the Buffer API: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "railway_list_projects",
        (
            "List Railway projects. Requires the Railway connector (`zeline connect railway`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Railway: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "flyio_list_apps",
        (
            "List Fly.io apps. Requires the Fly.io connector (`zeline connect flyio`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Fly.io: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "heroku_list_apps",
        (
            "List Heroku apps. Requires the Heroku connector (`zeline connect heroku`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Heroku: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "digitalocean_list_droplets",
        (
            "List DigitalOcean droplets. Requires the DigitalOcean connector (`zeline connect digitalocean`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via DigitalOcean: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "hetzner_list_servers",
        (
            "List Hetzner Cloud servers. Requires the HetznerCloud connector (`zeline connect hetzner`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via HetznerCloud: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "vultr_list_instances",
        (
            "List Vultr instances. Requires the Vultr connector (`zeline connect vultr`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Vultr: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "betterstack_list_monitors",
        (
            "List BetterStack monitors. Requires the BetterStack connector (`zeline connect betterstack`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via BetterStack: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "healthchecks_list_checks",
        (
            "List Healthchecks checks. Requires the Healthchecks connector (`zeline connect healthchecks`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Healthchecks: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "cronitor_list_monitors",
        (
            "List Cronitor monitors. Requires the Cronitor connector (`zeline connect cronitor`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Cronitor: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "plausible_list_sites",
        (
            "List Plausible sites. Requires the Plausible connector (`zeline connect plausible`)."
        ),
        {
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Plausible: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "plausible_site_stats",
        (
            "Get stats of a Plausible site. Requires the Plausible connector (`zeline connect plausible`)."
        ),
        {
            "type": "object",
            "properties": {
                "site_id": {"type": "string", "description": "Plausible site id or domain."},
                "period": {"type": "string", "description": "Stats period (default 7d)."},
            },
            "required": ["site_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Plausible: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "fathom_list_sites",
        (
            "List Fathom sites. Requires the Fathom connector (`zeline connect fathom`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Fathom: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "algolia_list_indexes",
        (
            "List Algolia indexes. Requires the Algolia connector (`zeline connect algolia`)."
        ),
        {
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Algolia: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "algolia_search_index",
        (
            "Search an Algolia index. Requires the Algolia connector (`zeline connect algolia`)."
        ),
        {
            "type": "object",
            "properties": {
                "index": {"type": "string", "description": "Algolia index name."},
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": ["index", "query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Algolia: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "meilisearch_list_indexes",
        (
            "List Meilisearch indexes. Requires the Meilisearch connector (`zeline connect meilisearch`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Meilisearch: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "meilisearch_search_index",
        (
            "Search a Meilisearch index. Requires the Meilisearch connector (`zeline connect meilisearch`)."
        ),
        {
            "type": "object",
            "properties": {
                "index_uid": {"type": "string", "description": "Meilisearch index uid."},
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."},
            },
            "required": ["index_uid", "query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Meilisearch: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "typesense_list_collections",
        (
            "List Typesense collections. Requires the Typesense connector (`zeline connect typesense`)."
        ),
        {
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Typesense: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "typesense_search_collection",
        (
            "Search a Typesense collection. Requires the Typesense connector (`zeline connect typesense`)."
        ),
        {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Typesense collection name."},
                "query": {"type": "string", "description": "Search query."},
                "query_by": {"type": "string", "description": "Search fields, comma-separated (default *)."},
            },
            "required": ["collection", "query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Typesense: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "lemlist_list_campaigns",
        (
            "List Lemlist campaigns. Requires the Lemlist connector (`zeline connect lemlist`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Lemlist: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "lemlist_campaign_stats",
        (
            "Show Lemlist campaign statistics. Requires the Lemlist connector (`zeline connect lemlist`)."
        ),
        {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Lemlist campaign ID."}
            },
            "required": ["campaign_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Lemlist: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "apollo_people_search",
        (
            "Search people via Apollo. Requires the Apollo connector (`zeline connect apollo`)."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (name, title, company)."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Apollo: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "apollo_enrich_person",
        (
            "Enrich a person's data via Apollo. Requires the Apollo connector (`zeline connect apollo`)."
        ),
        {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Person's email address."}
            },
            "required": ["email"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Apollo: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "hunter_domain_search",
        (
            "Find email addresses at a domain via Hunter. Requires the Hunter connector (`zeline connect hunter`)."
        ),
        {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Domain to search (e.g. example.com)."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["domain"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Hunter: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "hunter_verify_email",
        (
            "Verify an email address via Hunter. Requires the Hunter connector (`zeline connect hunter`)."
        ),
        {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address to verify."}
            },
            "required": ["email"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Hunter: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "bitly_shorten",
        (
            "Shorten a URL via Bitly. Requires the Bitly connector (`zeline connect bitly`)."
        ),
        {
            "type": "object",
            "properties": {
                "long_url": {"type": "string", "description": "The long URL to shorten."}
            },
            "required": ["long_url"],
        },
        frozenset({"workspace", "full"}),
        # Creates via Bitly: a mutating network action.
        risk=ToolRisk.NETWORK,
    ),
    ToolDef(
        "bitly_list_links",
        (
            "List Bitly shortened links. Requires the Bitly connector (`zeline connect bitly`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Bitly: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "cloudinary_list_resources",
        (
            "List Cloudinary media resources. Requires the Cloudinary connector (`zeline connect cloudinary`)."
        ),
        {
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "description": "Resource type (default image)."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Cloudinary: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "cloudinary_resource_info",
        (
            "Show Cloudinary resource details. Requires the Cloudinary connector (`zeline connect cloudinary`)."
        ),
        {
            "type": "object",
            "properties": {
                "public_id": {"type": "string", "description": "Resource public ID."},
                "resource_type": {"type": "string", "description": "Resource type (default image)."}
            },
            "required": ["public_id"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Cloudinary: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "bunnycdn_list_pull_zones",
        (
            "List BunnyCDN pull zones. Requires the BunnyCDN connector (`zeline connect bunnycdn`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via BunnyCDN: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "bunnycdn_list_storage_zones",
        (
            "List BunnyCDN storage zones. Requires the BunnyCDN connector (`zeline connect bunnycdn`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via BunnyCDN: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "polar_list_products",
        (
            "List Polar products. Requires the Polar connector (`zeline connect polar`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Polar: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "polar_list_orders",
        (
            "List Polar orders. Requires the Polar connector (`zeline connect polar`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Polar: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "lemon_squeezy_list_customers",
        (
            "List Lemon Squeezy customers. Requires the Lemon Squeezy connector (`zeline connect lemon_squeezy`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Lemon Squeezy: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "lemon_squeezy_list_orders",
        (
            "List Lemon Squeezy orders. Requires the Lemon Squeezy connector (`zeline connect lemon_squeezy`)."
        ),
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": [],
        },
        frozenset({"workspace", "full"}),
        # Reads via Lemon Squeezy: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "crates_io_crate_info",
        (
            "Show crates.io crate info. Requires the crates.io connector (`zeline connect crates_io`)."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Crate name."}
            },
            "required": ["name"],
        },
        frozenset({"workspace", "full"}),
        # Reads via crates.io: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "crates_io_search_crates",
        (
            "Search crates.io crates. Requires the crates.io connector (`zeline connect crates_io`)."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via crates.io: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "packagist_package_info",
        (
            "Show Packagist package info. Requires the Packagist connector (`zeline connect packagist`)."
        ),
        {
            "type": "object",
            "properties": {
                "vendor": {"type": "string", "description": "Package vendor."},
                "package": {"type": "string", "description": "Package name."}
            },
            "required": ["vendor", "package"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Packagist: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "packagist_search_packages",
        (
            "Search Packagist packages. Requires the Packagist connector (`zeline connect packagist`)."
        ),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "description": "How many (default 10, max 100)."}
            },
            "required": ["query"],
        },
        frozenset({"workspace", "full"}),
        # Reads via Packagist: a read-only network action.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "propose_skill_fix",
        (
            "Record a proposed fix for a skill's content WITHOUT changing any file. "
            "The proposal must be approved by the operator and applied via "
            "apply_skill_proposal. old_text must match exactly once in the skill's "
            "current file, otherwise the proposal is rejected."
        ),
        {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill name (normalized lowercase)."},
                "title": {"type": "string", "description": "Short proposal title."},
                "old_text": {"type": "string", "description": "Exact text in the skill file (must match exactly once)."},
                "new_text": {"type": "string", "description": "Replacement text."},
                "reason": {"type": "string", "description": "Why this fix is needed."},
                "file_path": {"type": "string", "description": "File inside the skill folder (default SKILL.md)."},
            },
            "required": ["skill_name", "old_text", "new_text", "reason"],
        },
        frozenset({"workspace", "full"}),
        # Records a proposal under ~/.zeline; no skill content is touched.
        risk=ToolRisk.WRITE,
    ),
    ToolDef(
        "apply_skill_proposal",
        (
            "Apply an operator-approved skill fix proposal. Re-verifies the file "
            "before patching: if the file changed since the proposal was made, the "
            "patch is REFUSED. Files are checkpointed before patching (undoable)."
        ),
        {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string", "description": "Proposal id from propose_skill_fix (e.g. 'p-9f2c...')."},
            },
            "required": ["proposal_id"],
        },
        frozenset({"full"}),
        # Modifies the agent's own procedures.
        risk=ToolRisk.INSTALL,
    ),
    ToolDef(
        "review_skills",
        (
            "Dry-run review of skill usage: which skills to promote (used and helpful), "
            "demote, archive (unused or consistently failing), and which overlap. "
            "Changes nothing — apply via apply_skill_review."
        ),
        {"type": "object", "properties": {}},
        frozenset({"workspace", "full"}),
        # Dry-run: pure computation over telemetry and skill scan.
        risk=ToolRisk.READ,
    ),
    ToolDef(
        "apply_skill_review",
        (
            "Apply the skill review plan (promote/demote/archive). Every change is "
            "logged and reversible; archiving moves skills to .archive (restorable). "
            "Overlaps are only reported, never auto-merged."
        ),
        {"type": "object", "properties": {}},
        frozenset({"full"}),
        # Changes the agent's own skill set (priorities, archiving).
        risk=ToolRisk.INSTALL,
    ),
    ToolDef(
        "rollback_skill_change",
        (
            "Undo one logged skill change: a review change (priority/archive) or an "
            "applied proposal (id starts with 'p-'). Restores the previous state."
        ),
        {
            "type": "object",
            "properties": {
                "change_id": {"type": "string", "description": "Change id from the review ledger, or proposal id (p-...)."},
            },
            "required": ["change_id"],
        },
        frozenset({"full"}),
        # INSTALL (bukan WRITE): rollback proposal me-rewrite konten skill —
        # itu mutasi konten yang HANYA boleh jalan lewat approval operator.
        # WRITE + tanpa path arg akan lolos approval_question diam-diam.
        risk=ToolRisk.INSTALL,
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


#: Path-like arguments per Write-class tool, for the workspace-escape check.
#: A Write call whose target resolves outside the session workspace needs
#: operator approval even though Write is otherwise allowed in workspace/full.
#: Tools without filesystem targets (memory, task board, lessons) are not
#: listed: their state is internal to the session, not the filesystem.
_WRITE_PATH_ARGS: dict[str, tuple[str, ...]] = {
    "write_file": ("path",),
    "edit_file": ("path",),
    "patch_file": ("path",),
    "undo_file": ("path",),
    "git": ("path",),
    "generate_image": ("path",),
    "generate_video": ("path",),
    "edit_image": ("image", "path", "mask"),
    "edit_video": ("video", "videos", "path", "audio"),
    "text_to_speech": ("path",),
    "voice_speak": ("path",),
    "qr_code": ("path",),
    "pdf_tool": ("pdfs", "path"),
    # download_file's destination is caged to the workspace at runtime, but
    # the approval gate sees the raw args first: an escape attempt asks here
    # instead of merely failing later inside the handler.
    "download_file": ("path",),
}


def _summarize_call_args(args: Any) -> str:
    """Argument summary for approval questions.

    The first argument (usually the command/path — the thing the operator
    most needs to judge) is shown IN FULL, up to ``interaction.MAX_DETAIL_CHARS``:
    a truncated command is exactly what makes approvals dangerous, so the
    approver must be able to see all of it. ``interaction.ask`` caps the
    picker itself at ``MAX_QUESTION_CHARS`` and delivers this full text as a
    code block ahead of the picker, so a long first argument costs the
    operator one extra message, not a blind approval. The remaining arguments
    are abbreviated; at most four are shown.
    """
    if not isinstance(args, dict) or not args:
        return "(no arguments)"
    lines = []
    for position, key in enumerate(list(args)[:4]):
        value = str(args[key])
        budget = interaction.MAX_DETAIL_CHARS if position == 0 else 60
        if len(value) > budget:
            value = value[:budget] + "…"
        lines.append(f"{key}={value}")
    return "\n".join(lines)


def _episode_add(identity: str, title: str, events: list) -> str:
    """Wrapper tool ``episode_add``: record an episodic memory."""
    from zeline import memory as memory_pkg
    eid = memory_pkg.add_episode(identity, title, events or [])
    if eid.startswith("ERROR"):
        return eid
    return f"Episode recorded: {eid} — {title} ({len(events or [])} events)"


def _episode_list(identity: str, limit: int = 10) -> str:
    """Wrapper tool ``episode_list``: list recent episodes."""
    from zeline import memory as memory_pkg
    episodes = memory_pkg.list_episodes(identity, limit=limit)
    if not episodes:
        return "No episodes recorded."
    return memory_pkg.format_episodes(episodes)


def _search_sessions(query: str, limit: int = 5) -> str:
    """Wrapper tool ``search_sessions``: FTS5 full-text search across all
    past sessions (conversations + episodes)."""
    from zeline import session_search
    try:
        results = session_search.search_sessions(query, limit=limit)
    except Exception as exc:
        return f"ERROR search sessions: {exc}"
    if not results:
        return f"No past sessions matched {query!r}."
    lines = [f"Found {len(results)} match(es) for {query!r}:"]
    for r in results:
        lines.append(f"\n[{r['source']}] {r['identifier']}\n{r['snippet']}")
    return "\n".join(lines)


def _learn_skill(name: str, description: str, content: str) -> str:
    """Wrapper tool ``learn_skill``: distill experience into a reusable skill."""
    from zeline import learning
    try:
        path = learning.save_learned_skill(name, description, content)
    except Exception as exc:
        return f"ERROR learn skill: {exc}"
    return f"Skill saved: {path}\nIt will be available in future sessions."


def _list_learned_skills() -> str:
    """Wrapper tool ``list_learned_skills``."""
    from zeline import learning
    skills = learning.list_learned_skills()
    if not skills:
        return "No learned skills yet. Use learn_skill after completing a complex task."
    lines = [f"{len(skills)} learned skill(s):"]
    for s in skills:
        lines.append(f"- {s['name']} ({s['file']}): {s['description']}")
    return "\n".join(lines)


def _improve_skill(slug: str, addition: str) -> str:
    """Wrapper tool ``improve_skill``: append to a learned skill."""
    from zeline import learning
    try:
        path = learning.improve_learned_skill(slug, addition)
    except Exception as exc:
        return f"ERROR improve skill: {exc}"
    return f"Skill updated: {path}"


def _user_model_set(dimension: str, key: str, value: str,
                    confidence: float, evidence: str = "") -> str:
    """Wrapper tool ``user_model_set``."""
    from zeline import user_model
    if dimension not in user_model.DIMENSIONS:
        return f"ERROR: unknown dimension {dimension!r}. Valid: {sorted(user_model.DIMENSIONS)}"
    try:
        t = user_model.set_trait(dimension, key, value, float(confidence), evidence)
    except Exception as exc:
        return f"ERROR user model set: {exc}"
    return f"Trait saved: [{dimension}] {key} = {t['value']} (confidence {t['confidence']})"


def _user_model_get(dimension: str | None = None) -> str:
    """Wrapper tool ``user_model_get``."""
    from zeline import user_model
    try:
        if dimension:
            if dimension not in user_model.DIMENSIONS:
                return f"ERROR: unknown dimension {dimension!r}."
            traits = user_model.get_dimension(dimension)
            if not traits:
                return f"No traits in dimension {dimension!r} yet."
            lines = [f"## {dimension}"]
            for k, t in sorted(traits.items()):
                lines.append(f"- {k}: {t['value']} (confidence {t.get('confidence', 0)})")
            return "\n".join(lines)
        return user_model.summarize()
    except Exception as exc:
        return f"ERROR user model get: {exc}"


def _skill_pack(skill_name: str) -> str:
    """Wrapper tool ``skill_pack``."""
    from zeline import skill_hub
    try:
        path = skill_hub.pack_skill(skill_name)
    except Exception as exc:
        return f"ERROR pack skill: {exc}"
    return f"Skill packaged: {path}"


def _skill_install(source: str) -> str:
    """Wrapper tool ``skill_install``."""
    from zeline import skill_hub
    try:
        return skill_hub.install_skill(source)
    except Exception as exc:
        return f"ERROR install skill: {exc}"


def _clawhub_search(query: str, limit: int = 10) -> str:
    """Search ClawHub skills."""
    try:
        from zeline import clawhub
        results = clawhub.search_clawhub(query, limit)
        if not results:
            return "No skills found."
        lines = [f"ClawHub results for {query!r}:"]
        for r in results:
            lines.append(
                f"  - {r['slug']}: {r['displayName']} "
                f"({r['installs']} installs, v{r['version']})"
            )
            if r['summary']:
                lines.append(f"    {r['summary'][:120]}")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


def _clawhub_install(slug: str) -> str:
    """Install a ClawHub skill."""
    try:
        from zeline import clawhub
        path = clawhub.install_clawhub_skill(slug)
        return f"Installed ClawHub skill {slug!r} to {path}"
    except Exception as e:
        return f"ERROR: {e}"


def _email_send(to: str, subject: str, body: str) -> str:
    """Send an email via the configured email gateway (lazy import)."""
    try:
        from zeline.gateways import email as email_gw
        return email_gw.tool_send(to, subject, body)
    except Exception as exc:  # never leak tracebacks to the model
        return f"ERROR: email_send failed ({exc})."


def _peer_send(peer: str, message: str) -> str:
    """Send a message to a configured peer and return its reply."""
    try:
        from zeline import peer as peer_mod
        from zeline import config as _cfg

        peers = getattr(_cfg, "PEERS", {}) or {}
        entry = peers.get(str(peer).strip())
        if not entry:
            known = ", ".join(sorted(peers)) or "(none configured)"
            return (
                f"ERROR: unknown peer {peer!r}. "
                f"Configured peers: {known}. "
                "Add peers in config.json under peer.peers."
            )
        url = entry.get("url", "")
        secret = entry.get("secret", "") or str(getattr(_cfg, "PEER_SECRET", "") or "")
        if not secret:
            return f"ERROR: no secret for peer {peer!r} (and no global peer.secret)."
        data = peer_mod.send_to_peer(
            url, secret, message,
            from_name=str(getattr(_cfg, "NAME", "") or "zeline"),
        )
        if data.get("ok"):
            return f"[reply from {data.get('from', peer)}]\n{data.get('response', '')}"
        return f"ERROR: peer returned: {data}"
    except Exception as e:
        return f"ERROR: {e}"


def _voice_transcribe(audio_path: str, workspace: Path, model: str = "tiny",
                      language: str | None = None) -> str:
    """Wrapper tool ``voice_transcribe``: audio file -> text (local STT)."""
    from zeline import voice as _voice_mod
    try:
        target = _resolve_workspace_path(audio_path, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    try:
        text = _voice_mod.transcribe(str(target), model=model or "tiny",
                                     language=language or None)
    except _voice_mod.VoiceError as exc:
        return f"ERROR: {exc}"
    if not text:
        return "(no speech detected in the audio)"
    return text


def _voice_speak(text: str, path: str, workspace: Path, voice: str = "id") -> str:
    """Wrapper tool ``voice_speak``: text -> WAV file (local offline TTS)."""
    from zeline import voice as _voice_mod
    import time as _time
    raw = (path or "").strip()
    if not raw:
        raw = f"voice-{int(_time.time())}.wav"
    try:
        dest = _resolve_workspace_path(raw, workspace)
    except ValueError as exc:
        return f"ERROR: {exc}"
    if dest.suffix.lower() != ".wav":
        return "ERROR: output path must end in .wav."
    try:
        out = _voice_mod.speak(text, dest, voice=voice or "id")
    except _voice_mod.VoiceError as exc:
        return f"ERROR: {exc}"
    try:
        rel = out.relative_to(workspace.resolve(strict=False))
        return f"Saved voice audio to {rel} — call send_file with that path so the user actually HEARS the audio instead of a filename."
    except ValueError:
        return f"Saved voice audio to {out}"


def _gepa_drafts(status: str | None = None) -> str:
    """Wrapper tool ``gepa_drafts``."""
    from zeline import gepa
    try:
        drafts = gepa.get_drafts(status)
    except Exception as exc:
        return f"ERROR: {exc}"
    if not drafts:
        return "No skill drafts." + (f" (status={status})" if status else "")
    lines = [f"{len(drafts)} draft(s):"]
    for d in drafts:
        lines.append(
            f"- [{d['status']}] {d['name']}: {d['description']} "
            f"(uses: {d['successful_uses']}/{d['uses']})"
        )
    return "\n".join(lines)


def _gepa_learn() -> str:
    """Wrapper tool ``gepa_learn``: trigger automatic pattern detection."""
    from zeline import gepa
    try:
        new_ids = gepa.auto_learn()
    except Exception as exc:
        return f"ERROR: {exc}"
    if not new_ids:
        return "No new patterns detected."
    return f"Created {len(new_ids)} skill draft(s): {', '.join(new_ids)}"


def _declared_worker_grants(args: Any) -> tuple[list[str], list[str]]:
    """Normalized ``(tools, risk)`` a ``spawn_worker`` call would grant.

    Uses the supervisor's canonical normalization — the same function
    ``spawn()`` applies — so the approval question and the session-cache
    key describe exactly what the worker will get, never a parallel
    interpretation that could drift from enforcement.
    """
    from zeline import supervisor as supervisor_module  # lazy: avoid the import cycle

    raw = args.get("grants") if isinstance(args, dict) else None
    normalized = supervisor_module.Supervisor._normalize_grants(raw)
    return normalized["tools"], normalized["risk"]


def _spawn_grants_key(name: str, args: Any) -> str:
    """Session-cache discriminator for approval-gated tools; ``""`` otherwise.

    A session allow is only valid for the exact thing the operator approved:
    - ``spawn_worker``: the exact grant declaration (approving a read-only
      spawn must not silently cover a later spawn declaring destructive
      tools). The key is the canonical JSON of the normalized declaration,
      so ``{"risk": ["destructive"]}`` and ``{"risk": ["destructive",
      "destructive"]}`` share one key while any real difference re-asks.
    - ``apply_skill_proposal``: the exact proposal id — approving one diff
      must not silently cover a different proposal's diff later in the
      session.
    - ``rollback_skill_change``: the exact change id — approving one
      rollback must not silently cover rolling back a different change
      later in the session.
    """
    if name == "spawn_worker":
        tools, risks = _declared_worker_grants(args)
        return json.dumps({"tools": tools, "risk": risks}, sort_keys=True)
    if name == "apply_skill_proposal":
        pid = args.get("proposal_id", "") if isinstance(args, dict) else ""
        return json.dumps({"proposal_id": pid}, sort_keys=True)
    if name == "rollback_skill_change":
        cid = args.get("change_id", "") if isinstance(args, dict) else ""
        return json.dumps({"change_id": str(cid).strip()}, sort_keys=True)
    return ""


# --- zeline-brain: durable goals + memory sync wrappers --------------------

def _goal_add(
    identity: str,
    title: str,
    target: str,
    deadline: str | None = None,
    milestones: Any = None,
    parent_id: str | None = None,
) -> str:
    """Wrapper tool ``goal_add``: buat goal jangka panjang baru."""
    goal = goals.add_goal(
        identity, title, target, deadline=deadline or None,
        milestones=milestones, parent_id=parent_id or None,
    )
    kind = "Sub-goal" if parent_id else "Goal"
    return f"{kind} dibuat: {goal['id']} — {goal['title']} (progress 0%)"


def _goal_update(
    identity: str,
    goal_id: str,
    progress: Any = None,
    status: str | None = None,
    milestone: Any = None,
    title: str | None = None,
    target: str | None = None,
    deadline: str | None = None,
) -> str:
    """Wrapper tool ``goal_update``.

    ``goals.update_goal`` mengembalikan TUPLE ``(goal, note)`` — wrapper ini
    WAJIB unpack di sini supaya model tidak menerima repr tuple mentah.
    ``milestone`` dari argumen tool berbentuk dict ``{"key", "done"}`` dan
    dikonversi ke tuple yang dimengerti ``update_goal``.
    """
    if isinstance(milestone, dict):
        milestone = (milestone.get("key"), milestone.get("done"))
    goal, note = goals.update_goal(
        identity,
        goal_id,
        progress=progress,
        status=status,
        milestone=milestone,
        title=title,
        target=target,
        deadline=deadline,
    )
    out = f"Goal {goal['id']} — {goal['title']}: {goal['progress']}% [{goal['status']}]"
    # Rollup: if this is a sub-goal, update parent progress.
    parent_id = goal.get("parent_id")
    if parent_id and progress is not None:
        try:
            new_avg = goals.rollup_progress(identity, parent_id)
            out += f"\nParent {parent_id} rollup: {new_avg}%"
        except Exception:  # noqa: BLE001 - rollup must not break update
            pass
    if note:
        out += f"\n{note}"
    return out


def _goal_list(identity: str, status: str | None = None) -> str:
    """Wrapper tool ``goal_list``: daftar goal sebagai teks ringkas."""
    items = goals.list_goals(identity, status=status)
    if not items:
        return "Belum ada goal."
    lines = []
    for item in items:
        line = f"• {item['id']} — {item['title']}: {item['progress']}% [{item['status']}]"
        if item.get("target"):
            line += f" (target: {item['target']})"
        lines.append(line)
    return "\n".join(lines)


def _goal_get(identity: str, goal_id: str) -> str:
    """Wrapper tool ``goal_get``: detail satu goal termasuk milestones."""
    goal = goals.get_goal(identity, goal_id)
    lines = [
        f"{goal['title']} [{goal['status']}] — {goal['progress']}%",
        f"id: {goal['id']}",
    ]
    if goal.get("target"):
        lines.append(f"target: {goal['target']}")
    if goal.get("deadline"):
        lines.append(f"deadline: {goal['deadline']}")
    for index, ms in enumerate(goal.get("milestones", [])):
        mark = "✓" if ms["done"] else "○"
        lines.append(f"  {mark} [{index}] {ms['title']}")
    return "\n".join(lines)


def _sync_memory(identity: str) -> str:
    """Wrapper tool ``sync_memory``: tarik aktivitas konektor → memory.

    Import lazy supaya startup tetap ringan; konektor di-resolve dari
    registry di dalam ``memory_sync`` (tidak terkoneksi = source di-skip
    dengan pesan yang jelas di ringkasan).
    """
    from zeline import memory_sync

    summary = memory_sync.sync_all(identity)
    if not summary.get("enabled"):
        return "Memory sync disabled (MEMORY_SYNC_ENABLED is false)."
    parts = []
    for source in ("gmail", "calendar", "github"):
        stats = summary.get(source, {})
        parts.append(
            f"{source}: +{stats.get('added', 0)} fakta, {stats.get('skipped', 0)} dilewati"
        )
    errors = summary.get("errors", {})
    for source, message in errors.items():
        parts.append(f"{source}: ERROR {message}")
    return "\n".join(parts)


def _propose_skill_fix(
    identity: str,
    skill_name: str,
    old_text: str,
    new_text: str,
    reason: str,
    title: str = "",
    file_path: str = "SKILL.md",
) -> str:
    """Wrapper tool ``propose_skill_fix``: catat proposal tanpa mengubah file."""
    from zeline import skill_proposals as _sp

    try:
        proposal = _sp.propose_fix(
            skill_name, identity, old_text, new_text, reason, file_path, title
        )
    except ValueError as exc:
        return f"ERROR: proposal ditolak: {exc}"
    return (
        f"Proposal {proposal['id']} tercatat untuk skill '{proposal['skill_name']}' "
        f"({proposal['file_path']}). Belum ada file yang berubah — terapkan lewat "
        f"apply_skill_proposal setelah operator menyetujui."
    )


def _apply_skill_proposal(identity: str, proposal_id: str) -> str:
    """Wrapper tool ``apply_skill_proposal``: terapkan proposal yang disetujui.

    Dipanggil HANYA setelah approval operator (tool ini risk INSTALL sehingga
    approval_question selalu bertanya dan menampilkan diff persis).
    """
    from zeline import skill_proposals as _sp

    try:
        result = _sp.apply_proposal(proposal_id, identity)
    except ValueError as exc:
        return f"ERROR: {exc}"
    return (
        f"Proposal {result['id']} diterapkan ke '{result['skill_name']}' "
        f"({result['file_path']}). Rollback via rollback_skill_change."
    )


#: Rencana review yang terakhir ditampilkan di pertanyaan approval, per
#: identitas: ``{identity: (timestamp, plan)}``. Anti-TOCTOU: handler
#: ``apply_skill_review`` mengeksekusi PERSIS rencana yang operator lihat
#: (bukan hitung ulang diam-diam). Entri kedaluwarsa setelah
#: ``_REVIEW_PLAN_TTL_S`` detik — tanpa rencana segar yang disetujui,
#: handler menolak (fail-closed, termasuk untuk cron unattended yang
#: tidak pernah melewati approval_question).
_REVIEW_PLAN_CACHE: dict[str, tuple[float, list[dict]]] = {}
_REVIEW_PLAN_TTL_S = 600


def _review_skills(identity: str) -> str:
    """Wrapper tool ``review_skills``: rencana review (dry-run, tidak mengubah)."""
    from zeline import skill_review as _sr

    plan = _sr.review_skills(identity, apply=False)
    if not plan:
        return "Review skill: tidak ada rekomendasi saat ini."
    lines = [f"- {item['skill']}: {item['action']} — {item['reason']}" for item in plan]
    lines.append("")
    lines.append(
        "Ini hanya rencana (tidak ada yang berubah). "
        "Terapkan lewat apply_skill_review — perlu approval operator."
    )
    return "\n".join(lines)


def _apply_skill_review(identity: str) -> str:
    """Wrapper tool ``apply_skill_review``: terapkan rencana yang disetujui.

    HANYA mengeksekusi rencana yang terakhir tampil di pertanyaan approval
    (``_REVIEW_PLAN_CACHE``) — tidak pernah menghitung ulang diam-diam.
    Tanpa rencana segar yang disetujui (termasuk dari cron unattended),
    menolak dengan pesan jelas alih-alih menebak.
    """
    from zeline import skill_review as _sr

    cached = _REVIEW_PLAN_CACHE.get(identity)
    if cached is None or time.time() - cached[0] > _REVIEW_PLAN_TTL_S:
        return (
            "Review skill DITOLAK: tidak ada rencana yang disetujui dan "
            "masih berlaku. Jalankan review_skills untuk melihat rencana "
            "terbaru, lalu apply_skill_review (perlu approval operator)."
        )
    _, plan = cached
    if not plan:
        return "Review skill: tidak ada rekomendasi saat ini."
    applied = _sr.apply_plan(identity, plan)
    lines = [f"Review diterapkan ({len(applied)} aksi):"]
    lines.extend(
        f"- {item['skill']}: {item['action']} — {item['reason']}" for item in applied
    )
    lines.append(
        "Semua perubahan tercatat dan bisa di-rollback via rollback_skill_change."
    )
    return "\n".join(lines)


def _rollback_skill_change(identity: str, change_id: str) -> str:
    """Wrapper tool ``rollback_skill_change``: batalkan satu perubahan tercatat.

    Id proposal diawali "p-" (rollback proposal); selain itu dianggap change id
    dari review ledger. Terbatas pada state yang tercatat — bukan konten bebas.
    """
    cid = (change_id or "").strip()
    if not cid:
        return "ERROR: change_id kosong."
    try:
        if cid.startswith("p-"):
            from zeline import skill_proposals as _sp

            result = _sp.rollback_proposal(cid, identity)
            return (
                f"Proposal {result['id']} di-rollback: "
                f"'{result['skill_name']}' kembali seperti sebelum apply."
            )
        from zeline import skill_review as _sr

        return _sr.rollback_change(cid, identity)
    except Exception as exc:  # noqa: BLE001 — tool tidak boleh meledak
        return f"ERROR: rollback gagal: {exc}"


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


#: Capability floor for cron jobs created without an explicit declaration:
#: read anything the profile allows, write only inside the session workspace.
#: Anything beyond this (shell, network sends, installs) needs a declared
#: grant the operator approved once at creation time.
DEFAULT_JOB_GRANTS: dict[str, list[str]] = {"tools": [], "risk": [ToolRisk.READ, ToolRisk.WRITE]}


def normalize_job_grants(grants: object) -> dict[str, list[str]]:
    """Coerce a grants declaration into canonical shape.

    Defensive on purpose: grants arrive from jobs.json, which operators edit
    by hand and which older versions wrote without the field at all. Garbage
    in must never crash the scheduler and must never widen into more
    capability — unknown risk names are dropped, non-list values are
    ignored, and an explicitly empty declaration stays empty (fail closed).
    Anything that is not a dict at all falls back to the default minimal
    grants.
    """
    if not isinstance(grants, dict):
        return {"tools": [], "risk": list(DEFAULT_JOB_GRANTS["risk"])}
    raw_tools = grants.get("tools")
    raw_risk = grants.get("risk")
    tools = (
        sorted({str(item).strip() for item in raw_tools if str(item).strip()})
        if isinstance(raw_tools, (list, tuple))
        else []
    )
    risks = (
        sorted(
            {
                str(item).strip().lower()
                for item in raw_risk
                if str(item).strip().lower() in TOOL_RISKS
            }
        )
        if isinstance(raw_risk, (list, tuple))
        else []
    )
    return {"tools": tools, "risk": risks}


class ApprovalPolicy:
    """Decides whether a single tool call may run.

    The policy is the *context* half of the approval choke point; the other
    half is ``ToolExecutor.run()``, which consults exactly one policy for
    every model-requested tool call — chat turns, reflection, sub-agents,
    parallel branches, and cron runs alike. ``decide`` returns a raw answer
    string; ``run()`` interprets it with ``approvals.parse_verdict`` (the one
    sanctioned parser): "allow"/"allow once" runs once, "allow_session"/
    "allow sesi ini" records a session allow, anything else denies.

    ``on_tool`` is an optional renderer hook ``on_tool("ask_user", args)``.
    When set (interactive turns) the operator's picker renders exactly like
    a model-initiated question; when unset (unattended runs) the tool runs
    headless and a missing operator degrades to denial, never to approval.
    """

    on_tool: Callable[[str, dict[str, Any]], None] | None = None

    def decide(self, executor: "ToolExecutor", name: str, args: dict[str, Any]) -> str:
        """Return the operator-answer string for this call.

        Must not raise: a policy that raises is treated as "deny" by the
        gate (fail closed).
        """
        raise NotImplementedError


class InteractiveApprovalPolicy(ApprovalPolicy):
    """Approval for attended turns: chat, reflect(), sub-agents.

    This is the former agent-loop gate moved verbatim into the choke point:

    When ``config.APPROVAL_AUTO_ALLOW_ALL`` is True, all tool calls are
    allowed without prompting (agent-style). This is explicitly opt-in
    via config — the operator accepts the risk of unattended execution.

    ``approval_question`` stays the single decision function (its logic is
    untouched); the operator is asked through the ``ask_user`` tool so the
    Telegram picker / CLI prompt renders via ``on_tool`` exactly as before.
    "Allow sesi ini" consults the session cache first, so an operator
    mid-flow is not re-asked for the same tool. For ``spawn_worker`` the
    cache is keyed by the exact grant declaration approved (see
    ``_spawn_grants_key``): a session allow for one declaration never
    covers a different one.

    The session check deliberately lives in THIS policy, not in the generic
    gate: a leftover "allow sesi ini" from a chat must never widen what an
    unattended grant-based run may do.
    """

    def __init__(self, on_tool: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.on_tool = on_tool

    def decide(self, executor: "ToolExecutor", name: str, args: dict[str, Any]) -> str:
        # Pure allow-all: operator explicitly opted in via config.
        # No prompts, all tools run. MORE permissive than standard agents.
        try:
            from zeline import config as _cfg
            if getattr(_cfg, "APPROVAL_AUTO_ALLOW_ALL", False):
                return "allow"
        except Exception:
            pass
        # agent-like tiered approval (default when auto_allow_all is False):
        # - READ/WRITE/NETWORK: routine operations, auto-allow (no prompt)
        # - INSTALL/DESTRUCTIVE: dangerous, prompt; fail-closed (deny on
        #   timeout/no response) just like leading agents.
        try:
            _def = next(
                (d for d in TOOL_DEFS if d.name == name), None)
            _risk = _def.risk if _def is not None else None
            if _risk in (ToolRisk.READ, ToolRisk.WRITE, ToolRisk.NETWORK):
                return "allow"
            # INSTALL/DESTRUCTIVE/unknown: fall through to prompt below.
        except Exception:
            pass
        # The session cache is grants-aware for spawn_worker (see
        # _spawn_grants_key): a session allow only fast-paths the exact
        # declaration the operator approved, never a broader one.
        if approvals.session_allowed(
            executor.identity, name, _spawn_grants_key(name, args)
        ):
            return "allow"
        question = executor.approval_question(name, args)
        if question is None:
            return "allow"
        return executor.ask_operator(question, approvals.APPROVAL_OPTIONS)


class GrantApprovalPolicy(ApprovalPolicy):
    """Pre-authorized capability set for unattended runs (cron jobs).

    Why pre-authorization, decided once per job at creation time: at 3 AM
    nobody can tap a picker. Per-call approval would either hang every run
    on an ask timeout or force the job to guess - both wrong. A per-job
    grant is also auditable: ``schedule_task show`` prints exactly what the
    job may do, and the operator approved that list once, explicitly.

    Why denial is loud instead of fail-open: a silently skipped tool reads
    as a successful run that did nothing, and the operator only notices
    weeks later. Every denial is recorded on ``self.denials`` and the
    scheduler appends it to the job's ``last_status``, so ``cron list``
    shows what was blocked and why.

    Grants are snapshotted when the run starts (``from_job`` copies the
    declaration into this object): editing jobs.json mid-run cannot change
    what the running turn may do — no TOCTOU inside a run. The next run
    picks up the edited grants.

    Omitting BOTH constructor args gives the default minimal grants (read +
    workspace-confined write) — the same floor ``add_job`` uses. Partial
    omission does NOT: ``GrantApprovalPolicy(tools=[...])`` alone grants only
    the named tools with NO risk classes, and vice versa — the omitted side
    falls back to empty, not to the default. Pass ``from_job`` — or both args
    explicitly — for a full grant declaration. Explicitly empty args mean
    deny-all (fail closed): an explicit empty declaration is a deliberate
    choice, never silently widened.
    """

    _USE_DEFAULT: object = object()

    def __init__(
        self, tools: object = _USE_DEFAULT, risk_classes: object = _USE_DEFAULT
    ) -> None:
        if tools is self._USE_DEFAULT and risk_classes is self._USE_DEFAULT:
            normalized = normalize_job_grants(None)
        else:
            normalized = normalize_job_grants(
                {
                    "tools": () if tools is self._USE_DEFAULT else tools,
                    "risk": () if risk_classes is self._USE_DEFAULT else risk_classes,
                }
            )
        self.granted_tools = frozenset(normalized["tools"])
        self.granted_risks = frozenset(normalized["risk"])
        self.denials: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    @classmethod
    def from_job(cls, job: object) -> "GrantApprovalPolicy":
        """Snapshot a job's declared grants into a policy for one run."""
        grants = normalize_job_grants(getattr(job, "grants", None))
        return cls(tools=grants["tools"], risk_classes=grants["risk"])

    def decide(self, executor: "ToolExecutor", name: str, args: dict[str, Any]) -> str:
        if name in self.granted_tools:
            return "allow"
        if name == "ask_user":
            # An unattended run can never get an answer: deny fast instead of
            # blocking the worker thread on a futile wait for the ask timeout.
            # (Timeout/no-user is deny anyway; this just skips the wait. An
            # explicit name grant still wins — the operator's word is literal.)
            return self._deny(name, "unattended run — nobody can answer a question")
        risk = executor.risk_of(name)
        if risk is None:
            # Not a native tool (MCP/custom/OpenAPI): approval_question
            # cannot classify it, so fail closed — the operator can still
            # allowlist it by exact name in the job's tool grants.
            return self._deny(name, "not a native tool; grant it by name explicitly")
        if risk not in self.granted_risks:
            return self._deny(name, f"risk class '{risk}' is not granted to this job")
        if risk == ToolRisk.WRITE and executor.writes_outside_workspace(name, args):
            return self._deny(name, "write escapes the job workspace")
        return "allow"

    def _deny(self, name: str, reason: str) -> str:
        with self._lock:
            self.denials.append((name, reason))
        return "deny"


def _approval_denied_message(name: str) -> str:
    """What the model sees when the gate denies a call.

    Kept byte-identical to the pre-choke-point agent loop text so existing
    transcripts and tests read the same.
    """
    return (
        f"ERROR: tool '{name}' was not approved by the "
        "operator and was not executed. Either ask the "
        "operator what to do, or use a safer tool."
    )


def _format_worker_status(status: dict[str, Any]) -> str:
    """One-line compact rendering of a worker record for the model."""
    task = str(status.get("task") or "")
    line = f"• {status.get('id')} [{status.get('status')}] — {task[:80]}"
    attempts = status.get("attempts") or 0
    if attempts:
        line += f" (attempts: {attempts})"
    if status.get("status") == "failed" and status.get("error"):
        line += f" — {str(status['error'])[:120]}"
    return line


class _WorkflowToolAgent:
    """Duck-typed agent for ``workflows.execute_workflow``.

    Each task node runs as a FRESH Zeline sub-agent turn (no cross-node
    history leaking between DAG nodes; the DAG itself is the memory).
    Lazy imports inside ``send``: agent -> tools would cycle at module
    top-level.
    """

    def __init__(self, executor: "ToolExecutor"):
        self._executor = executor

    def send(self, text: str) -> str:
        from zeline.agent import Zeline

        ex = self._executor
        sub = Zeline(
            identity=f"{ex.identity}:wfnode",
            tool_profile=ex.profile,
            workspace=str(ex.workspace),
            system_extra=(
                "\n\nYou are executing ONE task node of a user-authored "
                "visual workflow. Do exactly what the task says, then reply "
                "with a concise, self-contained result. You run UNATTENDED: "
                "nobody can answer questions, so `ask_user` is disabled — "
                "decide and act on your own."
            ),
            depth=ex.depth + 1,
        )
        # Default minimal grants (read + workspace-confined write), the same
        # floor cron jobs use: background work never gets interactive pickers.
        return sub.send(text, approval_policy=GrantApprovalPolicy())


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
            # Worker di kedalaman maksimal tidak boleh melahirkan worker lagi:
            # rantai spawn tak berbatas akan menghabiskan thread & kuota
            # provider tanpa pernah kembali ke operator.
            disabled.add("spawn_worker")
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
        # The approval choke point: every model-requested tool
        # call passes _approval_gate() inside run(). Each context installs
        # the policy it needs — Zeline.send() installs InteractiveApprovalPolicy
        # per turn, cron installs GrantApprovalPolicy per run. None means no
        # enforcement (unit tests, one-shot CLI introspection); every
        # production turn installs one.
        self.approval_policy: ApprovalPolicy | None = None
        # Reentrancy guard for the gate: the approval machinery itself calls
        # back into run() (ask_user). Thread-local because the parallel tool
        # branch runs executor.run() on pool threads.
        self._approval_tls = threading.local()
        self._handlers: dict[str, ToolFunction] = {
            "runtime_info": self._runtime_info,
            "add_memory": self.memory.add,
            "remove_memory": self.memory.remove,
            "restore_memory": self.memory.restore,
            "list_memory": self.memory.formatted,
            "episode_add": lambda title, events: _episode_add(self.identity, title, events),
            "episode_list": lambda limit=10: _episode_list(self.identity, limit),
            "search_sessions": lambda query, limit=5: _search_sessions(query, limit),
            "learn_skill": lambda name, description, content: _learn_skill(name, description, content),
            "list_learned_skills": lambda: _list_learned_skills(),
            "improve_skill": lambda slug, addition: _improve_skill(slug, addition),
            "user_model_set": lambda dimension, key, value, confidence, evidence="": _user_model_set(dimension, key, value, confidence, evidence),
            "user_model_get": lambda dimension=None: _user_model_get(dimension),
            "skill_pack": lambda skill_name: _skill_pack(skill_name),
            "skill_install": lambda source: _skill_install(source),
            "clawhub_search": lambda query, limit=10: _clawhub_search(query, limit),
            "clawhub_install": lambda slug: _clawhub_install(slug),
            "workflow_execute": lambda workflow_id, node_timeout=300, approval_timeout=1800: self._workflow_execute(
                workflow_id, node_timeout=node_timeout, approval_timeout=approval_timeout
            ),
            "workflow_pause": lambda exec_id: self._workflow_pause(exec_id),
            "workflow_resume": lambda exec_id, approved=True: self._workflow_resume(exec_id, approved=approved),
            "workflow_status": lambda exec_id: self._workflow_status(exec_id),
            "email_send": lambda to, subject, body: _email_send(to, subject, body),
            "peer_send": lambda peer, message: _peer_send(peer, message),
            "gepa_drafts": lambda status=None: _gepa_drafts(status),
            "gepa_learn": lambda: _gepa_learn(),
            "consolidate_memory": lambda: self._consolidate_memory(),
            "goal_add": lambda title, target, deadline=None, milestones=None, parent_id=None: _goal_add(
                self.identity, title, target, deadline, milestones, parent_id
            ),
            "goal_update": lambda goal_id, progress=None, status=None, milestone=None, title=None, target=None, deadline=None: _goal_update(
                self.identity,
                goal_id,
                progress=progress,
                status=status,
                milestone=milestone,
                title=title,
                target=target,
                deadline=deadline,
            ),
            "goal_list": lambda status=None: _goal_list(self.identity, status),
            "goal_get": lambda goal_id: _goal_get(self.identity, goal_id),
            "sync_memory": lambda: _sync_memory(self.identity),
            "load_skill": lambda name: self._load_skill_recorded(name),
            "propose_skill_fix": lambda skill_name, old_text, new_text, reason, title="", file_path="SKILL.md": _propose_skill_fix(
                self.identity, skill_name, old_text, new_text, reason, title, file_path
            ),
            "apply_skill_proposal": lambda proposal_id: _apply_skill_proposal(self.identity, proposal_id),
            "review_skills": lambda: _review_skills(self.identity),
            "apply_skill_review": lambda: _apply_skill_review(self.identity),
            "rollback_skill_change": lambda change_id: _rollback_skill_change(self.identity, change_id),
            "web_search": lambda query: _web_search(query),
            "web_fetch": lambda url: _web_fetch(url, use_private_routes=self.profile == "full"),
            "network_route": network_routes.tool,
            "deep_research": lambda query, max_hops=2: _deep_research(query, max_hops=max_hops),
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
            "voice_transcribe": lambda audio_path, model="tiny", language=None: _voice_transcribe(
                audio_path, self.workspace, model, language
            ),
            "voice_speak": lambda text, path="", voice="id": _voice_speak(
                text, path, self.workspace, voice
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
            "schedule_task": lambda action, schedule="", prompt="", job_id="", deliver="", grants=None: _schedule_task(
                action, self.identity, schedule=schedule, prompt=prompt, job_id=job_id, deliver=deliver,
                grants=grants, ask=self.ask_operator,
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
            "spawn_worker": lambda task="", grants=None, accept_if="", depends_on=None: self._spawn_worker(
                task, grants=grants, accept_if=accept_if, depends_on=depends_on
            ),
            "worker_status": lambda worker_id="": self._worker_status(worker_id),
            "worker_result": lambda worker_id="": self._worker_result(worker_id),
            "steer_worker": lambda worker_id, instruction: self._steer_worker(worker_id, instruction),
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
            "slack_list_channels": lambda limit=10: _connector_tool(
                "slack", "list_channels", limit=limit
            ),
            "slack_send_message": lambda channel, text: _connector_tool(
                "slack", "send_message", channel=channel, text=text
            ),
            "slack_read_history": lambda channel, limit=10: _connector_tool(
                "slack", "read_history", channel=channel, limit=limit
            ),
            "notion_search": lambda query, limit=10: _connector_tool(
                "notion", "search", query=query, limit=limit
            ),
            "notion_query_database": lambda database_id, limit=10: _connector_tool(
                "notion", "query_database", database_id=database_id, limit=limit
            ),
            "notion_create_page": lambda parent_page_id, title, content="": _connector_tool(
                "notion", "create_page", parent_page_id=parent_page_id, title=title, content=content
            ),
            "linear_list_issues": lambda limit=10: _connector_tool(
                "linear", "list_issues", limit=limit
            ),
            "linear_create_issue": lambda team_id, title, description="": _connector_tool(
                "linear", "create_issue", team_id=team_id, title=title, description=description
            ),
            "gitlab_list_projects": lambda limit=10: _connector_tool(
                "gitlab", "list_projects", limit=limit
            ),
            "gitlab_list_mrs": lambda state="opened", limit=10: _connector_tool(
                "gitlab", "list_merge_requests", state=state, limit=limit
            ),
            "gitlab_list_issues": lambda state="opened", limit=10: _connector_tool(
                "gitlab", "list_issues", state=state, limit=limit
            ),
            "trello_list_boards": lambda limit=10: _connector_tool(
                "trello", "list_boards", limit=limit
            ),
            "trello_list_cards": lambda board_id, limit=20: _connector_tool(
                "trello", "list_cards", board_id=board_id, limit=limit
            ),
            "trello_create_card": lambda list_id, name, desc="": _connector_tool(
                "trello", "create_card", list_id=list_id, name=name, desc=desc
            ),
            "todoist_list_tasks": lambda limit=10: _connector_tool(
                "todoist", "list_tasks", limit=limit
            ),
            "todoist_add_task": lambda content, description="", priority=1: _connector_tool(
                "todoist", "add_task", content=content, description=description, priority=priority
            ),
            "airtable_list_records": lambda base_id, table_id, limit=10: _connector_tool(
                "airtable", "list_records", base_id=base_id, table_id=table_id, limit=limit
            ),
            "airtable_create_record": lambda base_id, table_id, fields: _connector_tool(
                "airtable", "create_record", base_id=base_id, table_id=table_id, fields=fields
            ),
            "jira_search": lambda jql, limit=10: _connector_tool(
                "jira", "search", jql=jql, limit=limit
            ),
            "jira_create_issue": lambda project_key, summary, description="", issue_type="Task": _connector_tool(
                "jira", "create_issue", project_key=project_key, summary=summary,
                description=description, issue_type=issue_type
            ),
            "discord_list_channels": lambda guild_id, limit=20: _connector_tool(
                "discord", "list_channels", guild_id=guild_id, limit=limit
            ),
            "discord_send_message": lambda channel_id, content: _connector_tool(
                "discord", "send_message", channel_id=channel_id, content=content
            ),
            "telegram_bot_get_me": lambda: _connector_tool(
                "telegram_bot", "get_me"
            ),
            "telegram_bot_send_message": lambda chat_id, text: _connector_tool(
                "telegram_bot", "send_message", chat_id=chat_id, text=text
            ),
            "teams_send_message": lambda text: _connector_tool(
                "teams", "send_message", text=text
            ),
            "twilio_send_sms": lambda from_number, to_number, body: _connector_tool(
                "twilio", "send_sms", from_number=from_number, to_number=to_number, body=body
            ),
            "twilio_list_messages": lambda limit=10: _connector_tool(
                "twilio", "list_messages", limit=limit
            ),
            "sendgrid_send_email": lambda to_email, subject, body, from_email: _connector_tool(
                "sendgrid", "send_email", to_email=to_email, subject=subject, body=body, from_email=from_email
            ),
            "pushover_send_notification": lambda message, title="", priority=0: _connector_tool(
                "pushover", "send_notification", message=message, title=title, priority=priority
            ),
            "asana_list_tasks": lambda limit=10, assignee="me": _connector_tool(
                "asana", "list_tasks", limit=limit, assignee=assignee
            ),
            "asana_create_task": lambda name, notes="", workspace="": _connector_tool(
                "asana", "create_task", name=name, notes=notes, workspace=workspace
            ),
            "clickup_list_tasks": lambda list_id, limit=10: _connector_tool(
                "clickup", "list_tasks", list_id=list_id, limit=limit
            ),
            "clickup_create_task": lambda list_id, name, description="": _connector_tool(
                "clickup", "create_task", list_id=list_id, name=name, description=description
            ),
            "monday_list_boards": lambda limit=10: _connector_tool(
                "monday", "list_boards", limit=limit
            ),
            "monday_list_items": lambda board_id, limit=10: _connector_tool(
                "monday", "list_items", board_id=board_id, limit=limit
            ),
            "bitbucket_list_repos": lambda limit=10: _connector_tool(
                "bitbucket", "list_repos", limit=limit
            ),
            "bitbucket_list_prs": lambda workspace, repo_slug, limit=10: _connector_tool(
                "bitbucket", "list_prs", workspace=workspace, repo_slug=repo_slug, limit=limit
            ),
            "sentry_list_issues": lambda limit=10, project_slug="": _connector_tool(
                "sentry", "list_issues", limit=limit, project_slug=project_slug
            ),
            "pagerduty_list_incidents": lambda limit=10, status="triggered": _connector_tool(
                "pagerduty", "list_incidents", limit=limit, status=status
            ),
            "vercel_list_deployments": lambda limit=10: _connector_tool(
                "vercel", "list_deployments", limit=limit
            ),
            "cloudflare_list_zones": lambda: _connector_tool(
                "cloudflare", "list_zones"
            ),
            "cloudflare_list_dns_records": lambda zone_id: _connector_tool(
                "cloudflare", "list_dns_records", zone_id=zone_id
            ),
            "datadog_list_monitors": lambda limit=10: _connector_tool(
                "datadog", "list_monitors", limit=limit
            ),
            "confluence_search_pages": lambda cql, limit=10: _connector_tool(
                "confluence", "search_pages", cql=cql, limit=limit
            ),
            "confluence_get_page": lambda page_id: _connector_tool(
                "confluence", "get_page", page_id=page_id
            ),
            "dropbox_list_files": lambda path="", limit=10: _connector_tool(
                "dropbox", "list_files", path=path, limit=limit
            ),
            "dropbox_get_metadata": lambda path: _connector_tool(
                "dropbox", "get_metadata", path=path
            ),
            "hubspot_list_contacts": lambda limit=10: _connector_tool(
                "hubspot", "list_contacts", limit=limit
            ),
            "hubspot_create_contact": lambda email, firstname="", lastname="": _connector_tool(
                "hubspot", "create_contact", email=email, firstname=firstname, lastname=lastname
            ),
            "zendesk_list_tickets": lambda limit=10: _connector_tool(
                "zendesk", "list_tickets", limit=limit
            ),
            "zendesk_create_ticket": lambda subject, comment, priority="normal": _connector_tool(
                "zendesk", "create_ticket", subject=subject, comment=comment, priority=priority
            ),
            "intercom_list_conversations": lambda limit=10: _connector_tool(
                "intercom", "list_conversations", limit=limit
            ),
            "calendly_list_events": lambda limit=10: _connector_tool(
                "calendly", "list_events", limit=limit
            ),
            "stripe_list_charges": lambda limit=10: _connector_tool(
                "stripe", "list_charges", limit=limit
            ),
            "stripe_list_customers": lambda limit=10: _connector_tool(
                "stripe", "list_customers", limit=limit
            ),
            "x_api_post_tweet": lambda text: _connector_tool("x_api", "post_tweet", text=text),
            "x_api_read_timeline": lambda username, limit=10: _connector_tool("x_api", "read_timeline", username=username, limit=limit),
            "reddit_list_posts": lambda subreddit, sort="hot", limit=10: _connector_tool("reddit", "list_subreddit_posts", subreddit=subreddit, sort=sort, limit=limit),
            "reddit_search": lambda query, subreddit="", limit=10: _connector_tool("reddit", "search", query=query, subreddit=subreddit, limit=limit),
            "hackernews_top_stories": lambda limit=10: _connector_tool("hackernews", "top_stories", limit=limit),
            "hackernews_get_item": lambda item_id: _connector_tool("hackernews", "get_item", item_id=item_id),
            "mastodon_post_toot": lambda text, visibility="public": _connector_tool("mastodon", "post_toot", text=text, visibility=visibility),
            "mastodon_read_timeline": lambda limit=10: _connector_tool("mastodon", "read_timeline", limit=limit),
            "bluesky_post": lambda text: _connector_tool("bluesky", "post", text=text),
            "bluesky_read_timeline": lambda limit=10: _connector_tool("bluesky", "read_timeline", limit=limit),
            "devto_list_articles": lambda limit=10, tag="": _connector_tool("devto", "list_articles", limit=limit, tag=tag),
            "devto_create_article": lambda title, body_markdown, published=False: _connector_tool("devto", "create_article", title=title, body_markdown=body_markdown, published=published),
            "mailgun_send_email": lambda from_addr, to, subject, text: _connector_tool("mailgun", "send_email", from_addr=from_addr, to=to, subject=subject, text=text),
            "mailgun_list_messages": lambda limit=10: _connector_tool("mailgun", "list_messages", limit=limit),
            "resend_send_email": lambda from_addr, to, subject, html: _connector_tool("resend", "send_email", from_addr=from_addr, to=to, subject=subject, html=html),
            "vonage_send_sms": lambda to, from_name, text: _connector_tool("vonage", "send_sms", to=to, from_name=from_name, text=text),
            "onesignal_send_push": lambda title, message: _connector_tool("onesignal", "send_push", title=title, message=message),
            "wrike_list_tasks": lambda limit=10: _connector_tool("wrike", "list_tasks", limit=limit),
            "wrike_create_task": lambda title, folder_id, description="": _connector_tool("wrike", "create_task", title=title, folder_id=folder_id, description=description),
            "teamwork_list_projects": lambda limit=10: _connector_tool("teamwork", "list_projects", limit=limit),
            "teamwork_list_tasks": lambda limit=10: _connector_tool("teamwork", "list_tasks", limit=limit),
            "shortcut_list_stories": lambda limit=10: _connector_tool("shortcut", "list_stories", limit=limit),
            "shortcut_create_story": lambda name, description="", story_type="feature": _connector_tool("shortcut", "create_story", name=name, description=description, story_type=story_type),
            "height_list_tasks": lambda limit=10: _connector_tool("height", "list_tasks", limit=limit),
            "npm_registry_package_info": lambda name: _connector_tool("npm_registry", "package_info", name=name),
            "npm_registry_search": lambda query, limit=10: _connector_tool("npm_registry", "search", query=query, limit=limit),
            "pypi_registry_package_info": lambda name: _connector_tool("pypi_registry", "package_info", name=name),
            "rubygems_package_info": lambda name: _connector_tool("rubygems", "package_info", name=name),
            "rubygems_search": lambda query, limit=10: _connector_tool("rubygems", "search", query=query, limit=limit),
            "jenkins_list_jobs": lambda : _connector_tool("jenkins", "list_jobs", ),
            "jenkins_job_status": lambda job_name: _connector_tool("jenkins", "job_status", job_name=job_name),
            "opsgenie_list_alerts": lambda limit=10: _connector_tool("opsgenie", "list_alerts", limit=limit),
            "render_list_services": lambda limit=10: _connector_tool("render", "list_services", limit=limit),
            "render_list_deploys": lambda service_id, limit=10: _connector_tool("render", "list_deploys", service_id=service_id, limit=limit),
            "typeform_list_forms": lambda limit=10: _connector_tool("typeform", "list_forms", limit=limit),
            "typeform_get_responses": lambda form_id, limit=10: _connector_tool("typeform", "get_responses", form_id=form_id, limit=limit),
            "tally_list_forms": lambda limit=10: _connector_tool("tally", "list_forms", limit=limit),
            "jotform_list_forms": lambda limit=10: _connector_tool("jotform", "list_forms", limit=limit),
            "jotform_get_submissions": lambda form_id, limit=10: _connector_tool("jotform", "get_submissions", form_id=form_id, limit=limit),
            "surveymonkey_list_surveys": lambda limit=10: _connector_tool("surveymonkey", "list_surveys", limit=limit),
            "openweathermap_current_weather": lambda city: _connector_tool("openweathermap", "current_weather", city=city),
            "openweathermap_forecast": lambda city, limit=8: _connector_tool("openweathermap", "forecast", city=city, limit=limit),
            "coinbase_list_accounts": lambda limit=10: _connector_tool("coinbase", "list_accounts", limit=limit),
            "coinbase_spot_price": lambda pair="BTC-USD": _connector_tool("coinbase", "spot_price", pair=pair),
            "wise_list_profiles": lambda : _connector_tool("wise", "list_profiles", ),
            "wise_get_rate": lambda source="USD", target="EUR": _connector_tool("wise", "get_rate", source=source, target=target),
            "paypal_list_invoices": lambda limit=10: _connector_tool("paypal", "list_invoices", limit=limit),
            "paypal_get_order": lambda order_id: _connector_tool("paypal", "get_order", order_id=order_id),
            "linkedin_get_profile": lambda : _connector_tool("linkedin", "get_profile", ),
            "linkedin_share_post": lambda text: _connector_tool("linkedin", "share_post", text=text),
            "producthunt_todays_hunts": lambda limit=10: _connector_tool("producthunt", "todays_hunts", limit=limit),
            "producthunt_search_posts": lambda query, limit=10: _connector_tool("producthunt", "search_posts", query=query, limit=limit),
            "gitbook_list_spaces": lambda limit=10: _connector_tool("gitbook", "list_spaces", limit=limit),
            "gitbook_list_content": lambda space_id, limit=10: _connector_tool("gitbook", "list_content", space_id=space_id, limit=limit),
            "ghost_list_posts": lambda limit=10: _connector_tool("ghost", "list_posts", limit=limit),
            "ghost_create_post": lambda title, html="": _connector_tool("ghost", "create_post", title=title, html=html),
            "zoho_crm_list_contacts": lambda limit=10: _connector_tool("zoho_crm", "list_contacts", limit=limit),
            "zoho_crm_create_contact": lambda first_name, last_name, email="": _connector_tool("zoho_crm", "create_contact", first_name=first_name, last_name=last_name, email=email),
            "pipedrive_list_deals": lambda limit=10: _connector_tool("pipedrive", "list_deals", limit=limit),
            "pipedrive_create_deal": lambda title, value="": _connector_tool("pipedrive", "create_deal", title=title, value=value),
            "freshdesk_list_tickets": lambda limit=10: _connector_tool("freshdesk", "list_tickets", limit=limit),
            "freshdesk_create_ticket": lambda subject, description, email="", priority=1, status=2: _connector_tool("freshdesk", "create_ticket", subject=subject, description=description, email=email, priority=priority, status=status),
            "close_list_leads": lambda limit=10: _connector_tool("close", "list_leads", limit=limit),
            "close_create_lead": lambda name: _connector_tool("close", "create_lead", name=name),
            "chargebee_list_customers": lambda limit=10: _connector_tool("chargebee", "list_customers", limit=limit),
            "chargebee_list_subscriptions": lambda limit=10: _connector_tool("chargebee", "list_subscriptions", limit=limit),
            "paddle_list_customers": lambda limit=10: _connector_tool("paddle", "list_customers", limit=limit),
            "paddle_list_transactions": lambda limit=10: _connector_tool("paddle", "list_transactions", limit=limit),
            "box_list_files": lambda folder_id="0", limit=10: _connector_tool("box", "list_files", folder_id=folder_id, limit=limit),
            "box_get_file_info": lambda file_id: _connector_tool("box", "get_file_info", file_id=file_id),
            "webflow_list_sites": lambda limit=10: _connector_tool("webflow", "list_sites", limit=limit),
            "webflow_list_collections": lambda site_id, limit=10: _connector_tool("webflow", "list_collections", site_id=site_id, limit=limit),
            "n8n_list_workflows": lambda limit=10: _connector_tool("n8n", "list_workflows", limit=limit),
            "n8n_get_workflow": lambda workflow_id: _connector_tool("n8n", "get_workflow", workflow_id=workflow_id),
            "n8n_execute_workflow": lambda workflow_id, data=None: _connector_tool("n8n", "execute_workflow", workflow_id=workflow_id, data=data),
            "mailchimp_list_audiences": lambda limit=10: _connector_tool("mailchimp", "list_audiences", limit=limit),
            "mailchimp_list_campaigns": lambda limit=10: _connector_tool("mailchimp", "list_campaigns", limit=limit),
            "activecampaign_list_contacts": lambda limit=10: _connector_tool("activecampaign", "list_contacts", limit=limit),
            "activecampaign_create_contact": lambda email, first_name="", last_name="": _connector_tool("activecampaign", "create_contact", email=email, first_name=first_name, last_name=last_name),
            "convertkit_list_subscribers": lambda limit=10: _connector_tool("convertkit", "list_subscribers", limit=limit),
            "beehiiv_list_posts": lambda limit=10: _connector_tool("beehiiv", "list_posts", limit=limit),
            "buffer_list_profiles": lambda : _connector_tool("buffer", "list_profiles", ),
            "buffer_create_post": lambda text, profile_ids: _connector_tool("buffer", "create_post", text=text, profile_ids=profile_ids),
            "railway_list_projects": lambda limit=10: _connector_tool("railway", "list_projects", limit=limit),
            "flyio_list_apps": lambda limit=10: _connector_tool("flyio", "list_apps", limit=limit),
            "heroku_list_apps": lambda limit=10: _connector_tool("heroku", "list_apps", limit=limit),
            "digitalocean_list_droplets": lambda limit=10: _connector_tool("digitalocean", "list_droplets", limit=limit),
            "hetzner_list_servers": lambda limit=10: _connector_tool("hetzner", "list_servers", limit=limit),
            "vultr_list_instances": lambda limit=10: _connector_tool("vultr", "list_instances", limit=limit),
            "betterstack_list_monitors": lambda limit=10: _connector_tool("betterstack", "list_monitors", limit=limit),
            "healthchecks_list_checks": lambda limit=10: _connector_tool("healthchecks", "list_checks", limit=limit),
            "cronitor_list_monitors": lambda limit=10: _connector_tool("cronitor", "list_monitors", limit=limit),
            "plausible_list_sites": lambda : _connector_tool("plausible", "list_sites", ),
            "plausible_site_stats": lambda site_id, period="7d": _connector_tool("plausible", "site_stats", site_id=site_id, period=period),
            "fathom_list_sites": lambda limit=10: _connector_tool("fathom", "list_sites", limit=limit),
            "algolia_list_indexes": lambda : _connector_tool("algolia", "list_indexes", ),
            "algolia_search_index": lambda index, query, limit=10: _connector_tool("algolia", "search_index", index=index, query=query, limit=limit),
            "meilisearch_list_indexes": lambda limit=10: _connector_tool("meilisearch", "list_indexes", limit=limit),
            "meilisearch_search_index": lambda index_uid, query, limit=10: _connector_tool("meilisearch", "search_index", index_uid=index_uid, query=query, limit=limit),
            "typesense_list_collections": lambda : _connector_tool("typesense", "list_collections", ),
            "typesense_search_collection": lambda collection, query, query_by="*": _connector_tool("typesense", "search_collection", collection=collection, query=query, query_by=query_by),
            "lemlist_list_campaigns": lambda limit=10: _connector_tool("lemlist", "list_campaigns", limit=limit),
            "lemlist_campaign_stats": lambda campaign_id: _connector_tool("lemlist", "campaign_stats", campaign_id=campaign_id),
            "apollo_people_search": lambda query, limit=10: _connector_tool("apollo", "people_search", query=query, limit=limit),
            "apollo_enrich_person": lambda email: _connector_tool("apollo", "enrich_person", email=email),
            "hunter_domain_search": lambda domain, limit=10: _connector_tool("hunter", "domain_search", domain=domain, limit=limit),
            "hunter_verify_email": lambda email: _connector_tool("hunter", "verify_email", email=email),
            "bitly_shorten": lambda long_url: _connector_tool("bitly", "shorten", long_url=long_url),
            "bitly_list_links": lambda limit=10: _connector_tool("bitly", "list_links", limit=limit),
            "cloudinary_list_resources": lambda resource_type="image", limit=10: _connector_tool("cloudinary", "list_resources", resource_type=resource_type, limit=limit),
            "cloudinary_resource_info": lambda public_id, resource_type="image": _connector_tool("cloudinary", "resource_info", public_id=public_id, resource_type=resource_type),
            "bunnycdn_list_pull_zones": lambda limit=10: _connector_tool("bunnycdn", "list_pull_zones", limit=limit),
            "bunnycdn_list_storage_zones": lambda limit=10: _connector_tool("bunnycdn", "list_storage_zones", limit=limit),
            "polar_list_products": lambda limit=10: _connector_tool("polar", "list_products", limit=limit),
            "polar_list_orders": lambda limit=10: _connector_tool("polar", "list_orders", limit=limit),
            "lemon_squeezy_list_customers": lambda limit=10: _connector_tool("lemon_squeezy", "list_customers", limit=limit),
            "lemon_squeezy_list_orders": lambda limit=10: _connector_tool("lemon_squeezy", "list_orders", limit=limit),
            "crates_io_crate_info": lambda name: _connector_tool("crates_io", "crate_info", name=name),
            "crates_io_search_crates": lambda query, limit=10: _connector_tool("crates_io", "search_crates", query=query, limit=limit),
            "packagist_package_info": lambda vendor, package: _connector_tool("packagist", "package_info", vendor=vendor, package=package),
            "packagist_search_packages": lambda query, limit=10: _connector_tool("packagist", "search_packages", query=query, limit=limit),
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

    def _load_skill_recorded(self, name: str) -> str:
        """load_skill + pencatatan telemetri pemakaian skill.

        Telemetri fail-safe: kegagalan pencatatan tidak boleh merusak load.
        Identitas dinormalisasi ke owner (kupas suffix ::wkr/::sub worker).
        """
        content = skills.load_skill(
            name, include_private=self._can_read_private_skills
        )
        try:
            from zeline import skill_telemetry as _st

            _st.record_load(name, _st.owner_identity(self.identity))
            _st.note_used(name)
        except Exception:
            pass
        return content

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
            f"{exp} expired removed, {kept} kept. "
            f"{dup + exp} moved to trash (restorable via restore_memory)."
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

        Each sub-agent gets a DISTINCT identity — a per-run unique suffix is
        appended so the identity is never reused, even across separate
        ``delegate_task`` calls. This matters for two stores keyed by
        identity: session-scoped approval allows (a stale "Allow sesi ini"
        from run N must never fast-path a tool in run N+1) and the
        MemoryStore file (unrelated sub-tasks must not share one memory
        file in parallel). When the sub-agent finishes, its session allows
        are cleared: the identity is unique, so the cleanup cannot touch
        any other run's state — it only keeps the in-memory allow cache
        from growing without bound.
        """
        # Import inside the function to avoid a circular import (agent → tools).
        from zeline.agent import Zeline

        sub_identity = f"{self.identity}::sub{suffix}-{uuid.uuid4().hex[:8]}"
        try:
            sub = Zeline(
                identity=sub_identity,
                tool_profile=self.profile,
                workspace=str(self.workspace),
                system_extra=system_extra,
                depth=self.depth + 1,
            )
            return sub.send(brief)
        finally:
            approvals.clear_session_allows(sub_identity)

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

    def _spawn_worker(
        self,
        task: str = "",
        grants: Any = None,
        accept_if: str = "",
        depends_on: Any = None,
    ) -> str:
        """Tool ``spawn_worker``: start a background worker, return its id at once.

        The worker runs on its own thread under a non-interactive grant
        policy (the interactive picker would hang a background thread).
        Every tool call it makes still passes the single
        ``ToolExecutor.run()`` choke point — spawning changes the policy,
        never bypasses approval.

        The grants are exactly the ones declared in this call — nothing is
        inherited and nothing is widened:

        - Interactive turns: the spawn itself always asks (Install-class),
          and the approval question shows the exact grant declaration. A
          session allow ("Allow sesi ini") only repeats for an identical
          declaration; any change asks again.
        - Grant-policy contexts (cron jobs, workers spawning workers): the
          spawn is allowed without a prompt only when the spawn itself is
          inside the context's pre-approved grants, AND the worker's grants
          must fit inside the caller's grants — privilege may stay the same
          or shrink, never grow. Anything beyond the caller's grants is
          rejected loudly here, before the worker starts, instead of being
          silently trimmed (a narrowed worker would do different work than
          the caller asked for).
        """
        # Import lazy: zeline.supervisor tidak mengimpor tools/agent di
        # top-level, tapi pola ini menjaga startup tetap ringan dan konsisten
        # dengan wrapper lain di file ini.
        from zeline import supervisor as supervisor_module

        try:
            worker_pool = supervisor_module.get_supervisor(self.identity)
            cap_error = self._worker_grant_cap_error(grants)
            if cap_error is not None:
                return f"ERROR: {cap_error}"
            # Bind the CURRENT executor context before every spawn: the
            # registry caches one Supervisor per identity, so context passed
            # only at first creation would be silently ignored on later
            # calls (worker running in the wrong workspace / stale depth).
            # bind() replaces the runner context under the lock — the
            # caller's context is never dropped silently.
            worker_pool.bind(
                profile=self.profile,
                workspace=str(self.workspace),
                depth=self.depth,
            )
            wid = worker_pool.spawn(
                task, grants=grants, accept_if=accept_if,
                depends_on=depends_on if isinstance(depends_on, list) else None,
            )
        except ValueError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:  # noqa: BLE001 — never leak tracebacks to the model
            return f"ERROR: spawn_worker failed ({exc})."
        return (
            f"Worker {wid} started in the background (non-blocking). Its "
            "completion or failure will be reported automatically at the start "
            "of a later turn — do NOT poll worker_status repeatedly. Use "
            "worker_result only if you need the outcome inside this turn."
        )

    def _worker_grant_cap_error(self, grants: Any) -> str | None:
        """Reject a worker grant declaration that exceeds the caller's own.

        Returns an error string when the declared worker grants go beyond
        what this executor's context may do, ``None`` when they fit.

        - Interactive turns: the operator approves the exact declaration
          per call (the approval question shows the grants), so the
          operator's own judgment is the cap — nothing more to check here.
        - ``GrantApprovalPolicy`` (cron jobs, workers): the cap is the
          policy's granted tools and risk classes, checked exactly the way
          the policy itself decides — a tool name counts as covered when
          granted by name, or when its risk class is granted. Unknown tool
          names (``risk_of`` → ``None``) must be granted by name, mirroring
          the policy's own fail-closed rule.
        - No policy, or a policy kind this check cannot audit: fail closed.
          (In practice the gate already denied the spawn before this runs;
          this is the backstop for direct ``_dispatch`` callers.)
        """
        policy = self.approval_policy
        if isinstance(policy, InteractiveApprovalPolicy):
            return None
        if not isinstance(policy, GrantApprovalPolicy):
            return (
                "spawn_worker denied: cannot verify the caller's capability "
                "under this approval policy (fail closed)."
            )
        from zeline import supervisor as supervisor_module  # lazy: avoid the import cycle

        requested = supervisor_module.Supervisor._normalize_grants(grants)
        over_tools = [
            tool
            for tool in requested["tools"]
            if tool not in policy.granted_tools
            and self.risk_of(tool) not in policy.granted_risks
        ]
        over_risks = [
            risk for risk in requested["risk"] if risk not in policy.granted_risks
        ]
        if not over_tools and not over_risks:
            return None
        return (
            "spawn_worker denied: the requested worker grants exceed this "
            "context's grants "
            f"(tools beyond cap: {over_tools or 'none'}; risk classes beyond "
            f"cap: {over_risks or 'none'}). A worker may only use capabilities "
            "its spawner already has — narrow the 'grants' declaration, or "
            "widen the caller's grants first."
        )

    def _worker_status(self, worker_id: str = "") -> str:
        """Tool ``worker_status``: one worker's compact status, or list all."""
        from zeline import supervisor as supervisor_module

        worker_pool = supervisor_module.get_supervisor(self.identity)
        wid = str(worker_id or "").strip()
        if wid:
            status = worker_pool.get_status(wid)
            if status is None:
                return f"ERROR: unknown worker id {wid!r}."
            return _format_worker_status(status)
        workers = worker_pool.list_workers()
        if not workers:
            return "No background workers."
        return "\n".join(_format_worker_status(item) for item in workers)

    def _steer_worker(self, worker_id: str, instruction: str) -> str:
        """Tool ``steer_worker``: send mid-flight instruction to a running worker."""
        from zeline import supervisor as supervisor_module

        worker_pool = supervisor_module.get_supervisor(self.identity)
        wid = str(worker_id or "").strip()
        if not wid:
            return "ERROR: worker_id required."
        if not (instruction or "").strip():
            return "ERROR: instruction required."
        ok = worker_pool.steer_worker(wid, instruction)
        if not ok:
            return (
                f"ERROR: cannot steer worker {wid!r} — "
                "not found or not running."
            )
        return (
            f"Steering instruction sent to worker {wid}. "
            "It will pick it up at its next iteration (no restart)."
        )

    def _workflow_execute(
        self,
        workflow_id: str,
        node_timeout: float = 300,
        approval_timeout: float = 1800,
    ) -> str:
        """Tool ``workflow_execute``: run a saved visual workflow in background."""
        from zeline import workflows as workflows_module

        wid = str(workflow_id or "").strip()
        if not wid:
            return "ERROR: workflow_id required."
        try:
            exec_id = workflows_module.execute_workflow(
                wid,
                _WorkflowToolAgent(self),
                node_timeout=float(node_timeout or 300),
                approval_timeout=float(approval_timeout or 1800),
            )
        except ValueError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:  # noqa: BLE001 - surfaced, never silent
            return f"ERROR: {type(exc).__name__}: {exc}"
        return (
            f"Workflow execution started: {exec_id}. Poll workflow_status "
            f"for progress; approval gates pause the run until workflow_resume."
        )

    def _workflow_pause(self, exec_id: str = "") -> str:
        """Tool ``workflow_pause``: pause a running workflow execution."""
        from zeline import workflows as workflows_module

        eid = str(exec_id or "").strip()
        if not eid:
            return "ERROR: exec_id required."
        if workflows_module.pause_workflow(eid):
            return f"Execution {eid} paused (takes effect between nodes)."
        return (
            f"ERROR: cannot pause {eid!r} — not found or not running."
        )

    def _workflow_resume(self, exec_id: str = "", approved: bool = True) -> str:
        """Tool ``workflow_resume``: resume a paused execution or resolve an
        approval gate (approved=False cancels the run)."""
        from zeline import workflows as workflows_module

        eid = str(exec_id or "").strip()
        if not eid:
            return "ERROR: exec_id required."
        if workflows_module.resume_workflow(eid, approved=bool(approved)):
            return (
                f"Execution {eid} resumed"
                f"({'approved' if approved else 'denied'})."
            )
        return (
            f"ERROR: cannot resume {eid!r} — not found, not paused, "
            "and no approval waiting."
        )

    def _workflow_status(self, exec_id: str = "") -> str:
        """Tool ``workflow_status``: snapshot of a workflow execution."""
        from zeline import workflows as workflows_module

        eid = str(exec_id or "").strip()
        if not eid:
            return "ERROR: exec_id required."
        ex = workflows_module.get_execution(eid)
        if ex is None:
            return f"ERROR: execution {eid!r} not found."
        lines = [
            f"Execution {ex['exec_id']} [{ex['status']}] "
            f"workflow '{ex.get('wf_name', ex.get('wf_id', ''))}'"
        ]
        for nid, n in ex.get("nodes", {}).items():
            extra = ""
            if n.get("status") == "failed" and n.get("error"):
                extra = f" — {n['error'][:120]}"
            elif n.get("status") == "done" and n.get("result"):
                extra = f" — {str(n['result'])[:120]}"
            lines.append(f"  • {nid} ({n.get('type')}) [{n.get('status')}]"
                         f" {n.get('label', '')[:60]}{extra}")
        return "\n".join(lines)

    def _worker_result(self, worker_id: str = "") -> str:
        """Tool ``worker_result``: full result of a finished worker."""
        from zeline import supervisor as supervisor_module

        wid = str(worker_id or "").strip()
        if not wid:
            return "ERROR: worker_result needs a worker id (see worker_status)."
        worker_pool = supervisor_module.get_supervisor(self.identity)
        record = worker_pool.get_result(wid)
        if record is None:
            return f"ERROR: unknown worker id {wid!r}."
        if record["status"] in ("queued", "running"):
            return (
                f"Worker {wid} is still {record['status']} — no result yet. Its "
                "completion will be reported automatically at the start of a "
                "later turn."
            )
        if record["status"] == "failed":
            return f"Worker {wid} failed: {record['error'] or 'no reason recorded'}"
        if record["status"] == "interrupted":
            return (
                f"Worker {wid} was interrupted before finishing: "
                f"{record['error'] or 'no reason recorded'}"
            )
        body = str(record.get("result") or "").strip()
        if not body:
            return f"Worker {wid} finished with an empty result."
        return f"Worker {wid} result:\n{body}"

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

    def approval_question(self, name: str, args: dict[str, Any]) -> str | None:
        """Return the operator approval question for this call, or ``None``.

        Decision-only: never blocks, never asks anything itself. The caller
        (the agent loop) performs the actual approval through the ``ask_user``
        tool, which is what renders the Telegram picker / CLI prompt — so a
        future async supervisor can intercept or override the approval flow
        at that single seam instead of inside this module.

        This is the ONE place the ask/don't-ask decision lives. The table:

        - native Install / Destructive / Network → ask. Network asks because
          the class now means *mutating* network use (send/upload/state-
          changing API): the channel crossing is the point of no return.
        - native Write → ask only when the call targets a path outside the
          session workspace.
        - native Read → never asks (pure reads, including read-only network
          fetch — risk is the effect, not the channel).
        - ``spawn_worker`` is Install-class, so it always asks; the question
          shows the exact worker grant declaration being approved, and a
          session allow only repeats for an identical declaration — a
          different declaration asks again.
        - registered non-native tool (MCP ``mcp__*``, custom ``custom_*``,
          OpenAPI ``api_*``) → risk defaults to Destructive (fail closed:
          Zeline cannot audit what an external tool really does, so an
          unclassified tool must never run silently). Lowered only by an
          explicit per-server ``trust.risk_cap`` in the operator's config
          file — never from chat, and an invalid cap value fails closed too.
        - a name registered nowhere → ``None`` (``_dispatch`` reports it as
          an error; there is nothing to approve).
        """
        definition = next(
            (item for item in self._native_defs if item.name == name), None
        )
        if definition is not None:
            risk = definition.risk
            non_native: tuple[str, str, bool] | None = None
        else:
            non_native = self._non_native_risk(name)
            if non_native is None:
                return None
            risk = non_native[0]
        if risk == ToolRisk.INSTALL:
            reason = "installs something persistent (job, route, skill)"
            if name == "spawn_worker":
                reason = (
                    "starts a persistent background worker that keeps "
                    "acting unattended after this turn"
                )
        elif risk == ToolRisk.DESTRUCTIVE:
            if non_native is None:
                reason = "can irreversibly destroy data or act externally"
            elif non_native[2]:
                reason = (
                    f"{non_native[1]} tool, trusted at the operator's "
                    "configured risk cap 'destructive'"
                )
            else:
                reason = (
                    f"unclassified {non_native[1]} tool — default-deny: "
                    "treated as destructive until trusted in the config file"
                )
        elif risk == ToolRisk.NETWORK:
            reason = "mutating network action — sends data out / changes external state"
        elif risk == ToolRisk.WRITE and self._writes_outside_workspace(name, args):
            reason = "writes outside the session workspace"
        else:
            return None
        grant_block = ""
        if name == "spawn_worker":
            # The operator is not approving a single opaque call: they are
            # approving the exact unattended capability set the worker will
            # run with. Show the normalized declaration (what spawn() will
            # actually enforce), not the raw arg dict.
            granted_tools, granted_risks = _declared_worker_grants(args)
            grant_block = (
                "\nWorker grants — exactly what this background worker may "
                "use unattended:\n"
                f"  tools: {', '.join(granted_tools) or '(none by name)'}\n"
                f"  risk: {', '.join(granted_risks) or '(none — deny-all)'}\n"
                "Anything outside this grant is denied at run time, and the "
                "worker can never ask the operator a question.\n"
                '"Allow sesi ini" covers only this exact grant declaration — '
                "a different declaration will be asked again.\n"
            )
        elif name == "apply_skill_proposal":
            # The operator approves an exact, re-verified diff — show it.
            # Fail-safe: never let a lookup error break the approval decision.
            try:
                from zeline import skill_proposals as _sp

                proposal = _sp.get_proposal(
                    str(args.get("proposal_id", "")), self.identity
                )
            except Exception:
                proposal = None
            if proposal is None:
                grant_block = "Proposal tidak dikenal — tool akan menolak.\n"
            else:
                diff = (
                    f"--- {proposal['skill_name']}/{proposal['file_path']}\n"
                    f"- {proposal['old_text']}\n"
                    f"+ {proposal['new_text']}"
                )
                if len(diff) > 1500:
                    diff = diff[:1500] + "\n…(dipotong)"
                # Quote tiap baris: konten proposal berasal dari file skill dan
                # bisa meniru chrome UI approval ("Pick one: …") bila mentah.
                quoted = "\n".join(f"> {line}" for line in diff.splitlines())
                grant_block = (
                    f"Skill: {proposal['skill_name']}\n"
                    f"File: {proposal['file_path']}\n"
                    f"Alasan: {proposal['reason']}\n"
                    f"Diff yang akan diterapkan:\n{quoted}\n"
                )
        elif name == "apply_skill_review":
            # Show the dry-run plan so the operator approves something concrete.
            # The shown plan is cached: the handler executes EXACTLY this plan
            # (anti-TOCTOU — never silently recomputed between approval and
            # execution).
            try:
                from zeline import skill_review as _sr

                plan = _sr.review_skills(self.identity, apply=False)
            except Exception:
                plan = None
            _REVIEW_PLAN_CACHE[self.identity] = (time.time(), plan or [])
            if not plan:
                grant_block = "Tidak ada rekomendasi review saat ini.\n"
            else:
                lines = [
                    f"- {item['skill']}: {item['action']} — {item['reason']}"
                    for item in plan[:20]
                ]
                if len(plan) > 20:
                    lines.append(f"…dan {len(plan) - 20} lagi")
                grant_block = (
                    "Rencana review yang akan diterapkan:\n"
                    + "\n".join(lines)
                    + "\n"
                )
        elif name == "rollback_skill_change":
            # INSTALL: rollback proposal me-rewrite konten skill — operator
            # harus tahu persis perubahan apa yang dibatalkan.
            try:
                cid = str(args.get("change_id", "")).strip()
                if cid.startswith("p-"):
                    from zeline import skill_proposals as _sp

                    proposal = _sp.get_proposal(cid, self.identity)
                    desc = (
                        f"proposal {cid} pada skill "
                        f"'{proposal['skill_name']}' "
                        f"({proposal['file_path']}) — konten kembali seperti "
                        "sebelum proposal diterapkan"
                        if proposal
                        else f"proposal {cid} (tidak dikenal — tool akan menolak)"
                    )
                else:
                    from zeline import skill_review as _sr

                    entry = next(
                        (
                            e
                            for e in _sr.get_change_log(self.identity)
                            if e.get("id") == cid
                        ),
                        None,
                    )
                    desc = (
                        f"perubahan review '{entry.get('action')}' pada skill "
                        f"'{entry.get('skill')}'"
                        if entry
                        else f"change id {cid} (tidak dikenal — tool akan menolak)"
                    )
            except Exception:
                desc = "tidak bisa dibaca — tool akan menolak dengan aman"
            grant_block = f"Yang akan di-rollback: {desc}.\n"
        return (
            f"Allow tool '{name}'? Risk: {risk} — {reason}.\n"
            f"{_summarize_call_args(args)}\n"
            f"{grant_block}"
            "Pick one:\n"
            "- Allow once — run this single call only. You will be asked again next time.\n"
            f"- Allow sesi ini — allow '{name}' for the rest of this session, no more "
            "asking. Cleared automatically when the session ends.\n"
            "- Deny — do not run it."
        )

    def _non_native_risk(self, name: str) -> tuple[str, str, bool] | None:
        """Risk class for a registered non-native tool.

        Returns ``(risk, origin, via_cap)`` — the effective risk class, a
        short human label of where the tool comes from, and whether the risk
        was lowered by an explicit config trust cap. Returns ``None`` when
        the name is not a registered MCP/custom/OpenAPI tool at all (the
        dispatcher reports those as errors; approval has nothing to decide).

        Fail-closed by construction: anything unclassified is Destructive,
        and only a valid per-server ``trust.risk_cap`` in the operator's
        config file can lower that — an unknown or mistyped cap value keeps
        the Destructive default instead of silently widening permissions.
        """
        if self.mcp is not None:
            parsed = mcp_module.parse_tool_name(name)
            if parsed is not None:
                server_name, _tool = parsed
                if not self.mcp.has_tool(name):
                    return None
                cap = self.mcp.risk_cap_for(server_name)
                if cap in TOOL_RISKS:
                    return cap, f"MCP server '{server_name}'", True
                return ToolRisk.DESTRUCTIVE, f"MCP server '{server_name}'", False
        if self.custom is not None and name.startswith(custom_tools.TOOL_PREFIX):
            if not self.custom.has_tool(name):
                return None
            return ToolRisk.DESTRUCTIVE, "custom", False
        if self.openapi is not None and name.startswith(openapi_tools.TOOL_PREFIX):
            if not self.openapi.has_tool(name):
                return None
            return ToolRisk.DESTRUCTIVE, "OpenAPI", False
        return None

    def _writes_outside_workspace(self, name: str, args: dict[str, Any]) -> bool:
        """True when a Write-class call targets a path outside the workspace."""
        keys = _WRITE_PATH_ARGS.get(name, ())
        if not keys or not isinstance(args, dict):
            return False
        root = self.workspace.resolve(strict=False)
        for key in keys:
            value = args.get(key)
            candidates = value if isinstance(value, (list, tuple)) else [value]
            for candidate in candidates:
                if not isinstance(candidate, str):
                    continue
                for part in candidate.split(","):
                    item = part.strip()
                    if not item or "://" in item:
                        continue  # URL, not a filesystem path
                    path = Path(item).expanduser()
                    if not path.is_absolute():
                        path = self.workspace / path
                    try:
                        path.resolve(strict=False).relative_to(root)
                    except ValueError:
                        return True
        return False

    def run(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool, wrapped in the operator's plugin hooks if any.

        The hooks are deliberately outside _dispatch so that every kind of tool
        -- native, MCP and custom -- passes through the same governance point.
        The same point records an audit event for every MUTATING call, so a side
        effect is on record even if the turn later fails (session history is only
        saved after a successful turn; the audit row is written at tool time).

        Approval is enforced here too, before anything else: _approval_gate()
        consults the installed ApprovalPolicy (chat/reflect/sub-agent turns
        get the interactive one, cron runs get a grant-based one). A denied
        call returns an error string and never reaches plugins or _dispatch.
        """
        denied = self._approval_gate(name, args)
        if denied is not None:
            return denied
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

    def _approval_gate(self, name: str, args: dict[str, Any]) -> str | None:
        """The single approval choke point.

        Every model-requested tool call — serial branch, parallel branch,
        reflect(), sub-agents, cron turns — funnels through here, because
        they all funnel through run(). Returns the denial message when the
        call may not run, ``None`` when it may.

        Ordering notes, each deliberate:

        - The gate runs BEFORE plugin hooks. A denied call must not trigger
          plugin side effects (before-hooks can rewrite args or touch the
          network); operator consent is the outermost layer.
        - The reentrancy guard short-circuits tools the approval machinery
          itself invokes (ask_user). Without it, asking for approval would
          ask for approval to ask, forever.
        - Denials are NOT audit-logged: the audit trail records side effects,
          and a denied call has none. (Cron denials are recorded loudly on
          the grant policy instead, and surface in the job's last_status.)
        - A policy that raises is treated as "deny" — fail closed, never
          fail open.
        - No installed policy at all is also fail-closed: mutating tools
          (Write/Network/Install/Destructive) are denied, pure reads still
          run. See ``_no_policy_fallback``.
        """
        name = str(name or "")
        if not name:
            return None  # _dispatch reports unknown/empty names itself
        if getattr(self._approval_tls, "in_policy", False):
            # Inside policy.decide(): this is the machinery's own call
            # (ask_user). It must not re-enter the gate.
            return None
        policy = self.approval_policy
        if policy is None:
            # Fail-closed fallback (hardening, verdict owner): tidak ada
            # enforcement context yang terpasang — mis. reflect() dipanggil
            # tanpa send() dulu. Tool mutasi di-DENY; read murni tetap jalan
            # (dibutuhkan untuk introspeksi dan tidak punya efek samping).
            # Turn produksi selalu memasang policy di send(); unit test
            # memasangnya eksplisit bila menguji tool mutasi.
            return self._no_policy_fallback(name)
        safe_args = args if isinstance(args, dict) else {}
        self._approval_tls.in_policy = True
        try:
            verdict = policy.decide(self, name, safe_args)
        except Exception:
            verdict = "deny"  # fail closed
        finally:
            self._approval_tls.in_policy = False
        decision = approvals.parse_verdict(verdict)
        if decision == "deny":
            return _approval_denied_message(name)
        if decision == "session":
            # Grants-aware (spawn_worker / apply_skill_proposal /
            # rollback_skill_change): the session allow is recorded against
            # the exact thing the operator approved (see _spawn_grants_key);
            # "" for every other tool = unchanged.
            approvals.grant_session_allow(
                self.identity, name, _spawn_grants_key(name, safe_args)
            )
        return None

    #: Kelas risiko yang dianggap "mutasi" oleh fallback tanpa policy:
    #: apa pun yang bisa mengubah state (lokal maupun eksternal) di-deny.
    _NO_POLICY_MUTATING = frozenset(
        {ToolRisk.WRITE, ToolRisk.NETWORK, ToolRisk.INSTALL, ToolRisk.DESTRUCTIVE}
    )

    def _no_policy_fallback(self, name: str) -> str | None:
        """Deny-all fallback saat tidak ada approval policy yang terpasang.

        Fail-closed, bukan fail-open: absennya policy adalah kondisi yang
        tidak seharusnya terjadi di produksi (send() selalu memasang satu),
        jadi satu-satunya respons aman adalah menolak tool yang bisa
        bermutasi. Read murni tetap diizinkan — introspeksi (list tool,
        baca file, web search) tidak punya efek samping dan sering
        dibutuhkan justru untuk mendiagnosis kenapa policy tidak ada.

        Nama yang tidak terdaftar di mana pun dilewatkan (``None``) supaya
        ``_dispatch`` melaporkannya sebagai error seperti biasa — tidak ada
        yang perlu di-approve dari tool yang tidak ada.
        """
        definition = next(
            (item for item in self._native_defs if item.name == name), None
        )
        if definition is not None:
            risk = definition.risk
        else:
            non_native = self._non_native_risk(name)
            if non_native is None:
                return None
            risk = non_native[0]
        if risk in self._NO_POLICY_MUTATING:
            return _approval_denied_message(name)
        return None

    def ask_operator(self, question: str, options: object = None) -> str:
        """Ask the operator through the ask_user tool, with picker rendering.

        The single seam for every question the machinery asks itself (tool
        approvals, cron capability grants): when the installed policy carries
        an ``on_tool`` renderer (interactive turns), the Telegram picker /
        CLI prompt renders exactly like a model-initiated ask_user. Returns
        the raw verdict string; callers interpret it with
        ``approvals.parse_verdict``.

        ask_user is READ-risk, so this never needs approval itself — and the
        reentrancy guard in _approval_gate would short-circuit it anyway.
        """
        args = {
            "question": question,
            "options": list(options) if options else ["Allow", "Deny"],
        }
        renderer = getattr(self.approval_policy, "on_tool", None)
        if callable(renderer):
            renderer("ask_user", args)
        return self.run("ask_user", args)

    def risk_of(self, name: str) -> str | None:
        """Risk class of a native tool, or ``None`` for anything else.

        Read-only accessor over the same definitions approval_question uses;
        it never alters the decision logic (that stays Worker A's).
        """
        definition = next(
            (item for item in self._native_defs if item.name == name), None
        )
        return definition.risk if definition is not None else None

    def writes_outside_workspace(self, name: str, args: dict[str, Any]) -> bool:
        """Public delegate of the workspace-escape check approval_question uses."""
        return self._writes_outside_workspace(name, args)

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
