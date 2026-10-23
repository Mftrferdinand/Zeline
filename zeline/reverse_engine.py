"""Read-only, bounded artifact evidence for the built-in Zeline static engine.

Targets are never imported, executed, extracted, or sent over the network.
This is an inspector, not a decompiler, malware verdict, or OS sandbox.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import io
import struct
import zipfile
import os
import re
from pathlib import Path
import stat

MAX_FILE_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


def _check_stop(should_stop):
    if should_stop is not None and should_stop():
        raise InterruptedError('static analysis cancelled')


def _relative_path(path: str, workspace: Path) -> tuple[Path, Path]:
    if not isinstance(path, str) or not path or '\x00' in path:
        raise ValueError('path must be a nonempty workspace path')
    root = Path(workspace).resolve(strict=True)
    requested = Path(path).expanduser()
    if requested.is_absolute():
        try:
            requested = requested.relative_to(root)
        except ValueError as exc:
            raise ValueError('path must stay inside the workspace') from exc
    if '..' in requested.parts:
        raise ValueError('parent traversal is not allowed')
    return root, requested


@contextlib.contextmanager
def _open_target(root: Path, relative: Path, *, directory=False):
    """Use no-follow descriptor traversal on supported POSIX hosts.

    The fallback rejects observed symlinks but is not a race-proof Windows
    filesystem sandbox. A restricted OS account remains necessary for hostile
    workspaces on hosts without descriptor-relative no-follow operations.
    """
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    fd = None
    try:
        if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
            fd = os.open(root, flags | os.O_DIRECTORY | os.O_NOFOLLOW)
            for index, part in enumerate(relative.parts):
                is_dir = index < len(relative.parts) - 1 or directory
                next_fd = os.open(part, flags | os.O_NOFOLLOW |
                                  (os.O_DIRECTORY if is_dir else 0), dir_fd=fd)
                os.close(fd)
                fd = next_fd
        else:
            target = root
            for part in relative.parts:
                target = target / part
                if target.is_symlink():
                    raise ValueError('symlink traversal is not allowed')
            target.resolve(strict=True).relative_to(root)
            if directory:
                metadata = target.stat()
                if not stat.S_ISDIR(metadata.st_mode):
                    raise NotADirectoryError('target must be a directory')
                yield None, metadata
                return
            fd = os.open(target, flags)
        metadata = os.fstat(fd)
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(metadata.st_mode):
            raise ValueError('target must be a directory' if directory else
                             'target must be a regular file')
        yield fd, metadata
    finally:
        if fd is not None:
            os.close(fd)


def _file_bytes(root, relative, should_stop):
    with _open_target(root, relative) as (fd, before):
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError(f'file exceeds the {MAX_FILE_BYTES}-byte analysis limit')
        chunks = []
        total = 0
        while True:
            _check_stop(should_stop)
            chunk = os.read(fd, min(CHUNK_BYTES, MAX_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ValueError('file grew beyond the analysis limit')
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('target changed during inspection; retry on a stable copy')
        return b''.join(chunks)


def _evidence(action, artifact, findings, limitations):
    record = {'schema_version': 1, 'engine': 'zeline-static-v1', 'status': 'ok',
              'operation': action, 'artifact': artifact, 'findings': findings,
              'limitations': limitations}
    encoded = json.dumps(record, sort_keys=True, ensure_ascii=True,
                         separators=(',', ':')).encode('utf-8')
    return {'evidence_id': 'zev_' + hashlib.sha256(encoded).hexdigest(), **record}


def _directory_inventory(root, relative, offset, limit, should_stop):
    entries = []
    next_offset = None
    with _open_target(root, relative, directory=True) as (fd, metadata):
        with os.scandir(fd if fd is not None else root / relative) as listing:
            for index, entry in enumerate(listing):
                _check_stop(should_stop)
                if index < offset:
                    continue
                if len(entries) == limit:
                    next_offset = index
                    break
                try:
                    info = entry.stat(follow_symlinks=False)
                    kind = ('symlink' if stat.S_ISLNK(info.st_mode) else
                            'directory' if stat.S_ISDIR(info.st_mode) else
                            'file' if stat.S_ISREG(info.st_mode) else 'special')
                    size = info.st_size if kind == 'file' else None
                except OSError:
                    kind, size = 'unavailable', None
                entries.append({'path': entry.name, 'kind': kind, 'size_bytes': size})
        artifact = {'path': relative.as_posix(), 'sha256': None, 'size_bytes': None,
                    'directory_mtime_ns': metadata.st_mtime_ns}
    return _evidence('inventory', artifact,
                     {'kind': 'directory', 'recursive': False, 'offset': offset,
                      'entries': entries, 'next_offset': next_offset,
                      'exhausted': next_offset is None},
                     ['Directory observation only; no recursive content hash or snapshot.',
                      'Offset pages assume an unchanged directory; symlink children are not followed.'])


def _zip_inventory(data, artifact, offset, limit, should_stop):
    # Bound the directory before ZipFile allocates ZipInfo objects. Never read
    # member content: compressed sizes and CRCs here are unverified declarations.
    end = data.rfind(b'PK\x05\x06', max(0, len(data) - 65557))
    if end < 0 or end + 22 > len(data):
        raise ValueError('inventory supports directories and non-ZIP64 ZIP indexes only')
    _, disk, start_disk, disk_count, count, central_size, central_offset, comment_size = struct.unpack_from('<4s4H2LH', data, end)
    if (disk or start_disk or disk_count != count or count == 65535 or
            central_size == 0xffffffff or central_offset == 0xffffffff):
        raise ValueError('multi-volume and ZIP64 indexes are unsupported')
    if (count > 4096 or central_size > 512 * 1024 or
            central_offset + central_size > end or end + 22 + comment_size != len(data)):
        raise ValueError('ZIP index is malformed or exceeds bounded inventory limits')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) != count or len(members) > 4096:
                raise ValueError('ZIP entry count mismatch')
            entries = []
            for index in range(offset, min(offset + limit, len(members))):
                _check_stop(should_stop)
                member = members[index]
                name = member.filename
                portable = name.replace('\\', '/')
                unsafe = (portable.startswith('/') or '..' in portable.split('/') or
                          (len(portable) > 1 and portable[1] == ':'))
                entries.append({'path': name[:512], 'name_truncated': len(name) > 512,
                                'unsafe_path': unsafe,
                                'kind': 'directory' if member.is_dir() else 'member',
                                'size_bytes': member.file_size,
                                'compressed_bytes': member.compress_size,
                                'encrypted': bool(member.flag_bits & 1)})
    except zipfile.BadZipFile as exc:
        raise ValueError('invalid ZIP index') from exc
    next_offset = offset + len(entries) if offset + len(entries) < len(members) else None
    return _evidence('inventory', artifact,
                     {'kind': 'zip', 'entries': entries, 'offset': offset,
                      'total_entries': len(members), 'next_offset': next_offset,
                      'exhausted': next_offset is None},
                     ['ZIP index declarations only; member contents, sizes and CRCs are not verified.',
                      'No extraction or decompression; ZIP64 and multi-volume archives are unsupported.'])


def analyze(action: str, path: str, workspace: Path, *, offset=0, limit=50,
            min_length=4, language='', byte_offset=None, architecture='', base_address=None,
            symbol='', target_address=None, import_iat_rva=None, should_stop=None) -> dict:
    """Return reproducible evidence for an explicit static-only operation."""
    _check_stop(should_stop)
    if action not in {'inspect', 'strings', 'inventory', 'binary', 'source', 'disassemble', 'references'}:
        raise ValueError('unknown static analysis action')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('offset must be nonnegative and limit must be between 1 and 100')
    if type(min_length) is not int or not 1 <= min_length <= 64:
        raise ValueError('min_length must be between 1 and 64')
    if not isinstance(symbol, str):
        raise ValueError('symbol must be an exact string name')
    if symbol and (action not in ('disassemble', 'references') or base_address is not None
                   or (action == 'disassemble' and byte_offset is not None)):
        raise ValueError('symbol is disassemble/references-only; disassembly symbol excludes byte_offset/base_address')
    if action not in ('disassemble', 'references') and (byte_offset is not None or architecture != '' or base_address is not None):
        raise ValueError('byte_offset, architecture and base_address are disassemble/references-only options')
    if action in ('disassemble', 'references') and offset != 0:
        raise ValueError('disassemble/references use byte_offset, not entry offset')
    if target_address is not None and action != 'references':
        raise ValueError('target_address is references-only')
    if import_iat_rva is not None:
        if action != 'references' or type(import_iat_rva) is not int or not 0 <= import_iat_rva < 2**32:
            raise ValueError('import_iat_rva is references-only and must be a nonnegative 32-bit integer')
    if action == 'references' and (base_address is not None or
            sum((bool(symbol), target_address is not None, import_iat_rva is not None)) != 1):
        raise ValueError('references requires exactly one symbol/target_address/import_iat_rva and no base_address')
    root, relative = _relative_path(path, workspace)
    if action == 'inventory':
        try:
            return _directory_inventory(root, relative, offset, limit, should_stop)
        except NotADirectoryError:
            pass
    data = _file_bytes(root, relative, should_stop)
    artifact = {'path': relative.as_posix(), 'sha256': hashlib.sha256(data).hexdigest(),
                'size_bytes': len(data)}
    if action == 'inventory':
        return _zip_inventory(data, artifact, offset, limit, should_stop)
    if action == 'references':
        from zeline.reverse_references import scan_references
        findings = scan_references(data, symbol=symbol, target_address=target_address, import_iat_rva=import_iat_rva,
                                   byte_offset=byte_offset, architecture=architecture,
                                   limit=limit, should_stop=should_stop)
        limitations = findings.pop('limitations')
        return _evidence(action, artifact, findings, limitations)
    if action == 'disassemble':
        from zeline.reverse_code_regions import select_code_region
        from zeline.reverse_disassembly import decode_instructions
        navigation = {}
        if symbol:
            from zeline.reverse_symbol_navigation import resolve_code_symbol
            navigation = resolve_code_symbol(data, symbol, architecture=architecture, should_stop=should_stop)
            byte_offset = navigation.pop('byte_offset')
            mode = navigation.pop('architecture')
            if architecture and mode and architecture != mode:
                navigation['limitations'].append('Caller overrides the symbol-declared ARM/Thumb mode.')
            architecture = architecture or mode
        selection = select_code_region(data, byte_offset=byte_offset, architecture=architecture,
                                       base_address=base_address, should_stop=should_stop)
        if navigation:
            selection['limitations'].extend(navigation.pop('limitations'))
            selection.update(navigation)
        start = selection['byte_offset']
        window = data[start:start + min(selection['bytes_remaining'], 4096)]
        decoded = decode_instructions(window, selection['architecture'], address=selection['address'],
                                      file_offset=start, limit=limit, endianness=selection['endianness'],
                                      should_stop=should_stop)
        _check_stop(should_stop)
        consumed = decoded['bytes_consumed']
        if type(consumed) is not int or not 0 <= consumed <= len(window):
            raise ValueError('invalid disassembly worker byte count')
        remaining = selection['bytes_remaining'] - consumed
        stopped = decoded['stop_reason']
        limitations = selection.pop('limitations') + decoded.pop('limitations')
        findings = {**selection, **decoded, 'bytes_remaining': remaining,
                    'stopped_at_byte_offset': start + consumed,
                    'next_byte_offset': start + consumed if consumed and remaining and stopped != 'undecodable' else None,
                    'exhausted': remaining == 0,
                    'stop_reason': 'end_of_region' if remaining == 0 else
                                   'page_limit' if stopped == 'limit' else
                                   'window_boundary' if stopped == 'end' else stopped}
        return _evidence(action, artifact, findings, limitations)
    if action in {'binary', 'source'}:
        if action == 'binary':
            from zeline.reverse_binary import inspect_binary_details
            findings = inspect_binary_details(data, offset=offset, limit=limit, should_stop=should_stop)
        else:
            from zeline.reverse_source import inspect_source
            if not language:
                language = ('python' if relative.suffix.lower() == '.py' else
                            'javascript' if relative.suffix.lower() in {'.js', '.mjs', '.cjs', '.jsx'} else '')
            findings = inspect_source(data, language, should_stop=should_stop)
        _check_stop(should_stop)
        limitations = findings.pop('limitations')
        return _evidence(action, artifact, findings, limitations)
    if action == 'strings':
        matches = []
        next_offset = None
        for index, match in enumerate(re.finditer(rb'[ -~]{%d,}' % min_length, data)):
            _check_stop(should_stop)
            if index < offset:
                continue
            if len(matches) == limit:
                next_offset = index
                break
            length = match.end() - match.start()
            matches.append({'offset': match.start(), 'length_bytes': length,
                            'value': data[match.start():min(match.end(), match.start() + 256)].decode('ascii'),
                            'truncated': length > 256})
        return _evidence(action, artifact,
                         {'encoding': 'ascii', 'offset': offset, 'matches': matches,
                          'next_offset': next_offset, 'exhausted': next_offset is None},
                         ['Printable ASCII only; strings are observations, not proof of behavior.',
                          'Displayed strings are capped at 256 characters; byte lengths are original.'])
    kind = ('elf' if data.startswith(b'\x7fELF') else 'pe-candidate' if data.startswith(b'MZ')
            else 'zip' if data.startswith(b'PK\x03\x04') else
            'python-source-candidate' if relative.suffix.lower() == '.py' else
            'javascript-source-candidate' if relative.suffix.lower() in {'.js', '.mjs', '.cjs', '.jsx'}
            else 'unknown')
    return _evidence(action, artifact, {'format': kind, 'magic_hex': data[:16].hex()},
                     ['Static observations only; no target execution or behavioral equivalence claim.',
                      'Format candidates require the corresponding parser for validation.'])
