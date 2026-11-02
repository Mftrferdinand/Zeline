"""Private bounded inert active-history snapshots, independent of SQLite.

One atomic snapshot per identity (not an archive). No code or tools are replayed.
A successful later commit also replaces this snapshot, so failed cleanup cannot
resurrect an older turn while SQLite is offline. Reset uses an empty snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

MAX_RECOVERY_BYTES = 2_000_000
MAX_RECOVERY_IDENTITIES = 256


def _supports_private_snapshots() -> bool:
    # POSIX modes and directory fsync do not prove Windows ACL privacy.
    return os.name == 'posix'


def failure_category(exc: Exception) -> str:
    """Only allowlisted labels; never exception text or arbitrary class names."""
    import sqlite3
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, 'sqlite_errorcode', 0) or 0
        return {sqlite3.SQLITE_BUSY: 'sqlite_busy', sqlite3.SQLITE_LOCKED: 'sqlite_locked',
                sqlite3.SQLITE_FULL: 'storage_full', sqlite3.SQLITE_READONLY: 'read_only',
                sqlite3.SQLITE_CORRUPT: 'sqlite_corrupt', sqlite3.SQLITE_NOTADB: 'sqlite_invalid',
                sqlite3.SQLITE_CONSTRAINT: 'sqlite_constraint'}.get(code & 255, 'sqlite_error')
    if isinstance(exc, OSError):
        import errno
        return {errno.ENOSPC: 'storage_full', errno.EACCES: 'permission_denied',
                errno.EPERM: 'permission_denied'}.get(exc.errno or 0, 'storage_error')
    if isinstance(exc, (ValueError, TypeError)):
        return 'invalid_snapshot'
    return 'persistence_error'


class RecoverySnapshots:
    def __init__(self, database: Path):
        self.root = database.with_name(database.name + '.recovery')

    def _path(self, identity: str) -> Path:
        return self.root / (hashlib.sha256(identity.encode()).hexdigest() + '.json')

    def _directory(self, *, create=False):
        if create and not _supports_private_snapshots():
            raise OSError('Private recovery unavailable: unsupported platform')
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            info = self.root.lstat()
        except FileNotFoundError:
            return False
        if not _supports_private_snapshots():
            # Existing journals must not be silently ignored: they may hold
            # newer side effects than the primary database.
            raise OSError('Private recovery unavailable: unsupported platform')
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('unsafe recovery directory')
        return True

    def load(self, identity: str):
        if not self._directory():
            return None
        path = self._path(identity)
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise OSError('unsafe recovery file')
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                raise OSError('recovery file changed during open')
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError('unsafe recovery file')
            raw = stream.read(MAX_RECOVERY_BYTES + 1)
        if len(raw) > MAX_RECOVERY_BYTES:
            raise ValueError('recovery size limit')
        record = json.loads(raw)
        if (record.get('version') != 1 or not isinstance(record.get('messages'), list)
                or not all(isinstance(m, dict) for m in record['messages'])
                or not isinstance(record.get('title'), (str, type(None)))
                or not isinstance(record.get('token'), str)):
            raise ValueError('invalid recovery snapshot')
        return record

    def save(self, identity: str, messages: list, title: str | None, token: str):
        raw = json.dumps({'version': 1, 'messages': messages, 'title': title,
                          'token': token}, ensure_ascii=False).encode('utf-8')
        if len(raw) > MAX_RECOVERY_BYTES:
            raise ValueError('recovery size limit')
        self._directory(create=True)
        path = self._path(identity)
        existing = path.exists()
        count = 0
        for _entry in self.root.iterdir():
            count += 1
            if count > MAX_RECOVERY_IDENTITIES or (count == MAX_RECOVERY_IDENTITIES and not existing):
                raise ValueError('recovery identity limit')
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
