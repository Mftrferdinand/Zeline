"""Bounded exact-name navigation to declared code, without target execution."""
from zeline.reverse_binary import inspect_binary
from zeline import reverse_elf_symbols as elf

MAX_INPUT = 8 * 1024 * 1024
MAX_SOURCES = 8


def _check(stop):
    if stop is not None and stop():
        raise InterruptedError('symbol navigation cancelled')


def resolve_code_symbol(data: bytes, name: str, *, architecture='', should_stop=None) -> dict:
    """Select a code start, not a recovered function extent or runtime provider."""
    if not isinstance(data, bytes) or len(data) > MAX_INPUT:
        raise ValueError('symbol navigation requires bytes, at most 8 MiB')
    if not isinstance(name, str) or not name or '\0' in name or len(name) > 512:
        raise ValueError('symbol must be an exact nonempty name of at most 512 characters')
    if not isinstance(architecture, str):
        raise ValueError('architecture must be a string')
    if should_stop is not None and not callable(should_stop):
        raise ValueError('should_stop must be callable')
    _check(should_stop)
    def checked_stop():
        _check(should_stop)
        return False

    header = inspect_binary(data)
    selected = None
    count = 0

    def add(offset, address, mode, source):
        nonlocal selected, count
        key = (offset, address, mode)
        if selected is not None and key != selected['_key']:
            raise ValueError('ambiguous symbol: name identifies multiple code starts')
        if selected is None:
            selected = dict(_key=key, byte_offset=offset, architecture=mode,
                            selected_symbol=dict(name=name, address=address, sources=[]))
        count += 1
        if len(selected['selected_symbol']['sources']) < MAX_SOURCES:
            selected['selected_symbol']['sources'].append(source)

    if header['format'] == 'elf':
        wanted = name.encode('utf-8') + b'\0'
        bits, order, sections = elf._read_elf_layout(data, checked_stop)
        tables = elf._prepare_tables(data, sections, bits, checked_stop)
        for table in tables:
            strings = elf._string_table(data, sections, table['link'])
            for index in range(table['count']):
                _check(should_stop)
                record, _, _ = elf._read_symbol(data, table, index, bits, order, strings, len(sections))
                if record['name'] != name or not record['defined'] or record['type'] not in ('func', 'notype'):
                    continue
                # Enumeration may display replacement decoding; selection must
                # still identify the original UTF-8 name without byte collisions.
                entry = record['file_offset']
                name_index = int.from_bytes(data[entry:entry + 4], 'little' if order == '<' else 'big')
                if strings[name_index:name_index + len(wanted)] != wanted:
                    continue
                section_index = record['section_index']
                if not 0 < section_index < len(header['sections']):
                    continue  # Reserved/absolute symbols are not file-backed code declarations.
                section = header['sections'][section_index]
                if not section['file_backed'] or not section['flags'] & 4 or section['flags'] & 0x800:
                    continue  # Compressed code is unsupported; never decode compression headers.
                value, mode = record['value'], ''
                if header['metadata']['machine'] == 40 and record['type'] == 'func':
                    # AAELF32 tags Thumb FUNC values, not arbitrary NOTYPE addresses.
                    mode = 'thumb' if value & 1 else 'arm'
                    value &= ~1
                address = section['address'] + value if header['metadata']['type'] == 1 else value
                delta = address - section['address']
                if not 0 <= delta < section['size']:
                    continue
                add(section['offset'] + delta, address, mode,
                    dict(table=record['table'], entry_file_offset=record['file_offset'],
                         type=record['type'], declared_size=record['size']))
    else:
        from zeline import reverse_pe_imports as pe
        # PE names are ASCII. Never match a replacement-decoded byte spelling.
        try:
            name.encode('ascii')
        except UnicodeEncodeError as error:
            raise ValueError('PE symbol selection requires a lossless ASCII name') from error
        # Validate every table/name, including records outside the selection, but
        # retain at most one page item rather than materializing all 8192 exports.
        pe.inspect_pe_imports(data, offset=pe.MAX_FUNCTIONS, limit=1, should_stop=checked_stop)
        layout = pe._PE(data, checked_stop)
        directory = layout.directory(0)
        if directory:
            rva, span = directory
            fields = layout.unpack('<IIHHIIIIIII', rva)
            base, names, eat, npt, ordinals = fields[5], fields[7], fields[8], fields[9], fields[10]
            for index in range(names):
                _check(should_stop)
                name_rva, = layout.unpack('<I', npt + index * 4)
                if layout.string(name_rva) != name:
                    continue
                ordinal, = layout.unpack('<H', ordinals + index * 2)
                target, = layout.unpack('<I', eat + ordinal * 4)
                if rva <= target < rva + span:
                    continue  # Forwarded export strings are not local code.
                mode = ''
                if header['metadata']['machine'] in (0x1c0, 0x1c2, 0x1c4):
                    mode = 'thumb' if target & 1 or header['metadata']['machine'] != 0x1c0 else 'arm'
                    target &= ~1
                offset = layout.target_offset(target)
                if offset is not None:
                    add(offset, header['metadata']['image_base'] + target, mode,
                        dict(table='pe_exports', entry_file_offset=layout.map(eat + ordinal * 4, 4),
                             ordinal=base + ordinal))
    _check(should_stop)
    if selected is None:
        raise ValueError('no eligible file-backed code symbol with that exact name')
    from zeline.reverse_code_regions import select_code_region
    mapped = select_code_region(data, byte_offset=selected['byte_offset'],
                                architecture=architecture or selected['architecture'], should_stop=should_stop)
    if mapped['address'] != selected['selected_symbol']['address']:
        raise ValueError('symbol address does not match executable section mapping')
    selected.pop('_key')
    selected['selected_symbol']['source_count'] = count
    selected['selected_symbol']['sources_truncated'] = count > MAX_SOURCES
    selected['limitations'] = [
        'Exact name only; no demangling, provider lookup or source-name guessing.',
        'Symbol declarations locate a starting byte only, not a validated function extent.',
        'Disassembly can cross the declared symbol size; byte-cursor continuation is section-bounded.',
        'Only ELF FUNC/NOTYPE or named local PE exports in executable sections; no COFF/PDB or IFUNC resolution.',
        'ELF OBJECT, non-executable data, forwarded/virtual-only exports and compressed code cannot be selected.',
        'PE export types are unknown; executable section eligibility is not proof of a function.',
    ]
    return selected
