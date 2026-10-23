"""Cross-adapter safety checks independent of table parser implementations."""
import struct
import unittest

from tests.test_reverse_elf_symbols import _make_elf, _section_field
from tests.test_reverse_pe_imports import pe, put, directory, imports
from zeline.reverse_elf_symbols import inspect_elf_symbols
from zeline.reverse_pe_imports import inspect_pe_imports


def long_elf_name():
    data = _make_elf()
    start = len(data)
    strings = b'\0' + b'x' * 1024 + b'\0'
    data.extend(strings)
    _section_field(data, 64, 1, 'offset', start)
    _section_field(data, 64, 1, 'size', len(strings))
    return bytes(data)


def virtual_export(address):
    data = pe()
    struct.pack_into('<I', data, 0x178 + 8, 0x3000)
    struct.pack_into('<I', data, 0x98 + 56, 0x5000)
    directory(data, 0, 0x1200, 0x100)
    put(data, 0x1200, struct.pack('<IIHHIIIIIII',
        0, 0, 0, 0, 0, 1, 1, 0, 0x1240, 0, 0))
    put(data, 0x1240, struct.pack('<I', address))
    return bytes(data)


class TableSafetyTests(unittest.TestCase):
    def test_elf_names_bounded_before_decode_even_outside_page(self):
        for options in [{'limit': 100}, {'limit': 1, 'offset': 100}]:
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'name.*limit|name.*budget'):
                inspect_elf_symbols(long_elf_name(), **options)

    def test_pe_virtual_only_export_has_no_invented_file_offset(self):
        entry = inspect_pe_imports(virtual_export(0x3800))['exports']['entries'][0]
        self.assertEqual(entry['rva'], 0x3800)
        self.assertIsNone(entry['offset'])
        self.assertIsNone(entry['forwarder'])

    def test_pe_unmapped_export_address_still_rejected(self):
        with self.assertRaises(ValueError):
            inspect_pe_imports(virtual_export(0x5000))

    def test_pe_string_validation_not_per_byte_rva_remapping(self):
        data = imports()
        put(data, 0x1180, struct.pack('<H', 23) + b'n' * 511 + b'\0')
        calls = 0
        def stop():
            nonlocal calls
            calls += 1
            return False
        value = inspect_pe_imports(bytes(data), should_stop=stop)
        self.assertEqual(len(value['imports']['entries'][0]['name']), 511)
        self.assertLess(calls, 200, 'Repeated byte-by-byte RVA walks multiply table scan work')
