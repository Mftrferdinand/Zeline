"""Filter correctness independent of display delimiters and pagination."""
import os
from pathlib import Path
import shutil

import pytest

from zeline import file_io


@pytest.fixture(params=['native', 'fallback'])
def backend(request, monkeypatch):
    if request.param == 'native':
        if not shutil.which('rg'):
            pytest.skip('native backend requires ripgrep')
    else:
        monkeypatch.setattr(file_io.shutil, 'which', lambda *args: None)
    return request.param


@pytest.mark.parametrize('target', ['content', 'files'])
def test_filter_precedes_offset_and_page_limit(tmp_path, backend, target):
    for name in ('a_denied', 'b_allowed', 'c_denied', 'd_allowed', 'e_allowed'):
        (tmp_path / name).write_text('needle\n')
    result = file_io.search_files(
        'needle' if target == 'content' else '*', tmp_path, target=target,
        offset=1, limit=1, path_filter=lambda path: path.name.endswith('_allowed'))
    assert 'd_allowed' in result
    assert 'b_allowed' not in result
    assert 'e_allowed' not in result
    assert '_denied' not in result
    assert 'next_offset=2' in result


@pytest.mark.parametrize('target', ['content', 'files'])
def test_filter_exception_fails_closed_and_continues(tmp_path, backend, target):
    (tmp_path / 'a_denied').write_text('needle PRIVATE\n')
    (tmp_path / 'b_allowed').write_text('needle PUBLIC\n')

    def policy(path):
        if path.name == 'a_denied':
            raise ValueError('ownership lookup failed')
        return True

    result = file_io.search_files('needle' if target == 'content' else '*', tmp_path,
                                 target=target, path_filter=policy)
    assert 'b_allowed' in result
    assert 'a_denied' not in result
    assert 'PRIVATE' not in result


@pytest.mark.parametrize('target', ['content', 'files'])
def test_non_utf8_filename_keeps_exact_filter_path(tmp_path, backend, target):
    if os.name == 'nt':
        pytest.skip('requires POSIX arbitrary-byte filenames')
    name = os.fsdecode(b'odd\xff:12:\nname')
    path = tmp_path / name
    try:
        path.write_text('needle PRIVATE\n')
    except OSError as exc:
        import errno
        if exc.errno == errno.EILSEQ:
            pytest.skip('filesystem rejects invalid UTF-8 filenames')
        raise
    seen = []
    result = file_io.search_files('needle' if target == 'content' else '*', tmp_path,
                                 target=target,
                                 path_filter=lambda candidate: seen.append(candidate) or False)
    assert result == '(no matches)'
    assert path in seen


def test_native_records_survive_single_byte_reads(tmp_path, monkeypatch):
    if os.name == 'nt' or not shutil.which('rg'):
        pytest.skip('requires native POSIX backend')
    path = tmp_path / 'a\n:12:name'
    path.write_text('x\nx\n')
    monkeypatch.setattr(file_io, 'READ_CHUNK_BYTES', 1)
    seen = []
    result = file_io.search_files('x', tmp_path,
                                 path_filter=lambda candidate: seen.append(candidate) or True)
    assert seen == [path, path]
    assert ':1: x' in result and ':2: x' in result
