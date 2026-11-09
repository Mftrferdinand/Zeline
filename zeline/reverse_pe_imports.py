"""Bounded static PE imports/exports. Never loads, resolves or executes input.

Malformed, cancelled and over-budget inspections raise ValueError (no partial
success). Absent directories are available empty pages. Totals are exact after
bounded scans; only the requested page is retained for imports/exports.
"""
import struct

from .reverse_binary import inspect_binary

MAX_DESCRIPTORS = 256
MAX_THUNKS = 16384
MAX_FUNCTIONS = 8192
MAX_NAMES = 8192
MAX_STRING = 512


def _check(stop):
    if stop is not None and stop():
        raise ValueError("PE inspection cancelled")


class _PE:
    def __init__(self, data, stop):
        _check(stop)
        result = inspect_binary(data)
        if result['format'] != 'pe':
            raise ValueError("PE input required")
        self.data, self.stop = data, stop
        self.bits = result['metadata']['bits']
        headers = result['metadata']['header_size']
        self.regions = [(0, headers, 0, headers)]
        for section in result['sections']:
            _check(stop)
            rva, raw = section['virtual_address'], section['offset']
            size = section['size']
            span = max(size, section['virtual_size'])
            if rva + span > 2**32 or (size and raw < headers):
                raise ValueError("PE section overlaps headers or exceeds RVA range")
            for start, end, file_start, file_end in self.regions:
                if span and rva < end and start < rva + span:
                    raise ValueError("Ambiguous overlapping PE RVA regions")
                if size and raw < file_end and file_start < raw + size:
                    raise ValueError("Ambiguous overlapping PE file regions")
            if span:
                self.regions.append((rva, rva + span, raw, raw + size))
        peoff = struct.unpack_from('<I', data, 0x3c)[0]
        opt = peoff + 24
        minimum = 96 if self.bits == 32 else 112
        self.ndirs = struct.unpack_from('<I', data, opt + minimum - 4)[0]
        self.dirs = opt + minimum

    def map(self, rva, size):
        _check(self.stop)
        if rva < 0 or size < 1 or rva + size > 2**32:
            raise ValueError("Invalid PE RVA range")
        for start, end, raw, raw_end in self.regions:
            if start <= rva < end:
                offset = raw + rva - start
                if rva + size > end or offset + size > raw_end:
                    raise ValueError("PE RVA is virtual-only or crosses file-backed range")
                return offset
        raise ValueError("Unmapped PE RVA")

    def unpack(self, fmt, rva):
        offset = self.map(rva, struct.calcsize(fmt))
        return struct.unpack_from(fmt, self.data, offset)

    def string(self, rva, end=None):
        offset = self.map(rva, 1)
        # Regions were validated as disjoint. Probe only this file-backed range,
        # once, rather than remapping every byte against every section.
        raw_end = next(raw_end for start, stop, _, raw_end in self.regions
                       if start <= rva < stop)
        stop = min(raw_end, offset + MAX_STRING)
        if end is not None:
            stop = min(stop, offset + max(0, end - rva))
        terminator = self.data.find(b'\0', offset, stop)
        if terminator < 0:
            raise ValueError("Unterminated or over-limit PE string")
        self.map(rva, terminator - offset + 1)
        return self.data[offset:terminator].decode('ascii', 'replace')

    def target_offset(self, rva):
        """Return a declared target's raw offset, or None for zero-fill memory.

        Table structures must still use map(): a valid export target can be
        uninitialized data, but its address table must be file-backed.
        """
        _check(self.stop)
        if rva < 0 or rva >= 2**32:
            raise ValueError("Invalid PE target RVA")
        for start, end, raw, raw_end in self.regions:
            if start <= rva < end:
                offset = raw + rva - start
                return offset if offset < raw_end else None
        raise ValueError("Unmapped PE target RVA")

    def directory(self, index):
        if index >= self.ndirs:
            return None
        rva, size = struct.unpack_from('<II', self.data, self.dirs + index * 8)
        if not rva and not size:
            return None
        if not rva or not size:
            raise ValueError("Malformed PE directory")
        self.map(rva, size)
        return rva, size


def _page(offset, available=True):
    return dict(entries=[], offset=offset, total_entries=0, next_offset=None,
                exhausted=True, available=available)


def _append(page, item, limit):
    index = page['total_entries']
    if page['offset'] <= index < page['offset'] + limit:
        page['entries'].append(item)
    page['total_entries'] += 1


def _finish(page, limit):
    following = page['offset'] + limit
    page['exhausted'] = following >= page['total_entries']
    page['next_offset'] = None if page['exhausted'] else following


