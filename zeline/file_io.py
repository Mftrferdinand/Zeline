"""Streaming file windows and resumable workspace search."""
from __future__ import annotations

import codecs
import fnmatch
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
from typing import Callable, Iterable

MAX_OUTPUT_BYTES = 50_000
MAX_SEARCH_RESULTS = 50
READ_CHUNK_BYTES = 8192
_STOP = "CANCELLED: file operation stopped. (cancelled)"
_SKIP_DIRS = frozenset({"venv", "node_modules", "__pycache__", "dist", "build"})


def _stopped(should_stop: Callable[[], bool] | None) -> bool:
    try:
        return bool(should_stop and should_stop())
    except Exception:
        return True


def _inside(target: Path, workspace: Path) -> Path:
    root = workspace.expanduser().resolve(strict=False)
    resolved = (target if target.is_absolute() else root / target).expanduser().resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path must stay inside the workspace: {root}") from exc
    return resolved


def _display_bytes(data: bytes, limit: int | None = None) -> str:
    limit = MAX_OUTPUT_BYTES if limit is None else limit
    text = data.decode("utf-8", errors="replace")
    encoded = text.encode()
    if len(encoded) <= limit:
        return text
    marker = b"\n[truncated: output limit reached]"
    return encoded[:max(0, limit - len(marker))].decode("utf-8", errors="ignore") + marker.decode()


def read_text_window(path: Path, offset: int = 1, limit: int = 0,
                     should_stop: Callable[[], bool] | None = None,
                     byte_offset: int | None = None) -> str:
    """Read a bounded page; byte cursors continue an oversized physical line.

    No full-file scan is performed merely to count lines. Small files can have
    exact totals; otherwise the metadata explicitly leaves the total unknown.
    Cursors assume the file remains unchanged between requests.
    """
    path = Path(path)
    if _stopped(should_stop):
        return _STOP
    if not path.is_file():
        return f"ERROR read file: not a file or not found: {path}"
    start = max(1, int(offset or 1))
    limit = max(0, int(limit or 0))
    cap = max(1, MAX_OUTPUT_BYTES - 1024)
    retained = bytearray()
    current = 1
    shown_end = start - 1
    partial = False
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            if byte_offset is not None:
                cursor = max(0, int(byte_offset))
                if cursor > size:
                    return "ERROR read file: byte_offset is past EOF."
                stream.seek(cursor)
                current = start
            while True:
                if _stopped(should_stop):
                    return _STOP
                if limit and current >= start + limit:
                    break
                room = cap - len(retained)
                if room <= 0:
                    break
                raw = stream.readline(min(READ_CHUNK_BYTES, room) if current >= start else READ_CHUNK_BYTES)
                if not raw:
                    break
                if b"\x00" in raw:
                    return f"ERROR read file: binary file is not supported: {path}"
                if current >= start:
                    retained.extend(raw)
                    shown_end = current
                partial = not raw.endswith(b"\n")
                if raw.endswith(b"\n"):
                    current += 1
            position = stream.tell()
            more = position < size
            # Do not split a valid UTF-8 sequence between byte-cursor pages.
            if more and retained:
                decoder = codecs.getincrementaldecoder("utf-8")("strict")
                decoder.decode(bytes(retained), final=False)
                pending = decoder.getstate()[0]
                if pending:
                    del retained[-len(pending):]
                    position -= len(pending)
                    partial = True
            total = current - 1 if not partial else current
            exact = not more
            if more and size <= READ_CHUNK_BYTES:
                rest = stream.read(READ_CHUNK_BYTES)
                total = current + rest.count(b"\n") - (1 if rest.endswith(b"\n") else 0)
                exact = True
        if not size and start == 1:
            return ""
        if not retained and not more:
            return f"ERROR read file: offset {start} is past the last line ({max(0, total)})."
        # Reject invalid UTF-8 rather than expanding replacements after the source
        # cursor has been fixed: display truncation would otherwise skip bytes.
        text = bytes(retained).decode("utf-8", errors="strict")
        if start == 1 and not limit and byte_offset is None and not more:
            if len(text) > 20_000:
                from . import offload
                return _display_bytes(offload.maybe_offload(text, 20_000).encode())
            return text
        next_line = shown_end if partial else shown_end + 1
        note = f"[lines {start}-{shown_end}"
        note += f" of {total}" if exact else "; total lines unknown"
        if more:
            note += f"; truncated page; more_results=true; next_offset={next_line}; next_byte_offset={position}"
            if partial:
                note += "; physical line continues: pass both offset and byte_offset"
        else:
            note += "; more_results=false"
        if limit:
            text = text.rstrip("\r\n")
        return _display_bytes((note + "]\n" + text).encode())
    except (OSError, UnicodeError) as exc:
        return f"ERROR read file: {exc}"


def read_file(path: str, workspace: Path, offset: int = 1, limit: int = 0,
              should_stop: Callable[[], bool] | None = None,
              byte_offset: int | None = None) -> str:
    try:
        target = _inside(Path(path), Path(workspace))
    except ValueError as exc:
        return f"ERROR read file: {exc}"
    return read_text_window(target, offset, limit, should_stop, byte_offset)


def _walk_files(root: Path, should_stop: Callable[[], bool] | None = None) -> Iterable[Path]:
    """Stable fallback traversal, without retained file contents or open handles."""
    for directory, dirs, files in os.walk(root, followlinks=False):
        if _stopped(should_stop):
            return
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in _SKIP_DIRS
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            if _stopped(should_stop):
                return
            target = Path(directory) / name
            if not name.startswith('.') and not target.is_symlink():
                yield target


