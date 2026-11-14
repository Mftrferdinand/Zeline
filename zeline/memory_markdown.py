"""Human-editable canonical notes. Metadata is separate from visible quoted text.

Each record has one bounded JSON metadata comment and a Markdown blockquote.
Every body line has a ``> `` prefix, so arbitrary marker text is unambiguous.
"""
from __future__ import annotations
import json
from pathlib import Path

NAMES = ('MEMORY.md', 'USER.md')
MAX_NOTE_BYTES = 8 * 1024 * 1024


def read_note(path, limit=None):
    limit = MAX_NOTE_BYTES if limit is None else limit
    secure(path)
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Memory file exceeds parsing limit; original preserved')
    return data.decode('utf-8')


def render(records, name):
    lines = [f'# {name[:-3]}', '', '<!-- zeline-memory-v1 -->', '']
    for position, record in enumerate(records):
        user = record.get('kind') in {'preference', 'profile', 'user', 'user_profile'}
        explicit = record.get('file_path')
        target = explicit if explicit in NAMES else ('USER.md' if user else 'MEMORY.md')
        if explicit not in NAMES:
            target = 'USER.md' if user else 'MEMORY.md'
        if target != name:
            continue
        meta = {k: v for k, v in record.items() if k != 'text'}
        encoded = json.dumps({'position': position, 'fields': meta}, ensure_ascii=True, allow_nan=False).replace('--', '\\u002d\\u002d')
        lines.extend(['<!-- record ' + encoded + ' -->'])
        lines.extend('> ' + line.replace('\\', '\\\\').replace('\r', '\\r')
                     for line in record['text'].split('\n'))
        lines.extend(['<!-- /record -->', ''])
    return '\n'.join(lines)


def parse(text, positions=None):
    if len(text.encode('utf-8')) > MAX_NOTE_BYTES:
        raise ValueError('Memory file exceeds parsing limit; original preserved')
    if positions is None:
        positions = {}
    # Native editors may save CRLF. Literal carriage returns in note bodies are
    # already escaped by render(), so normalize only physical line endings.
    lines = text.replace('\r\n', '\n').split('\n')
    if len(lines) < 4 or lines[2] != '<!-- zeline-memory-v1 -->' or not lines[0].startswith('# '):
        raise ValueError('Memory Markdown is unreadable; original preserved')
    records = []
    i = 4
    while i < len(lines):
        if lines[i] == '':
            i += 1
            continue
        line = lines[i]
        if not line.startswith('<!-- record ') or not line.endswith(' -->') or len(line) > 65536:
            raise ValueError('Invalid memory metadata; original preserved')
        envelope = json.loads(line[12:-4])
        if not isinstance(envelope, dict) or type(envelope.get('position')) is not int:
            raise ValueError('Invalid memory position')
        meta = envelope.get('fields')
        if not isinstance(meta, dict):
            raise ValueError('Invalid memory fields')
        positions[str(meta.get('id'))] = envelope['position']
        meta = meta.copy()
        i += 1
        body = []
        while i < len(lines) and lines[i].startswith('> '):
            import re
            body.append(re.sub(r'\\([\\r])', lambda m: '\r' if m[1] == 'r' else '\\', lines[i][2:]))
            i += 1
        if i >= len(lines) or lines[i] != '<!-- /record -->' or not body:
            raise ValueError('Invalid memory body; original preserved')
        if not isinstance(meta, dict) or 'text' in meta:
            raise ValueError('Invalid memory metadata')
        meta['text'] = '\n'.join(body)
        if not meta.get('id') or type(meta.get('revision')) is not int or meta['revision'] < 1:
            raise ValueError('Invalid memory identity/revision')
        for key in ('kind', 'source', 'confidence', 'created_at', 'updated_at', 'expires_at', 'state'):
            if key not in meta:
                raise ValueError('Missing memory metadata')
        records.append(meta)
        i += 1
    return records


