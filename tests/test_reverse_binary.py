"""Synthetic fixtures; never load or execute binary code."""

import importlib.util
from pathlib import Path
import struct
import unittest


def inspect(data):
    spec = importlib.util.spec_from_file_location(
        "reverse_binary", Path(__file__).parents[1] / "zeline/reverse_binary.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.inspect_binary(data)


def elf(bits=64, endian="<"):
    hs, ss = (64, 64) if bits == 64 else (52, 40)
    names = b"\0.text\0.shstrtab\0"
    no = hs + 3 * ss
    data = bytearray(no + len(names) + 4)
    data[:16] = (
        b"\x7fELF" + bytes([2 if bits == 64 else 1, 1 if endian == "<" else 2, 1]) + bytes(9)
    )
    fmt = "HHIQQQIHHHHHH" if bits == 64 else "HHIIIIIHHHHHH"
    struct.pack_into(
        endian + fmt,
        data,
        16,
        2,
        62 if bits == 64 else 3,
        1,
        0x401000,
        0,
        hs,
        0,
        hs,
        0,
        0,
        ss,
        3,
        2,
    )
    fmt = "IIQQQQIIQQ" if bits == 64 else "IIIIIIIIII"
    struct.pack_into(endian + fmt, data, hs + ss, 1, 1, 6, 0x401000, no + len(names), 4, 0, 0, 1, 0)
    struct.pack_into(endian + fmt, data, hs + 2 * ss, 7, 3, 0, 0, no, len(names), 0, 0, 1, 0)
    data[no : no + len(names)] = names
    return data


def pe(bits=64):
    optional_size = 240 if bits == 64 else 224
    section_offset = 0x80 + 24 + optional_size
    data = bytearray(section_offset + 44)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into(
        "<HHIIIHH", data, 0x84, 0x8664 if bits == 64 else 0x14C, 1, 0, 0, 0, optional_size, 2
    )
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x20B if bits == 64 else 0x10B)
    struct.pack_into("<I", data, optional + 16, 0x1000)
    struct.pack_into(
        "<Q" if bits == 64 else "<I", data, optional + (24 if bits == 64 else 28), 0x400000
    )
    struct.pack_into("<II", data, optional + 32, 0x1000, 0x200)
    struct.pack_into("<II", data, optional + 56, 0x2000, section_offset + 40)
    struct.pack_into(
        "<8sIIIIIIHHI",
        data,
        section_offset,
        b".text\0\0\0",
        4,
        0x1000,
        4,
        section_offset + 40,
        0,
        0,
        0,
        0,
        0x60000020,
    )
    return data


