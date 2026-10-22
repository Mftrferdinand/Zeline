"""Read/search windows must stay bounded. unittest-style so CI discovers them."""
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest import mock

from zeline import file_io


def _binary(root: Path) -> Path:
    target = root / 'binary'
    target.write_bytes(b'a\x00b')
    return target


class TestFileIO(unittest.TestCase):
    def test_offset_without_limit_and_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'rows').write_text('one\ntwo\nthree\n')
            result = file_io.read_file('rows', tmp_path, 2)
            self.assertEqual(result, '[lines 2-3 of 3; more_results=false]\ntwo\nthree\n')
            (tmp_path / 'empty').touch()
            self.assertEqual(file_io.read_file('empty', tmp_path), '')

    def test_giant_line_keeps_prefix_and_physical_line_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'giant').write_bytes(b'PREFIX' + b'x' * 100_000 + b'TAIL\nneedle\n')
            result = file_io.read_file('giant', tmp_path, 1, 1)
            self.assertIn('PREFIX', result)
            self.assertNotIn('TAIL', result)
            self.assertIn('truncated', result)
            self.assertLessEqual(len(result.encode()), 50_000)
            self.assertIn('giant:2: needle', file_io.search_files('needle', tmp_path))

    def test_many_short_lines_retention_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'short').write_bytes(b'x\n' * 600_000)
            tracemalloc.start()
            try:
                result = file_io.read_file('short', tmp_path, 1, 1_000_000)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertLess(peak, 1_000_000)
            self.assertLessEqual(len(result.encode()), 50_000)
            self.assertIn('truncated', result)

    def test_search_continues_to_later_files_without_total_scan_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'a').write_bytes(b'x' * 100)
            (tmp_path / 'b').write_bytes(b'needle\n')
            visited = []

            def walk(*args, **kwargs):
                for name in ('a', 'b'):
                    visited.append(name)
                    yield tmp_path / name

            with mock.patch.object(file_io, '_walk_files', walk), \
                 mock.patch.object(file_io.shutil, 'which', lambda *a: None):
                result = file_io.search_files('needle', tmp_path)
            self.assertEqual(visited, ['a', 'b'])
            self.assertIn('b:1: needle', result)

    def test_search_skips_hidden_dependencies_binary_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for name in ('.hidden', 'binary', 'visible'):
                (tmp_path / name).write_bytes(b'Needle\x00' if name == 'binary' else b'Needle\n')
            for name in ('.secret', 'node_modules'):
                (tmp_path / name).mkdir()
                (tmp_path / name / 'x').write_text('Needle')
            (tmp_path / 'link').symlink_to(tmp_path, target_is_directory=True)
            self.assertEqual(file_io.search_files('needle', tmp_path), 'visible:1: Needle')

    def test_search_page_limit_stops_before_next_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'a').write_text('needle\n' * 5)
            visited = []

            def walk(*args, **kwargs):
                for _ in range(10):
                    visited.append(1)
                    yield tmp_path / 'a'

            with mock.patch.object(file_io, '_walk_files', walk), \
                 mock.patch.object(file_io.shutil, 'which', lambda *a: None):
                result = file_io.search_files('needle', tmp_path, limit=2)
            self.assertEqual(result.count('a:'), 2)
            self.assertIn('next_offset=2', result)
            self.assertEqual(len(visited), 1)

    def test_search_cancels_after_streaming_has_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'a').write_bytes(b'x' * 100000 + b'needle\n')
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

            with mock.patch.object(file_io.shutil, 'which', lambda *a: None), \
                 mock.patch.object(Path, 'open', lambda self, *a, **kw: Checked(original(self, *a, **kw))):
                result = file_io.search_files('needle', tmp_path, should_stop=lambda: len(reads) >= 3)
            self.assertIn('cancelled', result)
            self.assertEqual(len(reads), 3)
            self.assertLess(sum(reads), 100000)

    def test_read_stops_at_requested_page_without_counting_whole_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
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

            with mock.patch.object(Path, 'open', lambda self, *a, **kw: Checked(original(self, *a, **kw))):
                result = file_io.read_text_window(target, limit=1)
            self.assertIn('next_offset=2', result)
            self.assertEqual(reads, [2])

    def test_search_utf8_literal_across_chunk_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prefix = b'x' * (file_io.READ_CHUNK_BYTES - 1)
            (tmp_path / 'unicode').write_bytes(prefix + 'École\n'.encode())
            self.assertIn('unicode:1:', file_io.search_files('école', tmp_path))

    def test_small_reads_and_paginated_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'note.txt').write_text('one\ntwo\nthree\n')
            self.assertEqual(file_io.read_file('note.txt', tmp_path), 'one\ntwo\nthree\n')
            self.assertEqual(file_io.read_file('note.txt', tmp_path, 2, 1), (
                '[lines 2-2 of 3; truncated page; more_results=true; next_offset=3; next_byte_offset=8]\ntwo'
            ))
            self.assertIn('past the last line (3)', file_io.read_file('note.txt', tmp_path, 4, 1))
            outside = tmp_path.parent / 'outside.txt'
            self.assertIn('inside the workspace', file_io.read_file(str(outside), tmp_path))
            link = tmp_path / 'link'
            link.symlink_to(outside)
            self.assertIn('inside the workspace', file_io.read_file('link', tmp_path))
            self.assertIn('not a file', file_io.read_file('.', tmp_path))
            self.assertEqual(file_io.read_text_window(tmp_path / 'note.txt', 1, 0), 'one\ntwo\nthree\n')
            self.assertIn('cancelled', file_io.read_file('note.txt', tmp_path, should_stop=lambda: True))

    def test_read_bounds_giant_line_without_blocking_later_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / 'large.txt'
            target.write_bytes(b'x' * (1024 * 1024) + b'\nlast\n')
            with mock.patch.object(Path, 'read_text', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unbounded read'))):
                result = file_io.read_text_window(target, 1, 1)
                self.assertLessEqual(len(result.encode()), file_io.MAX_OUTPUT_BYTES)
                self.assertIn('truncated', result)
                self.assertIn('next_byte_offset=', result)
                self.assertIn('last', file_io.read_text_window(target, 2, 1))
                self.assertIn('cancelled', file_io.read_text_window(target, should_stop=lambda: True))
                self.assertIn('binary', file_io.read_text_window(_binary(tmp_path)))


if __name__ == '__main__':
    unittest.main()
