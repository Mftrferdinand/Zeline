from pathlib import Path
import tracemalloc


def test_offset_without_limit_and_empty_file(tmp_path):
    (tmp_path / 'rows').write_text('one\ntwo\nthree\n')
    result = file_io.read_file('rows', tmp_path, 2)
    assert result == '[lines 2-3 of 3; more_results=false]\ntwo\nthree\n'
    (tmp_path / 'empty').touch()
    assert file_io.read_file('empty', tmp_path) == ''


def test_giant_line_keeps_prefix_and_physical_line_numbers(tmp_path):
    (tmp_path / 'giant').write_bytes(b'PREFIX' + b'x' * 100_000 + b'TAIL\nneedle\n')
    result = file_io.read_file('giant', tmp_path, 1, 1)
    assert 'PREFIX' in result
    assert 'TAIL' not in result
    assert 'truncated' in result
    assert len(result.encode()) <= 50_000
    assert 'giant:2: needle' in file_io.search_files('needle', tmp_path)


def test_many_short_lines_retention_is_bounded(tmp_path):
    (tmp_path / 'short').write_bytes(b'x\n' * 600_000)
    tracemalloc.start()
    try:
        result = file_io.read_file('short', tmp_path, 1, 1_000_000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000
    assert len(result.encode()) <= 50_000
    assert 'truncated' in result


def test_search_continues_to_later_files_without_total_scan_cap(tmp_path, monkeypatch):
    (tmp_path / 'a').write_bytes(b'x' * 100)
    (tmp_path / 'b').write_bytes(b'needle\n')
    visited = []
    def walk(*args, **kwargs):
        for name in ('a', 'b'):
            visited.append(name)
            yield tmp_path / name
    monkeypatch.setattr(file_io, '_walk_files', walk)
    monkeypatch.setattr(file_io.shutil, 'which', lambda *a: None)
    result = file_io.search_files('needle', tmp_path)
    assert visited == ['a', 'b']
    assert 'b:1: needle' in result



def test_search_skips_hidden_dependencies_binary_and_symlinks(tmp_path):
    for name in ('.hidden', 'binary', 'visible'):
        (tmp_path / name).write_bytes(b'Needle\x00' if name == 'binary' else b'Needle\n')
    for name in ('.secret', 'node_modules'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'x').write_text('Needle')
    (tmp_path / 'link').symlink_to(tmp_path, target_is_directory=True)
    assert file_io.search_files('needle', tmp_path) == 'visible:1: Needle'



from zeline import file_io


def test_search_page_limit_stops_before_next_file(tmp_path, monkeypatch):
    (tmp_path / 'a').write_text('needle\n' * 5)
    visited = []
    def walk(*args, **kwargs):
        for _ in range(10):
            visited.append(1)
            yield tmp_path / 'a'
    monkeypatch.setattr(file_io, '_walk_files', walk)
    monkeypatch.setattr(file_io.shutil, 'which', lambda *a: None)
    result = file_io.search_files('needle', tmp_path, limit=2)
    assert result.count('a:') == 2
    assert 'next_offset=2' in result
    assert len(visited) == 1



def test_search_cancels_after_streaming_has_started(tmp_path, monkeypatch):
    (tmp_path / 'a').write_bytes(b'x' * 100000 + b'needle\n')
    monkeypatch.setattr(file_io.shutil, 'which', lambda *a: None)
    original = Path.open
    reads = []
    class Checked:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def readline(self, size):
            value = self.stream.readline(size)
            reads.append(len(value))
            return value
    monkeypatch.setattr(Path, 'open', lambda self, *a, **kw: Checked(original(self, *a, **kw)))
    result = file_io.search_files('needle', tmp_path, should_stop=lambda: len(reads) >= 3)
    assert 'cancelled' in result
    assert len(reads) == 3
    assert sum(reads) < 100000



def test_read_stops_at_requested_page_without_counting_whole_file(tmp_path, monkeypatch):
    target = tmp_path / 'large'
    target.write_bytes(b'x\n' * 6000)
    original = Path.open
    reads = []
    class Checked:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def __getattr__(self, name): return getattr(self.stream, name)
        def readline(self, size):
            value = self.stream.readline(size)
            reads.append(len(value))
            return value
    monkeypatch.setattr(Path, 'open', lambda self, *a, **kw: Checked(original(self, *a, **kw)))
    result = file_io.read_text_window(target, limit=1)
    assert 'next_offset=2' in result
    assert reads == [2]



def test_search_utf8_literal_across_chunk_boundary(tmp_path):
    prefix = b'x' * (file_io.READ_CHUNK_BYTES - 1)
    (tmp_path / 'unicode').write_bytes(prefix + 'École\n'.encode())
    assert 'unicode:1:' in file_io.search_files('école', tmp_path)


def test_small_reads_and_paginated_reads(tmp_path):
    (tmp_path / 'note.txt').write_text('one\ntwo\nthree\n')
    assert file_io.read_file('note.txt', tmp_path) == 'one\ntwo\nthree\n'
    assert file_io.read_file('note.txt', tmp_path, 2, 1) == (
        '[lines 2-2 of 3; truncated page; more_results=true; next_offset=3; next_byte_offset=8]\ntwo'
    )
    assert 'past the last line (3)' in file_io.read_file('note.txt', tmp_path, 4, 1)
    outside = tmp_path.parent / 'outside.txt'
    assert 'inside the workspace' in file_io.read_file(str(outside), tmp_path)
    link = tmp_path / 'link'
    link.symlink_to(outside)
    assert 'inside the workspace' in file_io.read_file('link', tmp_path)
    assert 'not a file' in file_io.read_file('.', tmp_path)
    assert file_io.read_text_window(tmp_path / 'note.txt', 1, 0) == 'one\ntwo\nthree\n'
    assert 'cancelled' in file_io.read_file('note.txt', tmp_path, should_stop=lambda: True)


def test_read_bounds_giant_line_without_blocking_later_lines(tmp_path, monkeypatch):
    target = tmp_path / 'large.txt'
    target.write_bytes(b'x' * (1024 * 1024) + b'\nlast\n')
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unbounded read')))
    result = file_io.read_text_window(target, 1, 1)
    assert len(result.encode()) <= file_io.MAX_OUTPUT_BYTES
    assert 'truncated' in result
    assert 'next_byte_offset=' in result
    assert 'last' in file_io.read_text_window(target, 2, 1)
    assert 'cancelled' in file_io.read_text_window(target, should_stop=lambda: True)
    assert 'binary' in file_io.read_text_window(_binary(tmp_path))



def _binary(root):
    target = root / 'binary'
    target.write_bytes(b'a\x00b')
    return target