class BinaryTests(unittest.TestCase):
    def test_every_truncated_prefix_is_rejected(self):
        for fixture in (elf(32), elf(64, ">"), pe(32), pe(64)):
            for length in range(len(fixture)):
                with self.subTest(length=length, total=len(fixture)):
                    with self.assertRaises(ValueError):
                        inspect(bytes(fixture[:length]))

    def test_unknown_and_wrong_input_types(self):
        for value in (b"", b"hello", b"PE\0\0", None, "MZ", bytearray(b"MZ")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    inspect(value)

    def test_malformed_elf_headers_and_sections(self):
        cases = [
            (4, "B", 0),
            (5, "B", 3),
            (6, "B", 0),
            (20, "I", 0),
            (52, "H", 1),
            (58, "H", 1),
            (60, "H", 257),
            (60, "H", 0),
            (62, "H", 3),
            (62, "H", 65535),
            (40, "Q", 2**63),
            (32, "Q", 2**63),
            (54, "H", 56),
            (64 + 64 + 24, "Q", 2**63),
            (64 + 64 + 32, "Q", 2**63),
            (64 + 64, "I", 999),
        ]
        for offset, fmt, value in cases:
            data = elf()
            if offset in (32, 54):
                struct.pack_into("<H", data, 56, 1)
            struct.pack_into("<" + fmt, data, offset, value)
            with self.subTest(offset=offset, value=value):
                with self.assertRaises(ValueError):
                    inspect(bytes(data))

    def test_malformed_pe_headers_and_sections(self):
        cases = [
            (0x3C, "I", 2**31),
            (0x3C, "I", 1),
            (0x80, "I", 0),
            (0x86, "H", 257),
            (0x94, "H", 2),
            (0x94, "H", 65535),
            (0x98, "H", 0),
            (0x98 + 108, "I", 65535),
            (0x98 + 60, "I", 1),
            (0x98 + 60, "I", 2**31),
            (0x98 + 240 + 20, "I", 2**31),
            (0x98 + 240 + 16, "I", 2**31),
        ]
        for offset, fmt, value in cases:
            data = pe()
            struct.pack_into("<" + fmt, data, offset, value)
            with self.subTest(offset=offset, value=value):
                with self.assertRaises(ValueError):
                    inspect(bytes(data))

    def test_elf_nobits_has_no_file_payload(self):
        data = elf()
        struct.pack_into("<I", data, 64 + 64 + 4, 8)
        struct.pack_into("<QQ", data, 64 + 64 + 24, 2**63, 2**63)
        result = inspect(bytes(data))
        self.assertFalse(result["sections"][1]["file_backed"])

    def test_section_names_are_bounded(self):
        data = elf()
        start = 64 + 3 * 64
        data[start:] = b"X" * 10000
        struct.pack_into("<Q", data, 64 + 2 * 64 + 32, 10000)
        result = inspect(bytes(data))
        self.assertLessEqual(len(result["sections"][1]["name"]), 128)

    def test_program_table_cannot_overlap_elf_header(self):
        data = elf()
        struct.pack_into("<Q", data, 32, 1)
        struct.pack_into("<HH", data, 54, 56, 1)
        with self.assertRaises(ValueError):
            inspect(bytes(data))

    def test_section_table_must_follow_declared_header(self):
        data = elf()
        struct.pack_into("<H", data, 52, 200)
        with self.assertRaises(ValueError):
            inspect(bytes(data))

    def test_section_tables_cannot_overlap_headers(self):
        data = elf()
        struct.pack_into("<Q", data, 40, 1)
        with self.assertRaises(ValueError):
            inspect(bytes(data))

    def test_elf_without_section_table(self):
        data = elf()
        struct.pack_into("<Q", data, 40, 0)
        struct.pack_into("<HH", data, 60, 0, 0)
        result = inspect(bytes(data[:64]))
        self.assertEqual(result["sections"], [])

    def test_input_size_limit(self):
        with self.assertRaises(ValueError):
            inspect(b"\0" * (64 * 1024 * 1024 + 1))

    def test_unknown_architecture_is_not_guessed(self):
        for data, offset in ((elf(), 18), (pe(), 0x84)):
            struct.pack_into("<H", data, offset, 0xFFFF)
            self.assertEqual(inspect(bytes(data))["metadata"]["architecture"], "unknown")

    def test_pe_zero_raw_size_has_no_file_payload(self):
        data = pe()
        struct.pack_into("<II", data, 0x98 + 240 + 16, 0, 0xFFFFFFFF)
        self.assertFalse(inspect(bytes(data))["sections"][0]["file_backed"])

    def test_pe_classes(self):
        for bits in (32, 64):
            with self.subTest(bits=bits):
                result = inspect(bytes(pe(bits)))
                self.assertEqual(result["format"], "pe")
                self.assertEqual(result["metadata"]["bits"], bits)
                self.assertEqual(result["metadata"]["entrypoint"], 0x401000)
                self.assertEqual(result["metadata"]["entrypoint_rva"], 0x1000)
                self.assertEqual(result["metadata"]["image_base"], 0x400000)
                self.assertEqual(
                    result["metadata"]["architecture"], "x86_64" if bits == 64 else "x86"
                )
                self.assertEqual(result["sections"][0]["name"], ".text")
                self.assertEqual(result["sections"][0]["size"], 4)

    def test_elf_classes_and_byte_orders(self):
        for bits in (32, 64):
            for endian in ("<", ">"):
                with self.subTest(bits=bits, endian=endian):
                    result = inspect(bytes(elf(bits, endian)))
                    self.assertEqual(set(result), {"format", "metadata", "sections", "limitations"})
                    self.assertEqual(result["format"], "elf")
                    self.assertEqual(result["metadata"]["bits"], bits)
                    self.assertEqual(
                        result["metadata"]["endianness"], "little" if endian == "<" else "big"
                    )
                    self.assertEqual(result["metadata"]["entrypoint"], 0x401000)
                    self.assertEqual(
                        result["metadata"]["architecture"], "x86_64" if bits == 64 else "x86"
                    )
                    self.assertEqual(result["sections"][1]["name"], ".text")
                    self.assertEqual(result["sections"][1]["size"], 4)
                    self.assertIn(
                        "No decompilation, import recovery, or symbol recovery.",
                        result["limitations"],
                    )


if __name__ == "__main__":
    unittest.main()
