"""Bounded, declared executable-region mapping; not a loader or relocation engine."""
import struct
from zeline.reverse_binary import inspect_binary

MAX_BYTES = 8 * 1024 * 1024
ARCHITECTURES = ('x86', 'x86_64', 'arm', 'thumb', 'aarch64')


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(name + ' must be a nonnegative integer')


def _check(should_stop):
    if should_stop is not None and should_stop():
        raise InterruptedError('code mapping cancelled')


def _address_range(address, size, architecture):
    bits = 32 if architecture in ('x86', 'arm', 'thumb') else 64
    if address < 0 or address >= 1 << bits or size > (1 << bits) - address:
        raise ValueError('code region exceeds architectural address width')


def select_code_region(data: bytes, *, byte_offset=None, architecture='', base_address=None,
                       should_stop=None) -> dict:
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError('code mapping requires bytes, at most 8 MiB')
    if not isinstance(architecture, str) or (architecture and architecture not in ARCHITECTURES):
        raise ValueError('unsupported architecture')
    if should_stop is not None and not callable(should_stop):
        raise ValueError('should_stop must be callable')
    if byte_offset is not None:
        _integer(byte_offset, 'byte_offset')
    if base_address is not None:
        _integer(base_address, 'base_address')
    _check(should_stop)
    limitations = ['Declared executable regions only; no loader, relocations or proof that bytes are code.',
                   'Linear page decoding does not recover mixed ARM/Thumb mode or prior Thumb IT state.']
    if not data.startswith((b'\x7fELF', b'MZ')):
        if not architecture:
            raise ValueError('raw code requires an explicit architecture')
        address = 0 if base_address is None else base_address
        _address_range(address, len(data), architecture)
        regions = [dict(name='raw', file_offset=0, size=len(data), address=address)]
        kind, endian, entry = 'raw', 'little', address
        limitations.append('Raw bytes and base address are caller assertions, not validated executable metadata.')
    else:
        if base_address is not None:
            raise ValueError('base_address is raw-only; binary relocation overrides are unsupported')
        header = inspect_binary(data)
        _check(should_stop)
        kind, metadata = header['format'], header['metadata']
        bits, machine = metadata['bits'], metadata['machine']
        inferred = ({(3, 32): 'x86', (62, 64): 'x86_64', (40, 32): 'arm', (183, 64): 'aarch64'}
                    if kind == 'elf' else
                    {(0x14c, 32): 'x86', (0x8664, 64): 'x86_64', (0x1c0, 32): 'arm',
                     (0x1c2, 32): 'thumb', (0x1c4, 32): 'thumb', (0xaa64, 64): 'aarch64'}).get((machine, bits))
        if inferred is None:
            raise ValueError('unsupported or inconsistent binary architecture')
        entry = metadata['entrypoint']
        if inferred in ('arm', 'thumb') and entry & 1:
            inferred, entry = 'thumb', entry & ~1
        if architecture and architecture != inferred:
            if {architecture, inferred} <= {'arm', 'thumb'}:
                limitations.append('Caller selected ARM/Thumb mode; mixed-mode inference is unsupported.')
            else:
                raise ValueError('incompatible binary architecture override')
        architecture = architecture or inferred
        endian = metadata['endianness']
        if architecture in ('x86', 'x86_64') and endian != 'little':
            raise ValueError('x86 cannot be decoded as big-endian')
        if kind == 'elf':
            order = '<' if endian == 'little' else '>'
            ehsize = struct.unpack_from(order + 'H', data, 40 if bits == 32 else 52)[0]
            shsize = struct.unpack_from(order + 'H', data, 46 if bits == 32 else 58)[0]
            phsize = struct.unpack_from(order + 'H', data, 42 if bits == 32 else 54)[0]
            reserved = [(0, ehsize),
                        (metadata['section_table_offset'], metadata['section_count'] * shsize),
                        (metadata['program_header_offset'], metadata['program_header_count'] * phsize)]
            if metadata['type'] == 1:
                limitations.append('ELF ET_REL addresses are section-relative; relocations are not applied.')
        else:
            reserved = [(0, metadata['header_size'])]
        regions = []
        for section in header['sections']:
            _check(should_stop)
            executable = section['flags'] & 4 if kind == 'elf' else section['characteristics'] & 0x20000000
            if not executable or not section['file_backed'] or not section['size']:
                continue
            size = section['size']
            if kind == 'pe' and section['virtual_size']:
                size = min(size, section['virtual_size'])
            offset = section['offset']
            address = section['address'] if kind == 'elf' else metadata['image_base'] + section['virtual_address']
            _address_range(address, size, architecture)
            if any(length and offset < start + length and start < offset + size for start, length in reserved):
                raise ValueError('executable section overlaps binary headers or descriptor tables')
            regions.append(dict(name=section['name'], file_offset=offset, size=size, address=address))
        # All bounded candidates are checked, not merely the requested page.
        for index, region in enumerate(regions):
            _check(should_stop)
            for other in regions[:index]:
                if (region['file_offset'] < other['file_offset'] + other['size']
                        and other['file_offset'] < region['file_offset'] + region['size']):
                    raise ValueError('ambiguous overlapping executable file regions')
                if (region['address'] < other['address'] + other['size']
                        and other['address'] < region['address'] + region['size']):
                    raise ValueError('ambiguous overlapping executable address regions')
    if not regions or not any(region['size'] for region in regions):
        raise ValueError('no file-backed executable code region')
    if byte_offset is None:
        candidates = [region for region in regions if region['address'] <= entry < region['address'] + region['size']]
        region = candidates[0] if candidates else regions[0]
        byte_offset = region['file_offset'] + entry - region['address'] if candidates else region['file_offset']
        if not candidates:
            limitations.append('No file-backed executable entrypoint; selected first declared executable section.')
    else:
        candidates = [region for region in regions if region['file_offset'] <= byte_offset < region['file_offset'] + region['size']]
        if len(candidates) != 1:
            raise ValueError('byte_offset must identify exactly one executable file region')
        region = candidates[0]
        limitations.append('Caller offset is not proof of an instruction boundary.')
    data_endian = endian
    if kind == 'elf' and (architecture == 'aarch64' or
                          (architecture in ('arm', 'thumb') and metadata['flags'] & 0x00800000)):
        # A64 and ARM BE8 fetch instructions LE even when ELF data/headers are BE.
        endian = 'little'
        if data_endian != endian:
            limitations.append('Instruction encoding is little-endian despite big-endian ELF data (A64/BE8).')
    address = region['address'] + byte_offset - region['file_offset']
    alignment = 4 if architecture in ('arm', 'aarch64') else 2 if architecture == 'thumb' else 1
    if address % alignment:
        raise ValueError('unaligned architectural instruction address')
    _check(should_stop)
    return dict(format=kind, architecture=architecture, endianness=endian, data_endianness=data_endian, region=region,
                byte_offset=byte_offset, address=address,
                bytes_remaining=region['size'] - (byte_offset - region['file_offset']), limitations=limitations)
