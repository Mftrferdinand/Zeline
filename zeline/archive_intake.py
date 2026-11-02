"""Bounded ZIP project previews; archive contents are never executed or extracted."""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import struct
import tempfile
import threading
import zipfile
from typing import Callable

_IGNORED_PARTS = {'node_modules', '.git', '.venv', 'venv', '__pycache__', '.cache'}
_MANIFESTS = {'package.json', 'pyproject.toml', 'requirements.txt', 'cargo.toml', 'go.mod',
              'dockerfile', 'compose.yaml', 'compose.yml', 'setup.py', 'setup.cfg'}
_LOCKFILES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'cargo.lock'}
_CACHE_LOCK = threading.RLock()
CACHE_MAX_BYTES = 256 * 1024 * 1024
CACHE_IDENTITY_BYTES = 64 * 1024 * 1024
CACHE_MAX_FILES = 512


@contextmanager
def _cache_guard(root: Path):
    # The permanent lock database survives crashes; SQLite releases its OS lock.
    with _CACHE_LOCK:
        if root.is_symlink():
            raise ValueError('Unsafe attachment cache root')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        lockfile = root / '.cache-lock.sqlite3'
        if lockfile.is_symlink():
            raise ValueError('Unsafe attachment cache lock')
        try:
            with closing(sqlite3.connect(str(lockfile), timeout=1)) as connection:
                lockfile.chmod(0o600)
                connection.execute('BEGIN IMMEDIATE')
                try:
                    yield
                finally:
                    connection.rollback()
        except sqlite3.Error:
            raise OSError('Attachment cache is busy or unavailable') from None


def cache_document(root: Path, identity: str, filename: str, content: bytes) -> Path:
    """Atomically retain a bounded original in an identity-isolated private cache."""
    if len(content) > 20 * 1024 * 1024:
        raise ValueError('Attachment exceeds download budget')
    suffix = Path(filename).suffix.lower()
    suffix = suffix if re.fullmatch(r'\.[a-z0-9]{1,12}', suffix) else '.bin'
    identity_key = hashlib.sha256(identity.encode()).hexdigest()
    digest = hashlib.sha256(content).hexdigest()
    with _cache_guard(root):
        owner = root / identity_key
        if owner.is_symlink():
            raise ValueError('Unsafe attachment owner directory')
        if not owner.exists():
            directory_count = 0
            for directory in root.iterdir():
                if directory.is_dir() and not directory.is_symlink():
                    directory_count += 1
                    if directory_count >= CACHE_MAX_FILES:
                        raise ValueError('Attachment cache identity budget exceeded')
        owner.mkdir(mode=0o700, exist_ok=True)
        owner.chmod(0o700)
        target = owner / (digest + suffix)
        if target.is_symlink():
            raise ValueError('Unsafe attachment destination')
        if target.exists():
            if target.stat().st_size != len(content) or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('Existing attachment cache integrity mismatch')
            return target
        count, total, per_owner = 0, 0, 0
        for directory in root.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            for entry in directory.iterdir():
                if entry.is_symlink() or not entry.is_file():
                    continue
                count += 1
                size = entry.stat().st_size
                total += size
                if directory == owner:
                    per_owner += size
                if count >= CACHE_MAX_FILES or total + len(content) > CACHE_MAX_BYTES:
                    raise ValueError('Attachment cache budget exceeded')
        if per_owner + len(content) > CACHE_IDENTITY_BYTES or len(content) > CACHE_MAX_BYTES:
            raise ValueError('Attachment cache budget exceeded')
        descriptor, name = tempfile.mkstemp(prefix='.incoming-', dir=owner)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                temporary.chmod(0o600)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target


def _safe_name(entry: zipfile.ZipInfo) -> PurePosixPath | None:
    name = entry.filename.replace('\\', '/')
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or re.match(r'^[a-zA-Z]:', name)
            or any(ord(c) < 32 for c in name) or len(name) > 512
            or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
        return None
    return path


