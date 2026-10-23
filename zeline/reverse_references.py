"""Paged incoming direct-transfer observations; no target execution or CFG recovery."""
from zeline.reverse_binary import inspect_binary
from zeline.reverse_code_regions import select_code_region
from zeline.reverse_disassembly import decode_instructions


def _check(should_stop):
    if should_stop is not None and should_stop():
        raise InterruptedError('reference scan cancelled')


def scan_references(data: bytes, *, target_address=None, symbol='', byte_offset=None,
                    architecture='', limit=50, should_stop=None) -> dict:
    """Decode at most one 4096-byte/100-instruction page, preserving gaps."""
    if not isinstance(data, bytes) or len(data) > 8 * 1024 * 1024:
        raise ValueError('reference scan requires bytes, at most 8 MiB')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('reference limit is an instruction budget between 1 and 100')
    if should_stop is not None and not callable(should_stop):
        raise ValueError('should_stop must be callable')
    if not isinstance(symbol, str) or bool(symbol) == (target_address is not None):
        raise ValueError('provide exactly one symbol or target_address')
    _check(should_stop)
    header = inspect_binary(data)
    kind = header['format']
    if kind == 'elf' and header['metadata']['type'] not in (2, 3):
        raise ValueError('reference scan requires linked ELF EXEC/DYN; relocations are not applied')
    regions = []
    for section in header['sections']:
        _check(should_stop)
        executable = section['flags'] & 4 if kind == 'elf' else section['characteristics'] & 0x20000000
        if not executable or not section['file_backed'] or not section['size']:
            continue
        if kind == 'elf' and section['flags'] & 0x800:
            raise ValueError('compressed executable code cannot be scanned')
        size = section['size']
        if kind == 'pe' and section['virtual_size']:
            size = min(size, section['virtual_size'])
        regions.append(dict(offset=section['offset'], size=size))
    regions.sort(key=lambda region: region['offset'])
    if not regions:
        raise ValueError('no file-backed executable code region')
    start = regions[0]['offset'] if byte_offset is None else byte_offset
    # Shared mapping validates all regions, widths, reserved spans and overlaps.
    selection = select_code_region(data, byte_offset=start, architecture=architecture, should_stop=should_stop)
    target = {}
    if symbol:
        from zeline.reverse_symbol_navigation import resolve_code_symbol
        # Target declaration mode is independent from the source scan ISA.
        navigation = resolve_code_symbol(data, symbol, should_stop=should_stop)
        target['declaration'] = navigation['selected_symbol']
        target_address = navigation['selected_symbol']['address']

    bits = 32 if selection['architecture'] in ('x86', 'arm', 'thumb') else 64
    if type(target_address) is not int or not 0 <= target_address < 1 << bits:
        raise ValueError('target_address must fit the source ISA address width')
    window = data[start:start + min(selection['bytes_remaining'], 4096)]
    decoded = decode_instructions(window, selection['architecture'], address=selection['address'],
                                  file_offset=start, limit=limit, endianness=selection['endianness'],
                                  should_stop=should_stop)
    _check(should_stop)
    consumed = decoded['bytes_consumed']
    if type(consumed) is not int or not 0 <= consumed <= len(window):
        raise ValueError('invalid reference worker byte count')
    matches = []
    unresolved = 0
    for row in decoded['instructions']:
        _check(should_stop)
        flow = row['flow']
        if flow['kind'] not in ('call', 'jump'):
            continue
        if flow['target'] is None:
            unresolved += 1
        elif flow['target'] == target_address:
            matches.append({**{key: row[key] for key in ('address', 'file_offset', 'size', 'bytes_hex', 'mnemonic', 'op_str')},
                            'kind': flow['kind'], 'target': flow['target'], 'conditional': flow['conditional']})
    remaining = selection['bytes_remaining'] - consumed
    region_index = next(i for i, region in enumerate(regions) if region['offset'] <= start < region['offset'] + region['size'])
    before = sum(region['size'] for region in regions[:region_index]) + start - regions[region_index]['offset']
    total = sum(region['size'] for region in regions)
    next_region = regions[region_index + 1]['offset'] if region_index + 1 < len(regions) else None
    next_offset = None
    if consumed and decoded['stop_reason'] != 'undecodable':
        next_offset = start + consumed if remaining else next_region
    limitations = selection['limitations'] + decoded['limitations'] + [
        'Only direct call/jump immediate targets in this linear page are matched; indirect/PLT/IAT providers are unresolved.',
        'No matches is not proof of absence; caller cursors do not prove earlier pages were scanned.',
        'Coverage is page-local declared executable bytes, not reachable code or whole-program completeness.',
        'One uniform source ISA/mode per page; target symbol mode does not establish caller code mode.',
        'Preserve artifact SHA256 and source ISA across continuation pages; addresses are declared, not runtime relocated.']
    return dict(format=kind, data_endianness=selection['data_endianness'],
                target=dict(address=target_address, **target), matches=matches,
                architecture=selection['architecture'], endianness=selection['endianness'],
                region=selection['region'], decoder=decoded['decoder'],
                unresolved_calls_jumps=unresolved,
                coverage=dict(scope='decoded_page_only', start_byte_offset=start, end_byte_offset=start + consumed,
                              bytes_decoded=consumed, instructions_decoded=len(decoded['instructions']),
                              eligible_bytes_total=total, eligible_bytes_before_page=before,
                              eligible_bytes_after_page=total - before - consumed,
                              region_index=region_index, regions_total=len(regions), whole_program_complete=False),
                next_byte_offset=next_offset, next_region_byte_offset=next_region,
                exhausted=remaining == 0 and region_index == len(regions) - 1,
                stop_reason='end_of_region' if remaining == 0 else decoded['stop_reason'],
                limitations=limitations)
