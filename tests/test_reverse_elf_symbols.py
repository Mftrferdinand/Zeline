"""Inert synthetic ELF symbol-table fixtures and contract tests."""

import struct
import unittest

from zeline.reverse_elf_symbols import inspect_elf_symbols


SHT_STRTAB = 3
SHT_SYMTAB = 2
SHT_DYNSYM = 11


def _make_elf(bits=64, endian="<"):
    """Build a small ELF with both symbol-table kinds and no executable payload."""
    header_size = 64 if bits == 64 else 52
    section_size = 64 if bits == 64 else 40
    symbol_size = 24 if bits == 64 else 16
    section_count = 6
    section_offset = 0x100
    dynstr = b"\0missing\0localimp\0public\0hidden\0weak\0localdef\0"
    strtab = b"\0ordinary\0"

    def dyn_name(name):
        return dynstr.index(name.encode())

    dyn_symbols = [
        (0, 0, 0, 0, 0, 0),  # ELF's null symbol.
        (dyn_name("missing"), 1, 2, 0, 0, 0),  # undefined global import.
        (dyn_name("localimp"), 0, 2, 0, 0, 0),  # undefined local is still an import observation.
        (dyn_name("public"), 1, 2, 0x1234, 7, 5),  # defined global export.
        (dyn_name("hidden"), 1, 2, 0x1240, 3, 5, 2),  # hidden global, not an export.
        (dyn_name("weak"), 2, 2, 0x1250, 4, 5),  # defined weak export.
        (dyn_name("localdef"), 0, 2, 0x1260, 2, 5),  # defined local, not an export.
    ]
    sym_symbols = [
        (0, 0, 0, 0, 0, 0),
        (1, 1, 2, 0x4321, 9, 5),  # ordinary global must not be a dyn export.
    ]
    dynstr_offset = section_offset + section_count * section_size
    dynsym_offset = dynstr_offset + len(dynstr)
    strtab_offset = dynsym_offset + len(dyn_symbols) * symbol_size
    symtab_offset = strtab_offset + len(strtab)
    text_offset = symtab_offset + len(sym_symbols) * symbol_size
    total = text_offset + 4
    data = bytearray(total)
    data[:16] = b"\x7fELF" + bytes([2 if bits == 64 else 1, 1 if endian == "<" else 2, 1]) + bytes(9)
    header_format = "HHIQQQIHHHHHH" if bits == 64 else "HHIIIIIHHHHHH"
    struct.pack_into(
        endian + header_format,
        data,
        16,
        2,
        62 if bits == 64 else 3,
        1,
        0,
        0,
        section_offset,
        0,
        header_size,
        0,
        0,
        section_size,
        section_count,
        0,
    )
    data[dynstr_offset : dynstr_offset + len(dynstr)] = dynstr
    data[strtab_offset : strtab_offset + len(strtab)] = strtab
    data[text_offset : text_offset + 4] = b"TEXT"

    def put_symbol(table_offset, index, symbol):
        name, binding, symbol_type, value, size, section_index, *visibility = symbol
        other = visibility[0] if visibility else 0
        info = (binding << 4) | symbol_type
        if bits == 64:
            struct.pack_into(
                endian + "IBBHQQ",
                data,
                table_offset + index * symbol_size,
                name,
                info,
                other,
                section_index,
                value,
                size,
            )
        else:
            struct.pack_into(
                endian + "IIIBBH",
                data,
                table_offset + index * symbol_size,
                name,
                value,
                size,
                info,
                other,
                section_index,
            )

    for index, symbol in enumerate(dyn_symbols):
        put_symbol(dynsym_offset, index, symbol)
    for index, symbol in enumerate(sym_symbols):
        put_symbol(symtab_offset, index, symbol)

    section_format = "IIQQQQIIQQ" if bits == 64 else "IIIIIIIIII"

    def put_section(index, section_type, offset, size, link=0, entsize=0):
        values = (0, section_type, 0, 0, offset, size, link, 0, 1, entsize)
        struct.pack_into(endian + section_format, data, section_offset + index * section_size, *values)

    put_section(0, 0, 0, 0)
    put_section(1, SHT_STRTAB, dynstr_offset, len(dynstr))
    put_section(2, SHT_DYNSYM, dynsym_offset, len(dyn_symbols) * symbol_size, link=1, entsize=symbol_size)
    put_section(3, SHT_STRTAB, strtab_offset, len(strtab))
    put_section(4, SHT_SYMTAB, symtab_offset, len(sym_symbols) * symbol_size, link=3, entsize=symbol_size)
    put_section(5, 1, text_offset, 4)
    return data


