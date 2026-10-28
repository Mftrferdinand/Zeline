"""Code mapping must not reinterpret headers, padding, or ambiguous addresses."""
import struct
import unittest
from tests.test_reverse_binary import elf, pe
from zeline.reverse_code_regions import select_code_region


class CodeRegionTests(unittest.TestCase):
    def test_elf_classes_entry_and_explicit_offset(self):
        for bits in (32, 64):
            data = bytes(elf(bits))
            region = select_code_region(data)
            self.assertEqual(region['architecture'], 'x86' if bits == 32 else 'x86_64')
            self.assertEqual(region['address'], 0x401000)
            start = region['byte_offset']
            self.assertEqual(select_code_region(data, byte_offset=start + 1)['address'], 0x401001)
            with self.assertRaises(ValueError):
                select_code_region(data, byte_offset=0)

    def test_pe_classes_base_padding_and_no_relocation_override(self):
        for bits in (32, 64):
            data = pe(bits)
            region = select_code_region(bytes(data))
            self.assertEqual(region['address'], 0x401000)
            self.assertEqual(region['bytes_remaining'], 4)
            with self.assertRaises(ValueError):
                select_code_region(bytes(data), base_address=0)
            # Raw span has four bytes, but virtual code span has only two.
            section = 0x80 + 24 + (240 if bits == 64 else 224)
            struct.pack_into('<I', data, section + 8, 2)
            with self.assertRaises(ValueError):
                select_code_region(bytes(data), byte_offset=region['byte_offset'] + 2)

    def test_raw_requires_isa_and_has_strict_bounds(self):
        with self.assertRaises(ValueError):
            select_code_region(b'raw')
        result = select_code_region(b'raw', architecture='x86_64', base_address=0x1000, byte_offset=1)
        self.assertEqual(result['address'], 0x1001)
        for options in [dict(byte_offset=True), dict(base_address=True), dict(byte_offset=3),
                        dict(base_address=(1 << 64) - 1), dict(architecture='bogus')]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                select_code_region(b'raw', **({'architecture': 'x86_64'} | options))
        for arch in ('x86', 'arm', 'thumb'):
            with self.assertRaises(ValueError):
                select_code_region(b'raw', architecture=arch, base_address=1 << 32)

    def test_unknown_isa_big_endian_x86_and_incompatible_override_fail(self):
        data = elf()
        struct.pack_into('<H', data, 18, 0xffff)
        with self.assertRaises(ValueError):
            select_code_region(bytes(data))
        with self.assertRaises(ValueError):
            select_code_region(bytes(elf(64, '>')))
        with self.assertRaises(ValueError):
            select_code_region(bytes(elf()), architecture='arm')
        with self.assertRaises(ValueError):
            select_code_region(b'\x7fELF', architecture='x86')

    def test_arm_thumb_entry_and_big_endian(self):
        for endian in ('<', '>'):
            data = elf(32, endian)
            struct.pack_into(endian + 'H', data, 18, 40)
            struct.pack_into(endian + 'I', data, 24, 0x401001)
            result = select_code_region(bytes(data))
            self.assertEqual(result['architecture'], 'thumb')
            self.assertEqual(result['address'], 0x401000)
            self.assertEqual(result['endianness'], 'little' if endian == '<' else 'big')
            self.assertEqual(select_code_region(bytes(data), architecture='arm')['architecture'], 'arm')
        data = pe(32)
        struct.pack_into('<H', data, 0x84, 0x1c4)
        self.assertEqual(select_code_region(bytes(data))['architecture'], 'thumb')

    def test_instruction_order_is_not_always_elf_data_endianness(self):
        data = elf(64, '>')
        struct.pack_into('>H', data, 18, 183)
        selected = select_code_region(bytes(data))
        self.assertEqual(selected['endianness'], 'little')
        self.assertEqual(selected['data_endianness'], 'big')
        data = elf(32, '>')
        struct.pack_into('>H', data, 18, 40)
        struct.pack_into('>I', data, 36, 0x00800000)
        selected = select_code_region(bytes(data))
        self.assertEqual(selected['endianness'], 'little')
        self.assertEqual(selected['data_endianness'], 'big')

    def test_nonexec_zero_fill_and_missing_sections_fail(self):
        data = elf()
        struct.pack_into('<Q', data, 64 + 64 + 8, 2)
        with self.assertRaises(ValueError):
            select_code_region(bytes(data))
        data = elf()
        struct.pack_into('<I', data, 64 + 64 + 4, 8)
        with self.assertRaises(ValueError):
            select_code_region(bytes(data))

    def test_header_overlap_rejected(self):
        data = elf()
        struct.pack_into('<Q', data, 64 + 64 + 24, 0)
        with self.assertRaises(ValueError):
            select_code_region(bytes(data))
        data = pe()
        section = 0x80 + 24 + 240
        struct.pack_into('<I', data, section + 20, 0)
        with self.assertRaises(ValueError):
            select_code_region(bytes(data))

    def test_overlapping_executable_file_and_address_regions_fail(self):
        for overlap in ('file', 'address'):
            data = elf()
            second = 64 + 2 * 64
            struct.pack_into('<Q', data, second + 8, 6)
            if overlap == 'file':
                string_offset = struct.unpack_from('<Q', data, second + 24)[0]
                struct.pack_into('<Q', data, 64 + 64 + 24, string_offset)
            else:
                struct.pack_into('<Q', data, second + 16, 0x401000)
            with self.subTest(overlap=overlap), self.assertRaises(ValueError):
                select_code_region(bytes(data))

    def test_cancellation_and_strict_input(self):
        with self.assertRaises(InterruptedError):
            select_code_region(bytes(elf()), should_stop=lambda: True)
        for data in (bytearray(b'abc'), b'x' * (8 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                select_code_region(data, architecture='x86')
        with self.assertRaises(ValueError):
            select_code_region(b'abc', architecture='arm', byte_offset=1)