def _literal_rows(query, root, pattern, should_stop, target):
    needle = query.casefold()
    for path in _walk_files(root, should_stop):
        relative = str(path.relative_to(root))
        if not (fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern)):
            continue
        if target == 'files':
            if needle in relative.casefold() or fnmatch.fnmatch(relative.casefold(), needle):
                yield relative
            continue
        try:
            with path.open('rb') as stream:
                line = 1
                matched = False
                carry = ''
                prefix = bytearray()
                decoder = codecs.getincrementaldecoder('utf-8')('replace')
                while True:
                    if _stopped(should_stop):
                        return
                    raw = stream.readline(READ_CHUNK_BYTES)
                    if not raw or b'\x00' in raw:
                        break
                    prefix.extend(raw[:max(0, 300 - len(prefix))])
                    text = carry + decoder.decode(raw).casefold()
                    if not matched and needle in text:
                        yield f'{relative}:{line}: ' + prefix.decode('utf-8', errors='replace').rstrip('\r\n')
                        matched = True
                    carry = text[-(len(needle) - 1):] if len(needle) > 1 else ''
                    if raw.endswith(b'\n'):
                        line += 1
                        matched = False
                        carry = ''
                        prefix.clear()
        except OSError:
            continue


def _rg_rows(executable, query, root, pattern, should_stop, target, regex):
    """Drain bounded native search rows and reap on page end or cancellation."""
    args = [executable, '--sort', 'path', '--no-ignore', '--color', 'never', '--no-messages']
    args += ['--glob', pattern]
    for name in sorted(_SKIP_DIRS):
        args += ['--glob', f'!**/{name}/**']
    args += ['--glob', '!**/.*']
    if target == 'files':
        args += ['--files', '.']
    else:
        args += ['--line-number', '--with-filename', '--no-heading', '--max-columns', '300', '--max-columns-preview']
        if not regex:
            args += ['--fixed-strings']
        args += ['--ignore-case', '--', query, '.']
    process = subprocess.Popen(args, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    buffer = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            done = False
            while not done:
                if _stopped(should_stop):
                    return
                if not selector.select(0.05):
                    continue
                data = os.read(process.stdout.fileno(), READ_CHUNK_BYTES)
                done = not data
                buffer.extend(data)
                while b'\n' in buffer or (done and buffer):
                    split = buffer.find(b'\n')
                    split = len(buffer) if split < 0 else split
                    row = bytes(buffer[:split]).decode('utf-8', errors='replace')
                    del buffer[:split + 1]
                    if row.startswith('./'):
                        row = row[2:]
                    if target == 'files':
                        needle = query.casefold()
                        if needle not in row.casefold() and not fnmatch.fnmatch(row.casefold(), needle):
                            continue
                    else:
                        match = re.match(r'^(.+?:[0-9]+:)(.*)$', row)
                        if not match:
                            continue
                        row = match.group(1) + ' ' + match.group(2)
                    yield row
                if len(buffer) > MAX_OUTPUT_BYTES:
                    raise ValueError('search row exceeds output window')
        code = process.wait(timeout=1)
        if code not in (0, 1):
            raise ValueError('native search rejected the pattern or could not search this path')
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
        if process.stdout is not None:
            process.stdout.close()


def search_files(query: str, workspace: Path, pattern: str = '*',
                 should_stop: Callable[[], bool] | None = None,
                 limit: int = MAX_SEARCH_RESULTS, offset: int = 0,
                 target: str = 'content', regex: bool = False) -> str:
    """Search a response window, not a permanently capped prefix of the tree."""
    query = str(query or '')
    if not query.strip():
        return 'ERROR search files: query is empty.'
    if len(query) > READ_CHUNK_BYTES:
        return 'ERROR search files: query is too long.'
    if target not in {'content', 'files'}:
        return 'ERROR search files: target must be content or files.'
    if _stopped(should_stop):
        return _STOP
    root = Path(workspace).expanduser().resolve(strict=False)
    offset, limit = max(0, int(offset)), max(1, int(limit))
    executable = shutil.which('rg')
    if regex and (not executable or target == 'files'):
        return 'ERROR search files: regex content search requires ripgrep.'
    rows = (_rg_rows(executable, query, root, pattern, should_stop, target, regex) if executable
            else _literal_rows(query, root, pattern, should_stop, target))
    retained = bytearray()
    seen = count = 0
    more = False
    try:
        for row in rows:
            if _stopped(should_stop):
                return _display_bytes((_STOP + '\n').encode() + retained)
            if seen < offset:
                seen += 1
                continue
            encoded = row.encode('utf-8')
            if count >= limit or (retained and len(retained) + len(encoded) + 1 > MAX_OUTPUT_BYTES - 1024):
                more = True
                break
            if retained:
                retained.extend(b'\n')
            retained.extend(_display_bytes(encoded, MAX_OUTPUT_BYTES - 1024).encode())
            count += 1
    except (OSError, ValueError) as exc:
        return f'ERROR search files: {exc}'
    finally:
        rows.close()
    if _stopped(should_stop):
        return _display_bytes((_STOP + '\n').encode() + retained)
    text = retained.decode() if retained else '(no matches)'
    if more or offset:
        note = f'[offset={offset}; matches={count}; more_results={str(more).lower()}'
        if more:
            note += f'; truncated page; next_offset={offset + count}'
        text = note + ']\n' + text
    return _display_bytes(text.encode())