def _rank(entry: tuple[zipfile.ZipInfo, PurePosixPath]) -> tuple:
    path = entry[1]
    lower = path.name.lower()
    priority = 0 if lower in _MANIFESTS else 1 if lower.startswith(('readme', 'install')) else 2
    return priority, len(path.parts), path.as_posix().lower()


def _index_budget(content: bytes) -> bool:
    """Bound central-directory allocation before handing the bytes to ZipFile."""
    if len(content) > 20 * 1024 * 1024:
        return False
    end = content.rfind(b'PK\x05\x06', max(0, len(content) - 65557))
    if end < 0 or end + 22 > len(content):
        raise ValueError('Missing ZIP end record')
    _signature, disk, cd_disk, disk_entries, total, size, offset, comment = struct.unpack_from('<4s4H2LH', content, end)
    if end + 22 + comment != len(content):
        raise ValueError('Invalid ZIP end record')
    if disk or cd_disk or disk_entries != total:
        return False
    if total > 20000 or size > 2 * 1024 * 1024 or offset == 0xffffffff:
        return False
    start = end - size
    if start < 0 or offset > start:
        raise ValueError('Invalid ZIP directory bounds')
    position, actual = start, 0
    while position < end:
        if position + 46 > end or content[position:position + 4] != b'PK\x01\x02':
            raise ValueError('Invalid ZIP directory member')
        name, extra, note = struct.unpack_from('<3H', content, position + 28)
        position += 46 + name + extra + note
        actual += 1
        if actual > 20000:
            return False
    if position != end or actual != total:
        raise ValueError('ZIP directory count mismatch')
    return True


def preview_zip(content: bytes, is_text: Callable[[str], bool], *,
                max_files: int = 512, byte_budget: int = 256 * 1024) -> tuple[str | None, str | None]:
    try:
        if not _index_budget(content):
            return ('ZIP preview deferred: the archive index exceeds the bounded preview budget '
                    'or uses an unsupported multi-volume/ZIP64 directory. This is not a file-count '
                    'rejection at 512; keep the original for selective offline inspection. '
                    'No archive members were opened or executed.', None)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            candidates = []
            for entry in members:
                path = _safe_name(entry)
                if (path is None or set(path.parts) & _IGNORED_PARTS
                        or path.name.lower() in _LOCKFILES or path.name.lower().startswith('.env')
                        or (not is_text(path.name) and path.name.lower() not in _MANIFESTS
                            and path.name.lower() not in {'readme', 'license', 'makefile', 'procfile'})
                        or entry.file_size > byte_budget):
                    continue
                candidates.append((entry, path))
            candidates.sort(key=_rank)
            header = (
                f'ZIP project preview (partial; not a full audit). Total files: {len(members)}. '
                f'Eligible text files: {len(candidates)}. Dependencies, unsafe paths and binary files '
                'are not read. No files were executed or extracted.\n'
            )
            listing = '\n'.join(json.dumps(path.as_posix(), ensure_ascii=True)
                                for _entry, path in candidates[:40])[:3000]
            parts = [header, 'Selected project paths:\n' + listing]
            consumed = 0
            for entry, path in candidates[:max_files]:
                remaining = min(8192, byte_budget - consumed)
                if remaining <= 0 or sum(map(len, parts)) >= 15500:
                    break
                try:
                    with archive.open(entry) as source:
                        data = source.read(remaining)
                except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
                    continue
                consumed += len(data)
                text = data.decode('utf-8', errors='replace').replace('\x00', ' ').strip()
                if text:
                    marker = '\n[member preview truncated]' if entry.file_size > len(data) else ''
                    parts.append(f'--- {json.dumps(path.as_posix(), ensure_ascii=True)} ---\n{text}{marker}')
            return '\n\n'.join(parts)[:16000], None
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
        return None, 'ZIP is invalid or cannot be previewed safely.'
