"""Read/search pages must be bounded without becoming permanent scan caps."""
import re
from zeline import file_io


def test_read_can_reach_lines_beyond_old_two_mib_scan(tmp_path):
    path = tmp_path / 'large'
    with path.open('wb') as f:
        for _ in range(2300):
            f.write(b'x' * 1023 + b'\n')
        f.write(b'TARGET_AFTER_OLD_CAP\nnext\n')
    result = file_io.read_file('large', tmp_path, offset=2301, limit=1)
    assert 'TARGET_AFTER_OLD_CAP' in result
    assert 'next_offset=2302' in result
    assert len(result.encode()) <= file_io.MAX_OUTPUT_BYTES


def test_search_offset_pages_do_not_restart_or_repeat(tmp_path):
    (tmp_path / 'rows').write_text(''.join(f'needle {i}\n' for i in range(140)))
    result = file_io.search_files('needle', tmp_path, limit=3, offset=120)
    assert 'rows:121: needle 120' in result
    assert 'rows:123: needle 122' in result
    assert 'rows:124:' not in result
    assert 'next_offset=123' in result
    assert len(result.encode()) <= file_io.MAX_OUTPUT_BYTES


def test_unicode_long_line_byte_cursor_reconstructs_without_loss(tmp_path):
    original = '😀' * 26000
    (tmp_path / 'unicode').write_text(original + '\nlast\n')
    offset, byte_offset = 1, None
    chunks = []
    for _ in range(8):
        page = file_io.read_file('unicode', tmp_path, offset=offset, limit=1, byte_offset=byte_offset)
        assert len(page.encode()) <= file_io.MAX_OUTPUT_BYTES
        header, text = page.split('\n', 1)
        chunks.append(text)
        following = int(re.search(r'next_offset=(\d+)', header)[1])
        following_byte = int(re.search(r'next_byte_offset=(\d+)', header)[1])
        assert following_byte > (byte_offset or 0)
        offset, byte_offset = following, following_byte
        if following == 2:
            break
    else:
        raise AssertionError('cursor did not advance beyond the long line')
    assert ''.join(chunks) == original
    assert 'last' in file_io.read_file('unicode', tmp_path, offset=offset, limit=1, byte_offset=byte_offset)


def test_native_and_fallback_search_pages_have_no_duplicates(tmp_path, monkeypatch):
    for name in ('b', 'a'):
        (tmp_path / name).write_text(''.join(f'needle {i}\n' for i in range(4)))
    native = file_io.shutil.which
    for use_native in (True, False):
        monkeypatch.setattr(file_io.shutil, 'which', native if use_native else lambda *a: None)
        rows = []
        offset = 0
        for _ in range(5):
            page = file_io.search_files('needle', tmp_path, limit=3, offset=offset)
            rows += [line for line in page.splitlines() if re.match(r'[ab]:\d+:', line)]
            marker = re.search(r'next_offset=(\d+)', page)
            if not marker:
                break
            offset = int(marker[1])
        assert len(rows) == len(set(rows)) == 8


def test_native_regex_and_file_name_search(tmp_path):
    (tmp_path / 'needle.py').write_text('item_17\nitem_text\n')
    assert 'item_17' in file_io.search_files(r'item_\d+', tmp_path, regex=True)
    assert 'item_text' not in file_io.search_files(r'item_\d+', tmp_path, regex=True)
    assert file_io.search_files('needle', tmp_path, target='files') == 'needle.py'
    assert 'ERROR' in file_io.search_files('[', tmp_path, regex=True)


def test_native_search_reaps_child_when_cancelled_mid_output(tmp_path, monkeypatch):
    (tmp_path / 'rows').write_text('needle\n' * 1000)
    real_popen = file_io.subprocess.Popen
    real_read = file_io.os.read
    children = []
    cancelled = [False]
    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process
    def read(*args):
        data = real_read(*args)
        if b'needle' in data:
            cancelled[0] = True
        return data
    monkeypatch.setattr(file_io.subprocess, 'Popen', spawn)
    monkeypatch.setattr(file_io.os, 'read', read)
    result = file_io.search_files('needle', tmp_path, should_stop=lambda: cancelled[0])
    assert 'cancelled' in result
    assert len(children) == 1 and children[0].poll() is not None
