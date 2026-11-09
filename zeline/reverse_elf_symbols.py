"""Bounded, read-only ELF section symbol recovery.

The parser intentionally reads only ELF headers, section descriptors, symbol
entries, and their linked string tables. It never loads or executes the input.
"""

from __future__ import annotations

import struct


SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_DYNSYM = 11
SHN_UNDEF = 0
SHN_XINDEX = 0xFFFF
SHN_LORESERVE = 0xFF00

# These are policy limits: they keep malformed declarations from turning a
# bounded page request into an unbounded scan or allocation.
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_SECTIONS = 256
MAX_NAME_BYTES = 512
MAX_SYMBOL_TABLE_ENTRIES = 100_000
MAX_TOTAL_SYMBOL_ENTRIES = 200_000

_BINDINGS = {
    0: "local",
    1: "global",
    2: "weak",
    10: "gnu_unique",
}
_TYPES = {
    0: "notype",
    1: "object",
    2: "func",
    3: "section",
    4: "file",
    5: "common",
    6: "tls",
    10: "gnu_ifunc",
}


def _check_stop(should_stop):
    if should_stop is not None and should_stop():
        raise InterruptedError("ELF symbol analysis cancelled")


def _bounds(data: bytes, start: int, size: int, label: str) -> None:
    if start < 0 or size < 0 or start > len(data) or size > len(data) - start:
        raise ValueError(f"{label} is outside input")


def _unpack(data: bytes, fmt: str, start: int, label: str):
    size = struct.calcsize(fmt)
    _bounds(data, start, size, label)
    return struct.unpack_from(fmt, data, start)


def _page(offset: int, limit: int, entries, total: int, available: bool) -> dict:
    next_offset = offset + limit if offset + limit < total else None
    return {
        "entries": entries,
        "offset": offset,
        "total_entries": total,
        "next_offset": next_offset,
        "exhausted": next_offset is None,
        "available": available,
    }


class _PageCollector:
    """Retain only the requested page while counting every matching record."""

    def __init__(self, offset: int, limit: int, available: bool):
        self.offset = offset
        self.limit = limit
        self.available = available
        self.entries = []
        self.total = 0

    def add(self, record: dict) -> None:
        index = self.total
        self.total += 1
        if self.offset <= index < self.offset + self.limit:
            self.entries.append(record)

    def result(self) -> dict:
        return _page(self.offset, self.limit, self.entries, self.total, self.available)


def _validate_options(data, offset, limit):
    if not isinstance(data, bytes):
        raise ValueError("Input must be bytes")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds 64 MiB limit")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")