def read_pair(paths):
    present = [p.exists() for p in paths.values()]
    if not any(present):
        return None
    if not all(present):
        raise ValueError('Incomplete canonical memory pair; original preserved')
    records = []
    positions = {}
    for path in paths.values():
        records.extend(parse(read_note(path), positions))
    for record in records:
        for key in ('created_at', 'updated_at', 'expires_at', 'confidence'):
            import math
            value = record[key]
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError('Invalid memory number')
        if not isinstance(record['text'], str) or not isinstance(record['id'], str):
            raise ValueError('Invalid memory text/identity')
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('Duplicate memory IDs')
    # Storage order survives classification across two notes.
    records.sort(key=lambda r: positions[r['id']])
    return records


def secure(path):
    """Reject user-controlled symlinks, retaining root-owned macOS aliases."""
    import stat
    import sys
    path = Path(path).absolute()
    aliases = {'/var': '/private/var', '/tmp': '/private/tmp', '/etc': '/private/etc'}
    for part in reversed([path, *path.parents]):
        try:
            observed = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(observed.st_mode):
            # macOS exposes its system temp roots through immutable-by-user
            # aliases. Do not generalize this to links inside memory storage.
            expected = aliases.get(str(part)) if sys.platform == 'darwin' else None
            trusted = False
            if expected and observed.st_uid == 0:
                try:
                    target = part.readlink()
                    trusted = (part.parent / target).absolute() == Path(expected)
                except OSError:
                    pass
            if not trusted:
                raise ValueError('Symlink memory component refused')
    return path


def atomic_write(path, text):
    import os
    import tempfile
    secure(path)
    fd, temporary = tempfile.mkstemp(prefix='.memory-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if Path(temporary).read_text(encoding='utf-8') != text:
            raise OSError('Memory readback failed')
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_directory(path):
    import os
    # Windows has no portable directory fsync via os.open; file fsync and atomic
    # replacement remain in force there. Do not break note writes on that host.
    if os.name == 'nt':
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def recover(paths):
    journal = secure(next(iter(paths.values())).parent / '.transaction.json')
    if not journal.exists():
        return
    before = json.loads(read_note(journal, MAX_NOTE_BYTES * 8 + 65536))
    if set(before) != set(NAMES) or any(v is not None and not isinstance(v, str) for v in before.values()):
        raise ValueError('Invalid memory recovery journal; preserved')
    for name, value in before.items():
        secure(paths[name])
        if value is None:
            paths[name].unlink(missing_ok=True)
        else:
            atomic_write(paths[name], value)
    journal.unlink()
    sync_directory(journal.parent)


def write_pair(paths, records):
    recover(paths)
    texts = {name: render(records, name) for name in paths}
    for text in texts.values():
        parse(text)
    before = {n: read_note(p) if p.exists() else None for n, p in paths.items()}
    journal = next(iter(paths.values())).parent / '.transaction.json'
    atomic_write(journal, json.dumps(before, ensure_ascii=True))
    try:
        for name, path in paths.items():
            atomic_write(path, texts[name])
        journal.unlink()
        sync_directory(journal.parent)
    except BaseException:
        recover(paths)
        raise


from contextlib import contextmanager


@contextmanager
def locked(paths, timeout):
    import os
    import time
    import sqlite3
    directory = next(iter(paths.values())).parent
    for p in [*paths.values(), directory / '.lock', directory / '.transaction.json']:
        secure(p)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(directory / '.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            def acquire():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            def release():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(fd, fcntl.LOCK_UN)
        while True:
            try:
                acquire()
                acquired = True
                break
            except OSError as exc:
                import errno
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise sqlite3.OperationalError('Memory lock timeout') from exc
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        # Windows byte locks may extend beyond EOF. Initialize only after
        # acquiring: a peer can lock byte zero after our empty-file check,
        # making a pre-lock write fail with EACCES outside the retry loop.
        if os.name == 'nt' and os.fstat(fd).st_size == 0:
            os.write(fd, b'\0')
        recover(paths)
        yield
    finally:
        try:
            if acquired:
                release()
        finally:
            os.close(fd)


def backup_sqlite(path):
    import sqlite3
    import uuid
    from contextlib import closing
    if not path.exists():
        return
    backup = secure(path.with_name(path.name + '.' + uuid.uuid4().hex + '.bak'))
    with closing(sqlite3.connect(str(path))) as src, closing(sqlite3.connect(str(backup))) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('Memory backup verification failed')
    backup.chmod(0o600)
    sync_directory(path.parent)
