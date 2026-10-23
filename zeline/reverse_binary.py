"""Bounded, static ELF/PE header inspection; never loads or executes input.

Only headers and section descriptors are interpreted. Limits are policy, not
claims of full loader validation. Unsupported ELF extended numbering is rejected.
"""

import struct

MAX_SECTIONS = 256
MAX_NAME_BYTES = 128
MAX_INPUT_BYTES = 64 * 1024 * 1024
_LIMITATIONS = [
    "No decompilation, import recovery, or symbol recovery.",
    "Headers and sections only; not a loader or full executable validator.",
    "ELF extended numbering is unsupported; section names are capped at 128 bytes.",
]
_ARCH = {3: "x86", 40: "arm", 62: "x86_64", 183: "aarch64", 243: "riscv"}


def _bounds(data, offset, size):
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise ValueError("Binary range outside input")


def _unpack(data, fmt, offset):
    _bounds(data, offset, struct.calcsize(fmt))
    return struct.unpack_from(fmt, data, offset)


def _result(kind, metadata, sections):
    return {
        "format": kind,
        "metadata": metadata,
        "sections": sections,
        "limitations": list(_LIMITATIONS),
    }


def _elf(data):
    _bounds(data, 0, 16)
    cls, encoding, version = data[4:7]
    if cls not in (1, 2) or encoding not in (1, 2) or version != 1:
        raise ValueError("Invalid ELF identification")
    bits = 32 if cls == 1 else 64
    order = "<" if encoding == 1 else ">"
    hs, ss = (52, 40) if bits == 32 else (64, 64)
    fmt = "HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH"
    (
        kind,
        machine,
        version,
        entry,
        phoff,
        shoff,
        flags,
        ehsize,
        phsize,
        phnum,
        shsize,
        shnum,
        names_index,
    ) = _unpack(data, order + fmt, 16)
    if version != 1 or ehsize < hs:
        raise ValueError("Invalid ELF header")
    _bounds(data, 0, ehsize)
    if phnum == 0xFFFF or (shoff and not shnum) or names_index == 0xFFFF:
        raise ValueError("Unsupported ELF extended numbering")
    if phnum:
        if phoff < ehsize or phsize < (32 if bits == 32 else 56):
            raise ValueError("Invalid ELF program table")
        _bounds(data, phoff, phnum * phsize)
    if shnum > MAX_SECTIONS:
        raise ValueError("Section count exceeds limit")
    if shnum:
        if shoff < ehsize or shsize < ss or names_index >= shnum:
            raise ValueError("Invalid ELF section table")
        _bounds(data, shoff, shnum * shsize)
    elif shoff or names_index:
        raise ValueError("Invalid empty ELF section table")
    raw = []
    fmt = "IIIIIIIIII" if bits == 32 else "IIQQQQIIQQ"
    for i in range(shnum):
        values = _unpack(data, order + fmt, shoff + i * shsize)
        name, stype, sflags, addr, offset, size, link, info, align, entsize = values
        if stype not in (0, 8):  # NULL and NOBITS have no file payload.
            _bounds(data, offset, size)
        raw.append(values)
    names_offset = names_size = 0
    if names_index:
        names = raw[names_index]
        if names[1] != 3:
            raise ValueError("ELF section names must use a string table")
        names_offset, names_size = names[4:6]
    sections = []
    for name, stype, sflags, addr, offset, size, link, info, align, entsize in raw:
        label = ""
        if names_index:
            if name >= names_size:
                raise ValueError("Invalid ELF section name offset")
            start = names_offset + name
            end = min(names_offset + names_size, start + MAX_NAME_BYTES)
            label = data[start:end].split(b"\0", 1)[0].decode("ascii", "replace")
        sections.append(
            {
                "name": label,
                "offset": offset,
                "size": size,
                "type": stype,
                "flags": sflags,
                "address": addr,
                "file_backed": stype not in (0, 8),
            }
        )
    return _result(
        "elf",
        {
            "bits": bits,
            "endianness": "little" if encoding == 1 else "big",
            "architecture": _ARCH.get(machine, "unknown"),
            "machine": machine,
            "entrypoint": entry,
            "type": kind,
            "flags": flags,
            "section_count": shnum,
            "section_table_offset": shoff,
            "program_header_count": phnum,
            "program_header_offset": phoff,
        },
        sections,
    )


