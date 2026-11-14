"""Portable lock/durability branches without pretending to run a Windows host."""
import os
import sys
import types
from unittest.mock import patch


def test_windows_lock_branch_does_not_require_fcntl(tmp_path, monkeypatch):
    from zeline import memory_markdown as md
    paths = {n: tmp_path / n for n in ('MEMORY.md', 'USER.md')}
    calls = []
    windows = types.ModuleType('msvcrt')
    windows.LK_NBLCK = 2
    windows.LK_UNLCK = 0
    def locking(fd, operation, length):
        assert length == 1
        # Windows permits locking a byte beyond EOF, before initialization.
        calls.append(operation)
    windows.locking = locking
    monkeypatch.setattr(md, 'secure', lambda p: p)
    monkeypatch.setattr(md, 'recover', lambda p: None)
    with patch.dict(sys.modules, {'fcntl': None, 'msvcrt': windows}), patch.object(os, 'name', 'nt'):
        with md.locked(paths, 1):
            pass
    assert calls == [windows.LK_NBLCK, windows.LK_UNLCK]


def test_windows_empty_lock_initialization_waits_for_byte_lock(tmp_path, monkeypatch):
    """A peer can lock byte zero while this opener still sees an empty file."""
    import errno
    from zeline import memory_markdown as md

    paths = {n: tmp_path / n for n in ('MEMORY.md', 'USER.md')}
    windows = types.ModuleType('msvcrt')
    windows.LK_NBLCK = 2
    windows.LK_UNLCK = 0
    acquired = False
    attempts = 0
    real_write = os.write

    def locking(fd, operation, length):
        nonlocal acquired, attempts
        assert length == 1
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
        if operation == windows.LK_NBLCK:
            attempts += 1
            if attempts == 1:
                raise PermissionError(errno.EACCES, 'peer holds byte zero')
            acquired = True
        else:
            acquired = False

    def mandatory_write(fd, data):
        if not acquired:
            raise PermissionError(errno.EACCES, 'peer holds byte zero')
        return real_write(fd, data)

    windows.locking = locking
    monkeypatch.setattr(md, 'secure', lambda p: p)
    monkeypatch.setattr(md, 'recover', lambda p: None)
    with patch.dict(sys.modules, {'fcntl': None, 'msvcrt': windows}), \
            patch.object(os, 'name', 'nt'), patch.object(os, 'write', mandatory_write):
        with md.locked(paths, 1):
            assert acquired
            assert (tmp_path / '.lock').stat().st_size == 1
    assert attempts == 2
    assert not acquired
    assert (tmp_path / '.lock').read_bytes() == b'\0'


def test_windows_directory_sync_does_not_open_directory(tmp_path):
    from zeline import memory_markdown as md
    with patch.object(os, 'name', 'nt'), patch.object(os, 'open', side_effect=AssertionError('not portable on Windows')):
        md.sync_directory(tmp_path)
