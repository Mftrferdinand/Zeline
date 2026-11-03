"""File checkpoints: snapshot before a write, restore when the agent gets it wrong.

Other coding agents expose this as `/undo` / `/redo` or a checkpoints command.
The value is a safety net — the agent overwrites the wrong file or mangles a
function, and the operator wants the previous bytes back without hunting for a
backup.

Why full-content copies instead of git:

Zeline runs in whatever workspace the operator points it at, which is frequently
not a git repository (Termux home, a scratch directory, a mounted share). A
git-based checkpoint would silently do nothing there — worse than no feature,
because the operator would believe they were protected. Copies always work.

Guarantees:

- **Snapshotting never blocks a write.** If the snapshot fails (permissions,
  full disk, unreadable file), the write proceeds and the operator is told the
  checkpoint is missing. A safety net that refuses work is not a safety net.
- **Restore verifies before replacing.** The current file is itself snapshotted
  first, so an undo can be undone.
- **Bounded.** Per-file size limit, per-workspace count limit, oldest pruned.
  A checkpoint store that grows without limit becomes the problem it solves.
- **Private.** Snapshots hold source content, so the directory and files are
  0700/0600.
"""
from __future__ import annotations

import secrets
import json
import os
import shutil
import time
import tempfile
import sqlite3
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any

from zeline import config


def _mutating(fallback):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                with _transaction():
                    return function(*args, **kwargs)
            except (OSError, shutil.Error):
                return fallback
        return wrapped
    return decorate


_MUTEX = threading.RLock()
_LOCAL = threading.local()
_LOCK_TIMEOUT = 0.5


@contextmanager
def _transaction():
    """Bounded, reentrant thread/process exclusion for the entire mutation."""
    if not _MUTEX.acquire(timeout=_LOCK_TIMEOUT):
        raise OSError("checkpoint mutex busy")
    connection = None
    try:
        if not getattr(_LOCAL, "depth", 0):
            lock_path = _ensure_root() / "mutex.sqlite3"
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            connection = sqlite3.connect(lock_path, timeout=_LOCK_TIMEOUT)
            connection.execute("BEGIN IMMEDIATE")
        _LOCAL.depth = getattr(_LOCAL, "depth", 0) + 1
        try:
            yield
        finally:
            _LOCAL.depth -= 1
    except sqlite3.Error as exc:
        raise OSError("checkpoint mutex unavailable") from exc
    finally:
        try:
            if connection is not None:
                connection.close()
        except sqlite3.Error as exc:
            raise OSError("checkpoint mutex cleanup failed") from exc
        finally:
            _MUTEX.release()


# Files above this are not snapshotted: the copy cost stops being worth it and
# a huge binary is rarely what an operator wants to undo.
MAX_SNAPSHOT_BYTES = 5_000_000

# Keep at most this many snapshots; the oldest are pruned first.
MAX_SNAPSHOTS = 200


def enabled() -> bool:
    return bool(getattr(config, "CHECKPOINTS", True))


def _normalize(path: str | Path) -> Path:
    """One canonical spelling per file, so lookups match what was stored.

    The tool layer resolves paths before writing while a caller may not, and two
    platforms make the unresolved and resolved spellings genuinely different
    strings: Windows hands out 8.3 short names (``C:\\Users\\RUNNER~1\\...``)
    beside the long form, and macOS symlinks ``/var`` to ``/private/var``.
    Storing the raw string therefore made one file look like two, and a
    checkpoint taken by write_file could not be found by path. Resolve on both
    sides instead.
    """
    return Path(path).expanduser().resolve(strict=False)


def _within(target: Path, workspace: Path | None) -> bool:
    """True when ``target`` sits inside ``workspace``. None = no restriction.

    The store is deliberately global — one operator, one machine, every
    workspace — so `zeline undo` can reach a file whatever directory the
    operator is standing in. That is right for the operator at a shell and
    WRONG for the agent: its file tools are confined to one workspace, so
    letting it restore by id would hand it a write outside that boundary. Every
    agent-facing entry point passes a workspace and this is the gate.
    """
    if workspace is None:
        return True
    root = _normalize(workspace)
    return target == root or target.is_relative_to(root)