def _section_field(data, bits, index, field, value, endian="<"):
    section_size = 64 if bits == 64 else 40
    start = 0x100 + index * section_size
    fields = {
        "type": (4, "I"),
        "offset": (24 if bits == 64 else 16, "Q" if bits == 64 else "I"),
        "size": (32 if bits == 64 else 20, "Q" if bits == 64 else "I"),
        "link": (40 if bits == 64 else 24, "I"),
        "entsize": (56 if bits == 64 else 36, "Q" if bits == 64 else "I"),
    }
    field_offset, field_format = fields[field]
    struct.pack_into(endian + field_format, data, start + field_offset, value)


def _section_value(data, bits, index, field, endian="<"):
    section_size = 64 if bits == 64 else 40
    start = 0x100 + index * section_size
    fields = {
        "offset": (24 if bits == 64 else 16, "Q" if bits == 64 else "I"),
        "size": (32 if bits == 64 else 20, "Q" if bits == 64 else "I"),
    }
    field_offset, field_format = fields[field]
    return struct.unpack_from(endian + field_format, data, start + field_offset)[0]


def _sectionless(bits=64):
    data = _make_elf(bits)
    if bits == 64:
        struct.pack_into("<Q", data, 40, 0)
        struct.pack_into("<H", data, 60, 0)
        struct.pack_into("<H", data, 62, 0)
        return bytes(data[:64])
    struct.pack_into("<I", data, 32, 0)
    struct.pack_into("<H", data, 48, 0)
    struct.pack_into("<H", data, 50, 0)
    return bytes(data[:52])


