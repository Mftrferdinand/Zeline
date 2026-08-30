"""File checkpoints: snapshot before a write, restore when the agent gets it wrong.

OpenCode has `/undo` and `/redo`; Hermes has `hermes checkpoints`. The value is
a safety net — the agent overwrites the wrong file or mangles a function, and the
operator wants the previous bytes back without hunting for a backup.

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

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from zeline import config

# Files above this are not snapshotted: the copy cost stops being worth it and
# a huge binary is rarely what an operator wants to undo.
MAX_SNAPSHOT_BYTES = 5_000_000

# Keep at most this many snapshots; the oldest are pruned first.
MAX_SNAPSHOTS = 200


def enabled() -> bool:
    return bool(getattr(config, "CHECKPOINTS", True))


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
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _save_index(entries: list[dict[str, Any]]) -> None:
    _ensure_root()
    temp = _index_path().with_suffix(".json.tmp")
    temp.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(_index_path())
    try:
        os.chmod(_index_path(), 0o600)
    except OSError:
        pass


def _prune(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the oldest entries past the cap, deleting their blobs."""
    if len(entries) <= MAX_SNAPSHOTS:
        return entries
    entries.sort(key=lambda item: float(item.get("ts", 0)))
    excess = len(entries) - MAX_SNAPSHOTS
    for stale in entries[:excess]:
        blob = _root() / str(stale.get("blob", ""))
        try:
            if blob.is_file():
                blob.unlink()
        except OSError:
            pass
    return entries[excess:]


def snapshot(path: str | Path, reason: str = "write") -> str | None:
    """Copy a file's current bytes aside. Returns the checkpoint id, or None.

    None means no checkpoint exists — because the file is new, the feature is
    off, the file is too large, or the copy failed. Callers must treat None as
    "no safety net", never as an error to abort on.
    """
    if not enabled():
        return None
    target = Path(path)
    try:
        if not target.is_file():
            # A brand-new file has no previous content to restore.
            return None
        size = target.stat().st_size
    except OSError:
        return None
    if size > MAX_SNAPSHOT_BYTES:
        return None

    try:
        _ensure_root()
        moment = time.time()
        digest = hashlib.sha256(f"{target}:{moment}".encode()).hexdigest()[:16]
        checkpoint_id = f"{int(moment)}-{digest[:8]}"
        blob_name = f"{checkpoint_id}.blob"
        shutil.copy2(target, _root() / blob_name)
        try:
            os.chmod(_root() / blob_name, 0o600)
        except OSError:
            pass
        entries = _load_index()
        entries.append({
            "id": checkpoint_id,
            "blob": blob_name,
            "path": str(target),
            "size": int(size),
            "reason": str(reason)[:40],
            "ts": moment,
        })
        _save_index(_prune(entries))
        return checkpoint_id
    except (OSError, shutil.Error):
        # Never let a failed snapshot block the write it was protecting.
        return None


def list_checkpoints(path: str | Path | None = None, limit: int = 50) -> list[dict[str, Any]]:
    entries = _load_index()
    if path is not None:
        wanted = str(Path(path))
        entries = [item for item in entries if str(item.get("path")) == wanted]
    entries.sort(key=lambda item: float(item.get("ts", 0)), reverse=True)
    return entries[: max(1, limit)]


def find(checkpoint_id: str) -> dict[str, Any] | None:
    for item in _load_index():
        if str(item.get("id")) == checkpoint_id:
            return item
    return None


def restore(checkpoint_id: str) -> tuple[bool, str]:
    """Put a snapshot's bytes back. Snapshots the current file first."""
    entry = find(checkpoint_id)
    if entry is None:
        return False, f"no checkpoint with id {checkpoint_id}"
    blob = _root() / str(entry.get("blob", ""))
    if not blob.is_file():
        return False, f"checkpoint {checkpoint_id} has no stored content"
    target = Path(str(entry.get("path", "")))
    if not str(target):
        return False, f"checkpoint {checkpoint_id} has no target path"
    # Make the undo undoable before touching anything.
    snapshot(target, reason="pre-restore")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(blob, target)
    except (OSError, shutil.Error) as exc:
        return False, f"could not restore {target}: {exc.__class__.__name__}"
    return True, f"restored {target} from checkpoint {checkpoint_id}"


def clear() -> int:
    """Delete every checkpoint. Returns how many were removed."""
    entries = _load_index()
    removed = 0
    for item in entries:
        blob = _root() / str(item.get("blob", ""))
        try:
            if blob.is_file():
                blob.unlink()
                removed += 1
        except OSError:
            pass
    try:
        if _index_path().is_file():
            _index_path().unlink()
    except OSError:
        pass
    return removed


def diff_preview(checkpoint_id: str, max_lines: int = 40) -> str:
    """Unified diff between a checkpoint and the file's current content."""
    import difflib

    entry = find(checkpoint_id)
    if entry is None:
        return f"no checkpoint with id {checkpoint_id}"
    blob = _root() / str(entry.get("blob", ""))
    target = Path(str(entry.get("path", "")))
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