def format_age(ts: float) -> str:
    """Human age of a checkpoint: one spelling for the CLI, the tool, and chat.

    Lives here rather than in each surface because a checkpoint's age is the
    only thing an operator uses to recognise it ("the one from a minute ago"),
    and three surfaces rendering it three ways would make the same snapshot
    look like different snapshots.
    """
    try:
        seconds = max(0, int(time.time() - float(ts or 0)))
    except (TypeError, ValueError):
        return "unknown age"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _root() -> Path:
    return config.DATA_DIR / "checkpoints"


def _index_path() -> Path:
    return _root() / "index.json"


def _ensure_root() -> Path:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _load_index() -> list[dict[str, Any]]:
    try:
        raw = json.loads(_index_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _save_index(entries: list[dict[str, Any]]) -> None:
    root = _ensure_root()
    fd, name = tempfile.mkstemp(prefix="index-", suffix=".tmp", dir=root)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(entries, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, _index_path())
        # Windows does not support opening/fsyncing directories. On POSIX,
        # report durability failures, but never roll back a published index.
        if os.name != "nt":
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def _prune(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select the retained entries; publication must precede blob deletion."""
    if len(entries) <= MAX_SNAPSHOTS:
        return entries
    newest = entries[-1]
    # The appended checkpoint must remain undoable even after wall-clock rollback.
    entries.sort(key=lambda item: (item is newest, float(item.get("ts", 0))))
    excess = len(entries) - MAX_SNAPSHOTS
    return entries[excess:]


@_mutating(None)
def snapshot(path: str | Path, reason: str = "write") -> str | None:
    """Copy a file's current bytes aside. Returns the checkpoint id, or None.

    None means no checkpoint exists — because the file is new, the feature is
    off, the file is too large, or the copy failed. Callers must treat None as
    "no safety net", never as an error to abort on.
    """
    if not enabled():
        return None
    target = _normalize(path)
    try:
        if not target.is_file():
            # A brand-new file has no previous content to restore.
            return None
        size = target.stat().st_size
    except OSError:
        return None
    if size > MAX_SNAPSHOT_BYTES:
        return None

    blob_name = None
    try:
        _ensure_root()
        moment = time.time()
        # Wall-clock ticks are not unique (notably on Windows). A restore
        # snapshots the same path again and must never overwrite its source.
        checkpoint_id = f"{int(moment)}-{secrets.token_hex(16)}"
        blob_name = f"{checkpoint_id}.blob"
        shutil.copy2(target, _root() / blob_name)
        try:
            os.chmod(_root() / blob_name, 0o600)
        except OSError:
            pass
        with (_root() / blob_name).open("r+b") as stream:
            os.fsync(stream.fileno())
        entries = _load_index()
        entries.append({
            "id": checkpoint_id,
            "blob": blob_name,
            "path": str(target),
            "size": int(size),
            "reason": str(reason)[:40],
            "ts": moment,
        })
        retained = _prune(entries)
        _save_index(retained)
        live_blobs = {item.get('blob') for item in retained}
        for stale in entries:
            if stale.get('blob') not in live_blobs:
                try:
                    (_root() / str(stale.get('blob', ''))).unlink(missing_ok=True)
                except OSError:
                    pass  # Orphan cleanup can retry; never invalidate the live index.
        return checkpoint_id
    except (OSError, shutil.Error):
        # A directory fsync can fail AFTER replace. Only remove a blob when
        # a fresh, readable index proves it is not published. Uncertainty leaks
        # an orphan rather than destroying an operator's recovery bytes.
        if blob_name is not None:
            try:
                raw = json.loads(_index_path().read_text(encoding="utf-8"))
                if isinstance(raw, list) and all(isinstance(x, dict) for x in raw):
                    if not any(x.get("blob") == blob_name for x in raw):
                        (_root() / blob_name).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
        # Never let a failed snapshot block the write it was protecting.
        return None


def list_checkpoints(
    path: str | Path | None = None,
    limit: int = 50,
    workspace: str | Path | None = None,
) -> list[dict[str, Any]]:
    try:
        entries = _load_index()
    except OSError:
        return []
    if path is not None:
        wanted = str(_normalize(path))
        entries = [item for item in entries if str(item.get("path")) == wanted]
    if workspace is not None:
        root = _normalize(workspace)
        entries = [
            item for item in entries
            if _within(_normalize(str(item.get("path", ""))), root)
        ]
    entries.sort(key=lambda item: float(item.get("ts", 0)), reverse=True)
    return entries[: max(1, limit)]


def find(checkpoint_id: str) -> dict[str, Any] | None:
    try:
        entries = _load_index()
    except OSError:
        return None
    for item in entries:
        if str(item.get("id")) == checkpoint_id:
            return item
    return None


@_mutating((False, "checkpoint store unavailable"))
def restore(checkpoint_id: str, workspace: str | Path | None = None) -> tuple[bool, str]:
    """Put a snapshot's bytes back. Snapshots the current file first."""
    entry = find(checkpoint_id)
    if entry is None:
        return False, f"no checkpoint with id {checkpoint_id}"
    blob = _root() / str(entry.get("blob", ""))
    if not blob.is_file():
        return False, f"checkpoint {checkpoint_id} has no stored content"
    target = _normalize(str(entry.get("path", "")))
    if not str(entry.get("path", "")):
        return False, f"checkpoint {checkpoint_id} has no target path"
    if not _within(target, None if workspace is None else _normalize(workspace)):
        # Reported as "not found" on purpose: naming the outside path would let
        # a caller enumerate files in other workspaces one id at a time.
        return False, f"no checkpoint with id {checkpoint_id} in this workspace"
    try:
        # A pre-restore snapshot can prune this blob when the store is full.
        # Stage it privately outside the index before snapshotting the target.
        with tempfile.TemporaryDirectory(prefix="restore-", dir=_root()) as staging:
            source = Path(staging) / "content.blob"
            shutil.copy2(blob, source)
            if target.exists() and snapshot(target, reason="pre-restore") is None:
                return False, "could not preserve current bytes before restore"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    except (OSError, shutil.Error) as exc:
        return False, f"could not restore {target}: {exc.__class__.__name__}"
    return True, f"restored {target} from checkpoint {checkpoint_id}"


@_mutating(0)
def clear() -> int:
    """Delete every checkpoint. Returns how many were removed."""
    entries = _load_index()
    try:
        _save_index([])
    except OSError:
        return 0
    removed = 0
    for item in entries:
        blob = _root() / str(item.get("blob", ""))
        try:
            if blob.is_file():
                blob.unlink()
                removed += 1
        except OSError:
            pass
    return removed



def diff_preview(
    checkpoint_id: str,
    max_lines: int = 40,
    workspace: str | Path | None = None,
) -> str:
    """Unified diff between a checkpoint and the file's current content."""
    import difflib

    entry = find(checkpoint_id)
    if entry is None:
        return f"no checkpoint with id {checkpoint_id}"
    blob = _root() / str(entry.get("blob", ""))
    target = _normalize(str(entry.get("path", "")))
    if not _within(target, None if workspace is None else _normalize(workspace)):
        # A diff is a read of two files. Refusing it keeps the tool from
        # printing the contents of a file outside the agent's workspace.
        return f"no checkpoint with id {checkpoint_id} in this workspace"
    try:
        old = blob.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        return f"checkpoint {checkpoint_id} content is unreadable"
    try:
        new = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        new = []
    lines = list(difflib.unified_diff(
        old, new,
        fromfile=f"checkpoint {checkpoint_id}",
        tofile=str(target),
        n=2,
    ))
    if not lines:
        return "(no differences)"
    text = "".join(lines[: max_lines * 2])
    if len(lines) > max_lines * 2:
        text += f"\n... [{len(lines) - max_lines * 2} more diff lines]"
    return text
