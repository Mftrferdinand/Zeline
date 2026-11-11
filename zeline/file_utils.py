"""Workspace file utilities behind the archive_file / checksum_file /
find_duplicate_files tools: stdlib-only, workspace-gated, bounded output."""
from __future__ import annotations

import hashlib
import os
import tarfile
import zipfile
from pathlib import Path

#: Read-shaped output cap, same budget the other file tools use.
MAX_OUTPUT_BYTES = 50_000
#: Safety caps: an archive must not be a zip-bomb or a tool-call that lists
#: hundreds of thousands of entries into a phone-sized chat window.
MAX_ARCHIVE_ENTRIES = 2_000
MAX_LISTED_ENTRIES = 200
MAX_DUPLICATE_GROUPS = 50
_HASH_ALGORITHMS = ("sha256", "sha1", "md5")
_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar")


def _fmt_size(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _truncate(text: str, marker: str = "\n[truncated: output limit reached]") -> str:
    data = text.encode()
    if len(data) <= MAX_OUTPUT_BYTES:
        return text
    return data[:MAX_OUTPUT_BYTES - len(marker)].decode("utf-8", errors="ignore") + marker


def _iter_workspace_files(root: Path):
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__", "venv"})
        for name in sorted(files):
            if not name.startswith("."):
                yield Path(directory) / name


# ---------------------------------------------------------------- archive ---

def archive_file(action: str, source: str, target: str, workspace: Path) -> str:
    """Create or extract a .zip / .tar.gz archive inside the workspace."""
    verb = (action or "").strip().lower()
    if verb not in ("create", "extract"):
        return "ERROR archive: action must be 'create' or 'extract'."

    from zeline.tools import _resolve_workspace_path

    try:
        src = _resolve_workspace_path(source, workspace)
        dst = _resolve_workspace_path(target, workspace)
    except ValueError as exc:
        return f"ERROR archive: {exc}"

    if verb == "extract":
        if not src.is_file():
            return f"ERROR archive: not a file or not found: {source}"
        lower = src.name.lower()
        if lower.endswith(".zip"):
            opener = zipfile.ZipFile
        elif lower.endswith((".tar.gz", ".tgz", ".tar")):
            opener = tarfile.open
        else:
            return "ERROR archive: extract supports .zip, .tar.gz, .tgz and .tar (see the file extension)."
        dst.mkdir(parents=True, exist_ok=True)
        try:
            if opener is zipfile.ZipFile:
                with zipfile.ZipFile(src) as archive:
                    names = archive.namelist()
                    if len(names) > MAX_ARCHIVE_ENTRIES:
                        return f"ERROR archive: archive holds {len(names)} entries (max {MAX_ARCHIVE_ENTRIES})."
                    for info in archive.infolist():
                        # A member path that escapes the destination would turn
                        # extraction into an arbitrary write (Zip Slip).
                        resolved = (dst / info.filename).resolve()
                        resolved.relative_to(dst.resolve())
                    archive.extractall(dst)
            else:
                with tarfile.open(src) as archive:
                    members = archive.getmembers()
                    if len(members) > MAX_ARCHIVE_ENTRIES:
                        return f"ERROR archive: archive holds {len(members)} entries (max {MAX_ARCHIVE_ENTRIES})."
                    for member in members:
                        resolved = (dst / member.name).resolve()
                        resolved.relative_to(dst.resolve())
                    archive.extractall(dst, filter="data")
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
            return f"ERROR archive: {exc}"
        extracted = sum(1 for _ in _iter_workspace_files(dst))
        return f"Extracted {extracted} file(s) from {src.name} into {target}/"

    # create
    if not src.exists():
        return f"ERROR archive: not a file or not found: {source}"
    lower = dst.name.lower()
    if lower.endswith(".zip"):
        files = [src] if src.is_file() else sorted(_iter_workspace_files(src))
        if len(files) > MAX_ARCHIVE_ENTRIES:
            return f"ERROR archive: source holds {len(files)} files (max {MAX_ARCHIVE_ENTRIES})."
        base = src if src.is_dir() else src.parent
        try:
            with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as archive:
                for f in files:
                    archive.write(f, str(f.relative_to(base)))
        except OSError as exc:
            return f"ERROR archive: {exc}"
    elif lower.endswith((".tar.gz", ".tgz", ".tar")):
        mode = "w:gz" if lower.endswith((".tar.gz", ".tgz")) else "w"
        files = [src] if src.is_file() else sorted(_iter_workspace_files(src))
        if len(files) > MAX_ARCHIVE_ENTRIES:
            return f"ERROR archive: source holds {len(files)} files (max {MAX_ARCHIVE_ENTRIES})."
        base = src if src.is_dir() else src.parent
        try:
            with tarfile.open(dst, mode) as archive:
                for f in files:
                    archive.add(f, str(f.relative_to(base)), recursive=False)
        except OSError as exc:
            return f"ERROR archive: {exc}"
    else:
        return "ERROR archive: target must end with .zip, .tar.gz, .tgz or .tar."
    return f"Created {dst.name} ({_fmt_size(dst.stat().st_size)}, {len(files)} file(s))."


# --------------------------------------------------------------- checksum ---

def checksum_file(path: str, workspace: Path, algorithm: str = "sha256", verify: str = "") -> str:
    """Hash a workspace file, or compare it against an expected digest."""
    algo = (algorithm or "sha256").strip().lower()
    if algo not in _HASH_ALGORITHMS:
        return f"ERROR checksum: algorithm must be one of {', '.join(_HASH_ALGORITHMS)}."

    from zeline.tools import _resolve_workspace_path

    try:
        target = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR checksum: {exc}"
    if not target.is_file():
        return f"ERROR checksum: not a file or not found: {path}"
    digest = hashlib.new(algo)
    try:
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        return f"ERROR checksum: {exc}"
    value = digest.hexdigest()
    if verify:
        expected = verify.strip().lower().replace(" ", "")
        if expected == value:
            return f"OK {algo}: {value} matches the expected digest."
        return f"MISMATCH {algo}: file is {value}, expected {expected}."
    return f"{algo}  {value}  {path}"


# ------------------------------------------------------------- duplicates ---

def find_duplicate_files(workspace: Path, pattern: str = "*") -> str:
    """Group identical files by size then full hash; report the groups."""
    by_size: dict[int, list[Path]] = {}
    total_scanned = 0
    for path in _iter_workspace_files(workspace):
        from fnmatch import fnmatch
        if pattern != "*" and not (fnmatch(path.name, pattern) or fnmatch(str(path.relative_to(workspace)), pattern)):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0:
            continue
        by_size.setdefault(size, []).append(path)
        total_scanned += 1

    lines: list[str] = []
    wasted = 0
    groups = 0
    for size in sorted((s for s, paths in by_size.items() if len(paths) > 1), reverse=True):
        by_hash: dict[str, list[Path]] = {}
        for path in by_size[size]:
            digest = hashlib.sha256()
            try:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b""):
                        digest.update(chunk)
            except OSError:
                continue
            by_hash.setdefault(digest.hexdigest(), []).append(path)
        for paths in by_hash.values():
            if len(paths) < 2:
                continue
            groups += 1
            wasted += size * (len(paths) - 1)
            lines.append(f"{_fmt_size(size)} x{len(paths)}:")
            for p in paths[:MAX_LISTED_ENTRIES]:
                lines.append(f"  {p.relative_to(workspace)}")
            if groups >= MAX_DUPLICATE_GROUPS:
                lines.append(f"(stopped at {MAX_DUPLICATE_GROUPS} groups)")
                break
        if groups >= MAX_DUPLICATE_GROUPS:
            break
    if not lines:
        return f"No duplicate files among {total_scanned} scanned."
    lines.append(f"\n{groups} duplicate group(s), {_fmt_size(wasted)} reclaimable by keeping one copy each.")
    return _truncate("\n".join(lines))