def _read_elf_layout(data: bytes, should_stop=None):
    _bounds(data, 0, 16, "ELF identification")
    if data[:4] != b"\x7fELF":
        raise ValueError("ELF magic is missing")
    elf_class, encoding, ident_version = data[4:7]
    if elf_class not in (1, 2) or encoding not in (1, 2) or ident_version != 1:
        raise ValueError("Invalid ELF identification")

    bits = 32 if elf_class == 1 else 64
    order = "<" if encoding == 1 else ">"
    header_size = 52 if bits == 32 else 64
    section_header_size = 40 if bits == 32 else 64
    program_header_minimum = 32 if bits == 32 else 56
    header_format = "HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH"
    (
        _elf_type,
        _machine,
        elf_version,
        _entry,
        program_offset,
        section_offset,
        _flags,
        declared_header_size,
        program_entry_size,
        program_count,
        declared_section_size,
        section_count,
        section_names_index,
    ) = _unpack(data, order + header_format, 16, "ELF header")

    if elf_version != 1 or declared_header_size < header_size:
        raise ValueError("Invalid ELF header")
    _bounds(data, 0, declared_header_size, "ELF header")
    if program_count == SHN_XINDEX or section_names_index == SHN_XINDEX:
        raise ValueError("ELF extended numbering is unsupported")
    if section_count == 0 and section_offset:
        raise ValueError("ELF extended section numbering is unsupported")
    if section_count > MAX_SECTIONS:
        raise ValueError("ELF section count exceeds safe limit")

    if program_count:
        if program_offset < declared_header_size or program_entry_size < program_header_minimum:
            raise ValueError("Invalid ELF program table")
        _bounds(data, program_offset, program_count * program_entry_size, "ELF program table")

    if section_count == 0:
        if section_names_index:
            raise ValueError("ELF section-name index is invalid without sections")
        return bits, order, []

    if declared_section_size < section_header_size or section_offset < declared_header_size:
        raise ValueError("Invalid ELF section table")
    _bounds(
        data,
        section_offset,
        section_count * declared_section_size,
        "ELF section table",
    )

    section_format = "IIIIIIIIII" if bits == 32 else "IIQQQQIIQQ"
    sections = []
    for index in range(section_count):
        _check_stop(should_stop)
        values = _unpack(
            data,
            order + section_format,
            section_offset + index * declared_section_size,
            f"ELF section {index}",
        )
        name, section_type, _flags, _address, file_offset, size, link, _info, _align, entsize = values
        if section_type not in (0, 8):  # NULL and NOBITS have no file payload.
            _bounds(data, file_offset, size, f"ELF section {index}")
        sections.append(
            {
                "type": section_type,
                "offset": file_offset,
                "size": size,
                "link": link,
                "entsize": entsize,
                "header_index": index,
                "name_offset": name,
            }
        )

    if section_names_index >= section_count:
        raise ValueError("ELF section-name index is outside the section table")
    if section_names_index:
        names = sections[section_names_index]
        if names["type"] != SHT_STRTAB:
            raise ValueError("ELF section names must use a string table")
    return bits, order, sections


def _string_table(data: bytes, sections, table_index: int):
    table = sections[table_index]
    if table["type"] != SHT_STRTAB:
        raise ValueError("ELF symbol table link must reference SHT_STRTAB")
    _bounds(data, table["offset"], table["size"], "ELF string table")
    if table["size"] and data[table["offset"]] != 0:
        raise ValueError("ELF string table must start with NUL")
    return data[table["offset"] : table["offset"] + table["size"]]


def _prepare_tables(data: bytes, sections, bits: int, should_stop=None):
    expected_entry_size = 16 if bits == 32 else 24
    tables = []
    total_declared = 0
    for index, section in enumerate(sections):
        section_type = section["type"]
        if section_type not in (SHT_SYMTAB, SHT_DYNSYM):
            continue
        _check_stop(should_stop)
        link = section["link"]
        if link >= len(sections):
            raise ValueError(f"ELF symbol table {index} has an invalid sh_link")
        if sections[link]["type"] != SHT_STRTAB:
            raise ValueError(f"ELF symbol table {index} sh_link is not SHT_STRTAB")
        entsize = section["entsize"]
        if entsize != expected_entry_size:
            raise ValueError(
                f"ELF symbol table {index} has invalid sh_entsize {entsize}; "
                f"expected {expected_entry_size}"
            )
        size = section["size"]
        if size % entsize:
            raise ValueError(f"ELF symbol table {index} size is not entry-aligned")
        count = size // entsize
        if count > MAX_SYMBOL_TABLE_ENTRIES:
            raise ValueError(f"ELF symbol table {index} exceeds safe entry budget")
        total_declared += count
        if total_declared > MAX_TOTAL_SYMBOL_ENTRIES:
            raise ValueError("ELF symbol tables exceed the safe total entry budget")
        _bounds(data, section["offset"], size, f"ELF symbol table {index}")
        tables.append(
            {
                "index": index,
                "kind": "dynsym" if section_type == SHT_DYNSYM else "symtab",
                "offset": section["offset"],
                "count": count,
                "link": link,
            }
        )
    return tables


def _symbol_name(string_data: bytes, name_offset: int) -> str:
    if name_offset >= len(string_data):
        raise ValueError("ELF symbol st_name is outside its string table")
    end = string_data.find(b"\0", name_offset, name_offset + MAX_NAME_BYTES + 1)
    if end < 0:
        raise ValueError("ELF symbol name is not NUL-terminated within name limit")
    return string_data[name_offset:end].decode("utf-8", "replace")