class ReverseELFSymbolTests(unittest.TestCase):
    def test_recovers_both_elf_classes_and_byte_orders(self):
        for bits in (32, 64):
            for endian in ("<", ">"):
                with self.subTest(bits=bits, endian=endian):
                    result = inspect_elf_symbols(bytes(_make_elf(bits, endian)))
                    self.assertEqual(
                        [entry["name"] for entry in result["imports"]["entries"]],
                        ["missing", "localimp"],
                    )
                    self.assertEqual(
                        [entry["name"] for entry in result["exports"]["entries"]],
                        ["public", "weak"],
                    )
                    self.assertEqual(result["symbols"]["total_entries"], 9)
                    public = result["exports"]["entries"][0]
                    self.assertEqual(public["binding"], "global")
                    self.assertEqual(public["type"], "func")
                    self.assertEqual(public["value"], 0x1234)
                    self.assertEqual(public["size"], 7)
                    self.assertEqual(public["section_index"], 5)
                    self.assertTrue(public["defined"])
                    self.assertEqual(public["table"], "dynsym")
                    self.assertIsInstance(public["file_offset"], int)

    def test_gnu_unique_exports_visibility_definition_and_pagination(self):
        for bits in (32, 64):
            for endian in ("<", ">"):
                with self.subTest(bits=bits, endian=endian):
                    data = _make_elf(bits, endian)
                    width = 24 if bits == 64 else 16
                    info_offset = 4 if bits == 64 else 12
                    dynamic = _section_value(data, bits, 2, "offset", endian)
                    # Undefined, visible defined, and hidden defined UNIQUE objects.
                    for index in (1, 3, 4):
                        data[dynamic + index * width + info_offset] = (10 << 4) | 1
                    ordinary = _section_value(data, bits, 4, "offset", endian)
                    data[ordinary + width + info_offset] = (10 << 4) | 1
                    result = inspect_elf_symbols(bytes(data))
                    page = result["exports"]
                    self.assertEqual([r["name"] for r in page["entries"]], ["public", "weak"])
                    self.assertEqual(page["total_entries"], 2)
                    self.assertEqual(page["entries"][0]["binding"], "gnu_unique")
                    self.assertEqual(page["entries"][0]["type"], "object")
                    first = inspect_elf_symbols(bytes(data), limit=1)["exports"]
                    second = inspect_elf_symbols(bytes(data), offset=first["next_offset"], limit=1)["exports"]
                    self.assertEqual(first["entries"][0]["name"], "public")
                    self.assertEqual(second["entries"][0]["name"], "weak")
                    self.assertIsNone(second["next_offset"])

    def test_each_category_is_independently_paginated_and_bounded(self):
        first = inspect_elf_symbols(bytes(_make_elf()), limit=1)
        self.assertEqual(first["imports"]["entries"][0]["name"], "missing")
        self.assertEqual(first["imports"]["total_entries"], 2)
        self.assertEqual(first["imports"]["next_offset"], 1)
        self.assertFalse(first["imports"]["exhausted"])
        self.assertEqual(first["exports"]["entries"][0]["name"], "public")
        self.assertEqual(first["exports"]["next_offset"], 1)
        self.assertEqual(first["symbols"]["entries"][0]["name"], "")
        self.assertEqual(first["symbols"]["total_entries"], 9)
        self.assertLessEqual(len(first["imports"]["entries"]), 1)
        self.assertLessEqual(len(first["exports"]["entries"]), 1)
        self.assertLessEqual(len(first["symbols"]["entries"]), 1)

        second = inspect_elf_symbols(bytes(_make_elf()), offset=1, limit=1)
        self.assertEqual(second["imports"]["entries"][0]["name"], "localimp")
        self.assertIsNone(second["imports"]["next_offset"])
        self.assertTrue(second["imports"]["exhausted"])
        self.assertEqual(second["exports"]["entries"][0]["name"], "weak")
        self.assertIsNone(second["exports"]["next_offset"])

    def test_sectionless_input_reports_unavailable_coverage(self):
        for bits in (32, 64):
            result = inspect_elf_symbols(_sectionless(bits))
            for category in ("imports", "exports", "symbols"):
                page = result[category]
                self.assertFalse(page["available"])
                self.assertEqual(page["entries"], [])
                self.assertEqual(page["total_entries"], 0)
                self.assertTrue(page["exhausted"])
            self.assertIn("not proof of absence", " ".join(result["limitations"]))

    def test_symtab_only_does_not_claim_dynamic_imports_or_exports(self):
        data = _make_elf()
        _section_field(data, 64, 2, "type", 0)
        result = inspect_elf_symbols(bytes(data))
        self.assertFalse(result["imports"]["available"])
        self.assertFalse(result["exports"]["available"])
        self.assertTrue(result["symbols"]["available"])
        self.assertEqual(result["symbols"]["total_entries"], 2)
        self.assertIn("not proof of absence", " ".join(result["limitations"]))

    def test_malformed_section_and_symbol_declarations_raise(self):
        cases = []

        data = _make_elf()
        _section_field(data, 64, 2, "offset", len(data) + 1)
        cases.append(("offset", data))

        data = _make_elf()
        _section_field(data, 64, 2, "link", 6)
        cases.append(("link", data))

        data = _make_elf()
        _section_field(data, 64, 2, "link", 5)
        cases.append(("linked type", data))

        data = _make_elf()
        _section_field(data, 64, 2, "entsize", 0)
        cases.append(("entsize", data))

        data = _make_elf()
        _section_field(data, 64, 2, "size", _section_value(data, 64, 2, "size") + 1)
        cases.append(("count alignment", data))

        for label, data in cases:
            with self.subTest(label=label), self.assertRaises(ValueError):
                inspect_elf_symbols(bytes(data))

    def test_oversized_symbol_count_is_rejected_by_the_safe_budget(self):
        from zeline.reverse_elf_symbols import MAX_SYMBOL_TABLE_ENTRIES

        data = _make_elf()
        symbol_table_offset = _section_value(data, 64, 2, "offset")
        declared_size = (MAX_SYMBOL_TABLE_ENTRIES + 1) * 24
        if len(data) < symbol_table_offset + declared_size:
            data.extend(b"\0" * (symbol_table_offset + declared_size - len(data)))
        _section_field(data, 64, 2, "size", declared_size)
        with self.assertRaisesRegex(ValueError, "safe entry budget"):
            inspect_elf_symbols(bytes(data))

    def test_malformed_name_and_extended_section_index_are_explicit_errors(self):
        data = _make_elf()
        start = data.find(b"\0missing\0")
        self.assertGreaterEqual(start, 0)
        end = start + len(b"\0missing\0localimp\0public\0hidden\0weak\0localdef\0")
        data[start:end] = data[start:end].replace(b"\0", b"X")
        with self.assertRaisesRegex(ValueError, "NUL"):
            inspect_elf_symbols(bytes(data))

        data = _make_elf()
        symbol_table_offset = _section_value(data, 64, 2, "offset")
        struct.pack_into("<H", data, symbol_table_offset + 6, 0xFFFF)
        with self.assertRaisesRegex(ValueError, "extended"):
            inspect_elf_symbols(bytes(data))

    def test_options_and_cancellation_are_explicit(self):
        fixture = bytes(_make_elf())
        for options in ({"offset": -1}, {"limit": 0}, {"limit": 101}, {"offset": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                inspect_elf_symbols(fixture, **options)
        with self.assertRaises(InterruptedError):
            inspect_elf_symbols(fixture, should_stop=lambda: True)

        calls = [0]

        def stop_during_scan():
            calls[0] += 1
            return calls[0] >= 7

        with self.assertRaises(InterruptedError):
            inspect_elf_symbols(fixture, should_stop=stop_during_scan)
        self.assertGreaterEqual(calls[0], 7)


if __name__ == "__main__":
    unittest.main()
