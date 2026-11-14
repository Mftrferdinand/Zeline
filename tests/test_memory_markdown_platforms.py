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
        assert os.fstat(fd).st_size >= 1
        calls.append(operation)
    windows.locking = locking
    monkeypatch.setattr(md, 'secure', lambda p: p)
    monkeypatch.setattr(md, 'recover', lambda p: None)
    with patch.dict(sys.modules, {'fcntl': None, 'msvcrt': windows}), patch.object(os, 'name', 'nt'):
        with md.locked(paths, 1):
            pass
    assert calls == [windows.LK_NBLCK, windows.LK_UNLCK]


def test_windows_directory_sync_does_not_open_directory(tmp_path):
    from zeline import memory_markdown as md
    with patch.object(os, 'name', 'nt'), patch.object(os, 'open', side_effect=AssertionError('not portable on Windows')):
        md.sync_directory(tmp_path)