def _read_symbol(data: bytes, table, index: int, bits: int, order: str, string_data: bytes, section_count: int):
    entry_offset = table["offset"] + index * (16 if bits == 32 else 24)
    if bits == 32:
        name_offset, value, size, info, other, section_index = _unpack(
            data, order + "IIIBBH", entry_offset, "ELF32 symbol entry"
        )
    else:
        name_offset, info, other, section_index, value, size = _unpack(
            data, order + "IBBHQQ", entry_offset, "ELF64 symbol entry"
        )
    if section_index == SHN_XINDEX:
        raise ValueError("ELF extended symbol section indexes are unsupported")
    if section_index < SHN_LORESERVE and section_index >= section_count:
        raise ValueError("ELF symbol section index is outside the section table")
    name = _symbol_name(string_data, name_offset)
    binding_value = info >> 4
    type_value = info & 0x0F
    return (
        {
            "name": name,
            "binding": _BINDINGS.get(binding_value, f"unknown({binding_value})"),
            "type": _TYPES.get(type_value, f"unknown({type_value})"),
            "value": value,
            "size": size,
            "section_index": section_index,
            "defined": section_index != SHN_UNDEF,
            "table": table["kind"],
            "file_offset": entry_offset,
        },
        binding_value,
        other & 0x03,
    )


def _limitations(tables):
    limitations = [
        "Section-backed ELF symbol recovery only; no dynamic loader or provider resolution.",
        "ELF extended section and symbol indexes are unsupported.",
        "Symbol names are at most 512 bytes, decoded as UTF-8 with replacement; file_offset locates the symbol entry, not its code.",
        "At most 100000 entries per symbol table and 200000 across all symbol tables; over-budget input is rejected.",
    ]
    has_dynsym = any(table["kind"] == "dynsym" for table in tables)
    has_symtab = any(table["kind"] == "symtab" for table in tables)
    if not tables:
        limitations.append(
            "No SHT_SYMTAB or SHT_DYNSYM section was found; import/export coverage is unavailable, not proof of absence."
        )
    elif not has_dynsym:
        limitations.append(
            "No SHT_DYNSYM section was found; imports and exports are unavailable, not proof of absence."
        )
    if not has_symtab:
        limitations.append("No SHT_SYMTAB section was found; ordinary symbol-table coverage is unavailable.")
    return limitations


def inspect_elf_symbols(data: bytes, *, offset=0, limit=50, should_stop=None) -> dict:
    """Return bounded pages of section-backed ELF symbols.

    ``offset`` is the zero-based entry offset within each independent page
    category, not a byte offset. Malformed declarations raise ``ValueError``;
    cancellation raises ``InterruptedError`` rather than returning partial data.
    """
    _validate_options(data, offset, limit)
    _check_stop(should_stop)
    bits, order, sections = _read_elf_layout(data, should_stop)
    _check_stop(should_stop)
    tables = _prepare_tables(data, sections, bits, should_stop)
    has_dynsym = any(table["kind"] == "dynsym" for table in tables)
    has_symbols = bool(tables)
    imports = _PageCollector(offset, limit, has_dynsym)
    exports = _PageCollector(offset, limit, has_dynsym)
    symbols = _PageCollector(offset, limit, has_symbols)

    for table in tables:
        _check_stop(should_stop)
        string_data = _string_table(data, sections, table["link"])
        for index in range(table["count"]):
            _check_stop(should_stop)
            record, binding_value, visibility = _read_symbol(
                data, table, index, bits, order, string_data, len(sections)
            )
            symbols.add(record)
            if table["kind"] == "dynsym":
                if not record["defined"] and record["name"]:
                    imports.add(record)
                if (
                    record["defined"]
                    and binding_value in (1, 2, 10)  # GLOBAL, WEAK, GNU_UNIQUE.
                    and visibility in (0, 3)
                ):
                    exports.add(record)
    _check_stop(should_stop)
    return {
        "imports": imports.result(),
        "exports": exports.result(),
        "symbols": symbols.result(),
        "limitations": _limitations(tables),
    }
