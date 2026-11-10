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


def _windows_private(path, *, create=False):
    """Set/verify a protected owner-only DACL; never infer Windows privacy from chmod."""
    import ctypes
    from ctypes import wintypes as wt
    if not hasattr(ctypes, 'WinDLL'):
        return False
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    signatures = (
        (security.OpenProcessToken, [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]),
        (security.GetTokenInformation, [wt.HANDLE, ctypes.c_int, pointer, wt.DWORD, ctypes.POINTER(wt.DWORD)]),
        (security.ConvertSidToStringSidW, [pointer, ctypes.POINTER(wt.LPWSTR)]),
        (security.ConvertStringSecurityDescriptorToSecurityDescriptorW,
         [wt.LPCWSTR, wt.DWORD, ctypes.POINTER(pointer), ctypes.POINTER(wt.DWORD)]),
        (security.SetFileSecurityW, [wt.LPCWSTR, wt.DWORD, pointer]),
        (security.GetFileSecurityW, [wt.LPCWSTR, wt.DWORD, pointer, wt.DWORD, ctypes.POINTER(wt.DWORD)]),
        (security.ConvertSecurityDescriptorToStringSecurityDescriptorW,
         [pointer, wt.DWORD, wt.DWORD, ctypes.POINTER(wt.LPWSTR), ctypes.POINTER(wt.DWORD)]),
    )
    for function, arguments in signatures:
        function.argtypes, function.restype = arguments, wt.BOOL
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [pointer], pointer
    token, size = wt.HANDLE(), wt.DWORD()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        user = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, user, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = wt.LPWSTR()
        if not security.ConvertSidToStringSidW(ctypes.cast(user, ctypes.POINTER(pointer))[0], ctypes.byref(sid)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            principal = {'S-1-5-18': 'SY', 'S-1-5-19': 'LS', 'S-1-5-20': 'NS'}.get(sid.value, sid.value)
            expected = 'D:P(A;;FA;;;' + principal + ')'
        finally:
            kernel.LocalFree(sid)
    finally:
        kernel.CloseHandle(token)
    if create:
        descriptor = pointer()
        if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(expected, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not security.SetFileSecurityW(str(path), 0x80000004, descriptor):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.LocalFree(descriptor)
    security.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(size))
    descriptor = ctypes.create_string_buffer(size.value)
    if not security.GetFileSecurityW(str(path), 4, descriptor, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    actual = wt.LPWSTR()
    if not security.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(actual), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # Auto-inheritance bookkeeping does not grant access; require protection
        # and exactly one explicit full-access ACE for the current principal.
        header, separator, entries = actual.value.partition('(')
        return header in {'D:P', 'D:PAI', 'D:PAR', 'D:PARAI'} and separator + entries == expected[3:]
    finally:
        kernel.LocalFree(actual)


def _private(path, info, *, create=False):
    if os.name == 'nt':
        if getattr(info, 'st_file_attributes', 0) & 0x400:
            return False  # Reject junctions and other reparse points too.
        return _windows_private(path, create=create)
    return not (info.st_mode & 0o077)


def failure_category(exc: Exception) -> str:
    """Only allowlisted labels; never exception text or arbitrary class names."""
    import sqlite3
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, 'sqlite_errorcode', 0) or 0
        # SQLite's stable primary result codes predate Python exposing named constants.
        return {5: 'sqlite_busy', 6: 'sqlite_locked', 13: 'storage_full', 8: 'read_only',
                11: 'sqlite_corrupt', 26: 'sqlite_invalid',
                19: 'sqlite_constraint'}.get(code & 255, 'sqlite_error')
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
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            info = self.root.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(info.st_mode) or not _private(self.root, info, create=create):
            raise ValueError('unsafe recovery directory')
        return True

    def load(self, identity: str):
        if not self._directory():
            return None
        path = self._path(identity)
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or not _private(path, before):
                raise OSError('unsafe recovery file')
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                raise OSError('recovery file changed during open')
            if not stat.S_ISREG(info.st_mode) or not _private(path, info):
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
                if not _private(temporary, os.fstat(stream.fileno()), create=True):
                    raise ValueError('unsafe recovery staging file')
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            # Windows cannot open directories with the POSIX CRT. File fsync and
            # atomic replacement remain required; directory fsync is POSIX-only.
            if os.name == 'posix':
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
