"""Windows native-search pipe ABI and nonblocking polling contracts."""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from zeline import file_io


def make_reader(available=3, error=0):
    stream = SimpleNamespace(fileno=lambda: 7)
    def peek(handle, buffer, size, read, total, remaining):
        total._obj.value = available
        return not error
    kernel = SimpleNamespace(PeekNamedPipe=Mock(side_effect=peek))
    loader = Mock(return_value=kernel)
    contexts = [patch.dict('sys.modules', {'msvcrt': SimpleNamespace(get_osfhandle=lambda fd: 0x123456789ABC)}),
                patch.object(ctypes, 'WinDLL', loader, create=True),
                patch.object(ctypes, 'get_last_error', return_value=error, create=True)]
    return stream, kernel, contexts


def test_windows_pipe_uses_pointer_sized_handle_and_only_available_bytes():
    stream, kernel, contexts = make_reader()
    with contexts[0], contexts[1], contexts[2], patch.object(file_io.os, 'read', return_value=b'abc') as read:
        reader = file_io._windows_pipe_reader(stream)
        assert reader() == b'abc'
        read.assert_called_once_with(7, 3)
        assert kernel.PeekNamedPipe.call_args.args[0] == 0x123456789ABC
        assert kernel.PeekNamedPipe.argtypes[0] is wintypes.HANDLE
        assert kernel.PeekNamedPipe.restype is wintypes.BOOL


def test_windows_pipe_empty_is_pending_not_eof():
    stream, kernel, contexts = make_reader(available=0)
    with contexts[0], contexts[1], contexts[2], patch('time.sleep') as sleep:
        assert file_io._windows_pipe_reader(stream)() is None
        sleep.assert_called_once_with(0.05)


def test_windows_pipe_broken_is_eof_and_other_errors_propagate():
    for code in (109, 233):
        stream, kernel, contexts = make_reader(error=code)
        with contexts[0], contexts[1], contexts[2]:
            assert file_io._windows_pipe_reader(stream)() == b''
    stream, kernel, contexts = make_reader(error=6)
    with contexts[0], contexts[1], contexts[2], pytest.raises(OSError):
        file_io._windows_pipe_reader(stream)()