def _imports(pe, page, limit, *, selected_iat_rva=None):
    directory = pe.directory(1)
    if directory is None:
        return
    rva, size = directory
    width = pe.bits // 8
    fmt = '<I' if width == 4 else '<Q'
    high = 1 << (pe.bits - 1)
    scanned = 0
    for descriptor in range(min(size // 20, MAX_DESCRIPTORS)):
        descriptor_rva = rva + descriptor * 20
        values = pe.unpack('<IIIII', descriptor_rva)
        if not any(values):
            return
        original, timestamp, _, name_rva, first = values
        if selected_iat_rva is not None and not original and timestamp:
            raise ValueError("Bound IAT without a lookup table has no reliable static import identity")
        if not name_rva or not first:
            raise ValueError("Malformed PE import descriptor")
        dll = pe.string(name_rva)
        thunk = original or first
        for index in range(MAX_THUNKS + 1):
            if scanned >= MAX_THUNKS:
                raise ValueError("PE thunk scan budget exceeded")
            scanned += 1
            thunk_rva = thunk + index * width
            pe.map(thunk, (index + 1) * width)
            value, = pe.unpack(fmt, thunk_rva)
            if not value:
                break
            pe.map(first, (index + 1) * width)
            iat_rva = first + index * width
            item = dict(dll=dll, name=None, ordinal=None, hint=None,
                        descriptor_rva=descriptor_rva,
                        descriptor_offset=pe.map(descriptor_rva, 20),
                        dll_rva=name_rva, dll_offset=pe.map(name_rva, 1),
                        thunk_rva=thunk_rva, thunk_offset=pe.map(thunk_rva, width),
                        iat_rva=iat_rva, iat_offset=pe.map(iat_rva, width))
            if value & high:
                if value & ~(high | 0xffff):
                    raise ValueError("Invalid ordinal import bits")
                item['ordinal'] = value & 0xffff
            else:
                hint, = pe.unpack('<H', value)
                item.update(hint=hint, name=pe.string(value + 2),
                            name_rva=value + 2, name_offset=pe.map(value + 2, 1))
            if selected_iat_rva is None or iat_rva == selected_iat_rva:
                _append(page, item, limit)
    raise ValueError("Unterminated PE descriptors or descriptor budget exceeded")


def _exports(pe, page, limit):
    directory = pe.directory(0)
    if directory is None:
        return
    rva, size = directory
    if size < 40:
        raise ValueError("Truncated PE export directory")
    (_, _, _, _, dll, base, functions, names, eat, npt, ordinals) = pe.unpack(
        '<IIHHIIIIIII', rva)
    if functions > MAX_FUNCTIONS or names > MAX_NAMES:
        raise ValueError("PE export count budget exceeded")
    if base + functions > 2**32:
        raise ValueError("PE export ordinal overflow")
    if dll:
        pe.string(dll)
    if functions and not eat or names and (not npt or not ordinals):
        raise ValueError("Missing PE export table RVA")
    if functions:
        pe.map(eat, functions * 4)
    if names:
        pe.map(npt, names * 4)
        pe.map(ordinals, names * 2)
    selected = {}
    for index in range(functions):
        address, = pe.unpack('<I', eat + index * 4)
        if not address:  # Address table holes are not exports.
            continue
        target_offset = pe.target_offset(address)
        forwarder = pe.string(address, rva + size) if rva <= address < rva + size else None
        item = dict(name=None, names=[], ordinal=base + index, rva=address,
                    offset=target_offset, forwarder=forwarder,
                    address_table_rva=eat + index * 4,
                    address_table_offset=pe.map(eat + index * 4, 4),
                    name_rvas=[], name_offsets=[])
        before = len(page['entries'])
        _append(page, item, limit)
        if len(page['entries']) > before:
            selected[index] = item
    for index in range(names):
        ordinal, = pe.unpack('<H', ordinals + index * 2)
        if ordinal >= functions:
            raise ValueError("PE export name ordinal outside address table")
        address, = pe.unpack('<I', eat + ordinal * 4)
        if not address:
            raise ValueError("PE export name refers to address table hole")
        name_rva, = pe.unpack('<I', npt + index * 4)
        name = pe.string(name_rva)
        if ordinal in selected:
            item = selected[ordinal]
            item['names'].append(name)
            item['name_rvas'].append(name_rva)
            item['name_offsets'].append(pe.map(name_rva, 1))
            if item['name'] is None:
                item['name'] = name


def inspect_pe_imports(data: bytes, *, offset=0, limit=50, should_stop=None) -> dict:
    """Return independent pages; offset >= 0 and 1 <= limit <= 100 required."""
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    pe = _PE(data, should_stop)
    result = dict(imports=_page(offset), exports=_page(offset),
                  symbols=_page(offset, False), limitations=[
                      'COFF/PDB symbol recovery unsupported; no symbols invented.',
                      'Delay imports unsupported; no runtime resolution or execution.',
                      'Bounded static scans; malformed/cancelled/over-budget input rejected.',
                      'Limits: 256 descriptors, 16384 thunks including terminators, 8192 functions/names, 511-byte ASCII names with replacement.',
                      'Export offset is null for virtual-only zero-fill targets; table offsets remain file-backed.',
                  ])
    _imports(pe, result['imports'], limit)
    _exports(pe, result['exports'], limit)
    for category in ('imports', 'exports'):
        _finish(result[category], limit)
    return result


def select_pe_import(data: bytes, iat_rva: int, *, should_stop=None) -> dict:
    """Validate the bounded standard table, retaining at most two slot matches."""
    if not isinstance(data, bytes) or len(data) > 8 * 1024 * 1024:
        raise ValueError("PE import selection requires bytes, at most 8 MiB")
    if type(iat_rva) is not int or not 0 <= iat_rva < 2**32:
        raise ValueError("import_iat_rva must be a nonnegative 32-bit integer")
    if should_stop is not None and not callable(should_stop):
        raise ValueError("should_stop must be callable")
    pe = _PE(data, should_stop)
    pe.map(iat_rva, pe.bits // 8)
    page = _page(0)
    _imports(pe, page, 2, selected_iat_rva=iat_rva)
    if page['total_entries'] != 1:
        raise ValueError("Standard PE import IAT slot missing or ambiguous; delay/runtime resolution unsupported")
    return dict(page['entries'][0], pointer_width=pe.bits // 8)