def _pe(data):
    _bounds(data, 0, 64)
    (peoff,) = _unpack(data, "<I", 0x3C)
    if peoff < 64:
        raise ValueError("Invalid PE header offset")
    _bounds(data, peoff, 24)
    if data[peoff : peoff + 4] != b"PE\0\0":
        raise ValueError("Invalid PE signature")
    machine, count, timestamp, symoff, symcount, optsize, flags = _unpack(
        data, "<HHIIIHH", peoff + 4
    )
    if count > MAX_SECTIONS:
        raise ValueError("Section count exceeds limit")
    opt = peoff + 24
    _bounds(data, opt, optsize)
    (magic,) = _unpack(data, "<H", opt)
    if magic not in (0x10B, 0x20B):
        raise ValueError("Unsupported PE optional header")
    bits = 32 if magic == 0x10B else 64
    minimum = 96 if bits == 32 else 112
    if optsize < minimum:
        raise ValueError("Truncated PE optional header")
    (entry,) = _unpack(data, "<I", opt + 16)
    (base,) = _unpack(data, "<I" if bits == 32 else "<Q", opt + (28 if bits == 32 else 24))
    image_size, header_size = _unpack(data, "<II", opt + 56)
    (dirs,) = _unpack(data, "<I", opt + minimum - 4)
    if dirs > (optsize - minimum) // 8:
        raise ValueError("PE data directories exceed optional header")
    section_offset = opt + optsize
    _bounds(data, section_offset, count * 40)
    if header_size < section_offset + count * 40:
        raise ValueError("PE headers do not cover section table")
    _bounds(data, 0, header_size)
    sections = []
    for i in range(count):
        name, virtual_size, address, size, offset, reloc, lines, nr, nl, attrs = _unpack(
            data, "<8sIIIIIIHHI", section_offset + i * 40
        )
        if size:
            _bounds(data, offset, size)
        sections.append(
            {
                "name": name.split(b"\0", 1)[0].decode("ascii", "replace"),
                "offset": offset,
                "size": size,
                "virtual_size": virtual_size,
                "virtual_address": address,
                "characteristics": attrs,
                "file_backed": bool(size),
            }
        )
    architecture = {
        0x14C: "x86",
        0x8664: "x86_64",
        0x1C0: "arm",
        0x1C4: "arm",
        0xAA64: "aarch64",
    }.get(machine, "unknown")
    return _result(
        "pe",
        {
            "bits": bits,
            "endianness": "little",
            "machine": machine,
            "architecture": architecture,
            "entrypoint": base + entry,
            "entrypoint_rva": entry,
            "image_base": base,
            "image_size": image_size,
            "header_size": header_size,
            "characteristics": flags,
            "section_count": count,
            "section_table_offset": section_offset,
        },
        sections,
    )


def inspect_binary(data: bytes) -> dict:
    """Inspect bytes, raising ValueError for unknown, malformed or over-limit input."""
    if not isinstance(data, bytes):
        raise ValueError("Input must be bytes")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds 64 MiB limit")
    if data.startswith(b"\x7fELF"):
        return _elf(data)
    if data.startswith(b"MZ"):
        return _pe(data)
    raise ValueError("Unknown binary format")


def inspect_binary_details(data: bytes, *, offset=0, limit=50, should_stop=None) -> dict:
    """Preserve header inspection and add bounded, paginated static file tables.

    Header-only inspect_binary remains unchanged for library callers. Import
    and export observations are not runtime provider resolution or a loader.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('offset must be nonnegative and limit must be between 1 and 100')
    if should_stop is not None and should_stop():
        raise InterruptedError('binary analysis cancelled')
    header = inspect_binary(data)
    if header['format'] == 'elf':
        from zeline.reverse_elf_symbols import inspect_elf_symbols
        details = inspect_elf_symbols(data, offset=offset, limit=limit, should_stop=should_stop)
    else:
        from zeline.reverse_pe_imports import inspect_pe_imports
        details = inspect_pe_imports(data, offset=offset, limit=limit, should_stop=should_stop)
    if should_stop is not None and should_stop():
        raise InterruptedError('binary analysis cancelled')
    header['limitations'] = [
        'Static file tables only; no decompilation or runtime provider resolution.',
        'Not a loader or complete executable validator.',
        *(_LIMITATIONS[2:] if header['format'] == 'elf' else []),
        *details.pop('limitations'),
    ]
    header.update(details)
    return header
