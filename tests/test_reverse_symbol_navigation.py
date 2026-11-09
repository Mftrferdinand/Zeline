"""Exact symbol selection maps declarations to inert executable bytes."""
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
from zeline.reverse_binary import inspect_binary
from zeline.reverse_symbol_navigation import resolve_code_symbol
from tests.test_reverse_elf_symbols import _make_elf
from tests.test_reverse_pe_imports import pe, directory, put


def code_elf(bits=64, endian='<'):
    data = _make_elf(bits, endian)
    if endian == '>':
        struct.pack_into('>H', data, 18, 183 if bits == 64 else 40)
    header = inspect_binary(bytes(data))
    shoff = header['metadata']['section_table_offset']
    stride = 64 if bits == 64 else 40
    text = shoff + stride * 5
    struct.pack_into(endian + ('QQ' if bits == 64 else 'II'), data, text + 8, 6, 0x1234)
    payload = header['sections'][5]['offset']
    data[payload:payload + 4] = bytes.fromhex('90c390c3')
    return data, payload


class SymbolNavigationTests(unittest.TestCase):
    def test_exact_elf_code_name_maps_symbol_value_not_symbol_table_offset(self):
        for bits in (32, 64):
            for order in ('<', '>'):
                data, payload = code_elf(bits, order)
                result = resolve_code_symbol(bytes(data), 'public')
                self.assertEqual(result['byte_offset'], payload)
                self.assertEqual(result['selected_symbol']['address'], 0x1234)
                self.assertEqual(result['selected_symbol']['name'], 'public')
                self.assertNotEqual(result['selected_symbol']['sources'][0]['entry_file_offset'], payload)

    def test_pe_named_exports_and_aliases_are_code_starts_not_forwarders(self):
        for bits in (32, 64):
            data = pe(bits)
            optsize = (96 if bits == 32 else 112) + 128
            struct.pack_into('<I', data, 0x98 + optsize + 36, 0x20000000)
            directory(data, 0, 0x1200, 0x100, bits)
            put(data, 0x1200, struct.pack('<IIHHIIIIIII', 0, 0, 0, 0, 0, 10, 2, 3, 0x1240, 0x1260, 0x1270))
            put(data, 0x1240, struct.pack('<II', 0x1400, 0x1290))
            put(data, 0x1260, struct.pack('<III', 0x12b0, 0x12c0, 0x12d0))
            put(data, 0x1270, struct.pack('<HHH', 0, 1, 0))
            put(data, 0x1290, b'OTHER.Func\0')
            put(data, 0x12b0, b'Alpha\0')
            put(data, 0x12c0, b'Forward\0')
            put(data, 0x12d0, b'Alias\0')
            for name in ('Alpha', 'Alias'):
                result = resolve_code_symbol(bytes(data), name)
                self.assertEqual(result['byte_offset'], 0x600)
                self.assertEqual(result['selected_symbol']['address'], 0x1400)
            with self.assertRaises(ValueError):
                resolve_code_symbol(bytes(data), 'Forward')

    def test_name_ambiguity_and_duplicate_origins(self):
        for same_address in (False, True):
            data, payload = code_elf()
            section = next(s for s in inspect_binary(bytes(data))['sections'] if s['type'] == 11)
            first = section['offset'] + 3 * 24
            second = section['offset'] + 5 * 24
            name_index = struct.unpack_from('<I', data, first)[0]
            struct.pack_into('<I', data, second, name_index)
            struct.pack_into('<Q', data, second + 8, 0x1234 if same_address else 0x1235)
            if same_address:
                result = resolve_code_symbol(bytes(data), 'public')
                self.assertEqual(result['byte_offset'], payload)
                self.assertEqual(result['selected_symbol']['source_count'], 2)
            else:
                with self.assertRaisesRegex(ValueError, 'ambiguous'):
                    resolve_code_symbol(bytes(data), 'public')

    def test_selection_beyond_first_100_symbol_records_and_bounded_provenance(self):
        data, payload = code_elf()
        header = inspect_binary(bytes(data))
        section = next(s for s in header['sections'] if s['type'] == 11)
        first = section['offset'] + 3 * 24
        record = bytes(data[first:first + 24])
        struct.pack_into('<I', data, first, 0)
        end = section['offset'] + section['size']
        extra = bytes(149 * 24) + record
        data[end:end] = extra
        for index, entry in enumerate(header['sections']):
            sh = header['metadata']['section_table_offset'] + index * 64
            if entry['offset'] >= end:
                struct.pack_into('<Q', data, sh + 24, entry['offset'] + len(extra))
            if entry['type'] == 11:
                struct.pack_into('<Q', data, sh + 32, entry['size'] + len(extra))
        result = resolve_code_symbol(bytes(data), 'public')
        self.assertEqual(result['byte_offset'], payload + len(extra))
        self.assertGreater(result['selected_symbol']['sources'][0]['entry_file_offset'], first + 100 * 24)

    def test_elf_thumb_tag_and_relative_object_value(self):
        data, payload = code_elf(32)
        struct.pack_into('<H', data, 18, 40)
        section = next(s for s in inspect_binary(bytes(data))['sections'] if s['type'] == 11)
        struct.pack_into('<I', data, section['offset'] + 3 * 16 + 4, 0x1235)
        result = resolve_code_symbol(bytes(data), 'public')
        self.assertEqual(result['architecture'], 'thumb')
        self.assertEqual(result['byte_offset'], payload)
        data, payload = code_elf()
        header = inspect_binary(bytes(data))
        section = next(s for s in header['sections'] if s['type'] == 11)
        struct.pack_into('<H', data, 16, 1)
        struct.pack_into('<Q', data, header['metadata']['section_table_offset'] + 5 * 64 + 16, 0)
        struct.pack_into('<Q', data, section['offset'] + 3 * 24 + 8, 2)
        result = resolve_code_symbol(bytes(data), 'public')
        self.assertEqual(result['byte_offset'], payload + 2)
        self.assertEqual(result['selected_symbol']['address'], 2)

    def test_unsupported_names_data_symbols_cancellation_and_no_subprocess(self):
        data, _ = code_elf()
        for name in ('', True, None, '\0', 'x' * 513, 'missing', 'ordinary'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                resolve_code_symbol(bytes(data), name)
        with self.assertRaises(InterruptedError):
            resolve_code_symbol(bytes(data), 'public', should_stop=lambda: True)
        calls = 0
        def stop():
            nonlocal calls
            calls += 1
            return calls > 5
        with self.assertRaises(InterruptedError):
            resolve_code_symbol(bytes(data), 'public', should_stop=stop)
        with patch('subprocess.Popen', side_effect=AssertionError('selector spawned process')):
            resolve_code_symbol(bytes(data), 'public')
        section = next(s for s in inspect_binary(bytes(data))['sections'] if s['type'] == 11)
        struct.pack_into('<I', data, section['offset'] + 5 * 24, 0xffffffff)
        with self.assertRaises(ValueError):
            resolve_code_symbol(bytes(data), 'public')

    def test_native_symbol_selection_and_option_guards(self):
        from zeline.reverse_engine import analyze
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, payload = code_elf()
            (root / 'code.elf').write_bytes(data)
            result = analyze('disassemble', 'code.elf', root, symbol='public', limit=1)
            self.assertEqual(result['findings']['byte_offset'], payload)
            self.assertEqual(result['findings']['selected_symbol']['name'], 'public')
            self.assertEqual(result['findings']['instructions'][0]['mnemonic'], 'nop')
            self.assertEqual(result['findings']['next_byte_offset'], payload + 1)
            self.assertEqual(result, analyze('disassemble', 'code.elf', root, symbol='public', limit=1))
            for action, options in [('inspect', {}), ('disassemble', {'byte_offset': payload}),
                                    ('disassemble', {'base_address': 0})]:
                with self.subTest(action=action, options=options), self.assertRaises(ValueError):
                    analyze(action, 'code.elf', root, symbol='public', **options)

    def test_rejects_data_zero_fill_compressed_unknown_isa_and_raw(self):
        for variant in ('data', 'zero_fill', 'compressed', 'unknown_isa'):
            data, _ = code_elf()
            header = inspect_binary(bytes(data))
            table = next(s for s in header['sections'] if s['type'] == 11)
            text = header['metadata']['section_table_offset'] + 5 * 64
            if variant == 'data':
                data[table['offset'] + 3 * 24 + 4] = 0x11
            elif variant == 'zero_fill':
                struct.pack_into('<I', data, text + 4, 8)
            elif variant == 'compressed':
                struct.pack_into('<Q', data, text + 8, 6 | 0x800)
            else:
                struct.pack_into('<H', data, 18, 0xffff)
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                resolve_code_symbol(bytes(data), 'public')
        for data in (b'raw', b'MZ', b'x' * (8 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                resolve_code_symbol(data, 'public')

    def test_provenance_is_bounded_even_for_many_duplicate_records(self):
        data, payload = code_elf()
        header = inspect_binary(bytes(data))
        table = next(s for s in header['sections'] if s['type'] == 11)
        first = table['offset'] + 3 * 24
        record = bytes(data[first:first + 24])
        end = table['offset'] + table['size']
        extra = record * 150
        data[end:end] = extra
        for index, entry in enumerate(header['sections']):
            sh = header['metadata']['section_table_offset'] + index * 64
            if entry['offset'] >= end:
                struct.pack_into('<Q', data, sh + 24, entry['offset'] + len(extra))
            if entry['type'] == 11:
                struct.pack_into('<Q', data, sh + 32, entry['size'] + len(extra))
        result = resolve_code_symbol(bytes(data), 'public')
        self.assertEqual(result['byte_offset'], payload + len(extra))
        self.assertEqual(result['selected_symbol']['source_count'], 151)
        self.assertEqual(len(result['selected_symbol']['sources']), 8)
        self.assertTrue(result['selected_symbol']['sources_truncated'])

    def test_pe_lookup_beyond_first_export_page_and_rejects_noncode_zero_fill(self):
        data = pe(32)
        section = 0x98 + 96 + 128
        struct.pack_into('<I', data, section + 36, 0x20000000)
        directory(data, 0, 0x1200, 0x100, 32)
        put(data, 0x1200, struct.pack('<IIHHIIIIIII', 0, 0, 0, 0, 0, 1, 1, 150, 0x1240, 0x1500, 0x1800))
        put(data, 0x1240, struct.pack('<I', 0x1400))
        put(data, 0x1500, struct.pack('<' + 'I' * 150, *([0x1a00] * 149 + [0x1a20])))
        put(data, 0x1800, bytes(150 * 2))
        put(data, 0x1a00, b'Other\0')
        put(data, 0x1a20, b'Wanted\0')
        self.assertEqual(resolve_code_symbol(bytes(data), 'Wanted')['byte_offset'], 0x600)
        nonexec = bytearray(data)
        struct.pack_into('<I', nonexec, section + 36, 0)
        with self.assertRaises(ValueError):
            resolve_code_symbol(bytes(nonexec), 'Wanted')
        virtual = bytearray(data)
        struct.pack_into('<I', virtual, section + 16, 0x1800)
        put(virtual, 0x1240, struct.pack('<I', 0x2f00))
        with self.assertRaises(ValueError):
            resolve_code_symbol(bytes(virtual), 'Wanted')

    def test_arm_notype_odd_value_is_not_a_thumb_function_tag(self):
        for order in ('<', '>'):
            data, _ = code_elf(32, order)
            struct.pack_into(order + 'H', data, 18, 40)
            table = next(s for s in inspect_binary(bytes(data))['sections'] if s['type'] == 11)
            record = table['offset'] + 3 * 16
            data[record + 12] = 0x10
            struct.pack_into(order + 'I', data, record + 4, 0x1235)
            with self.subTest(order=order), self.assertRaisesRegex(ValueError, 'unaligned'):
                resolve_code_symbol(bytes(data), 'public')

    def test_explicit_thumb_override_precedes_symbol_alignment_validation(self):
        from zeline.reverse_engine import analyze
        data, payload = code_elf(32)
        struct.pack_into('<H', data, 18, 40)
        table = next(s for s in inspect_binary(bytes(data))['sections'] if s['type'] == 11)
        record = table['offset'] + 3 * 16
        data[record + 12] = 0x10
        struct.pack_into('<I', data, record + 4, 0x1236)
        data[payload:payload + 4] = bytes.fromhex('00bf7047')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'code.elf').write_bytes(data)
            expected = analyze('disassemble', 'code.elf', root, byte_offset=payload + 2,
                               architecture='thumb', limit=1)['findings']
            actual = analyze('disassemble', 'code.elf', root, symbol='public',
                             architecture='thumb', limit=1)['findings']
            self.assertEqual(actual['instructions'], expected['instructions'])
            self.assertEqual(actual['address'], 0x1236)
            self.assertEqual(actual['instructions'][0]['bytes_hex'], '7047')
            for mode in ('arm', 'x86'):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    analyze('disassemble', 'code.elf', root, symbol='public', architecture=mode)

    def test_elf_selection_requires_lossless_utf8_but_allows_literal_replacement_character(self):
        for bits in (32, 64):
            for order in ('<', '>'):
                for encoded, accepted in ((b'\xfflic\0\0\0', False), (b'\xef\xbf\xbdlic\0', True)):
                    data, payload = code_elf(bits, order)
                    pos = data.index(b'public\0')
                    data[pos:pos + 7] = encoded
                    with self.subTest(bits=bits, order=order, encoded=encoded):
                        if accepted:
                            selected = resolve_code_symbol(bytes(data), '\ufffdlic')
                            self.assertEqual(selected['byte_offset'], payload)
                        else:
                            with self.assertRaises(ValueError):
                                resolve_code_symbol(bytes(data), '\ufffdlic')

    def test_pe_selection_rejects_lossy_ascii_name(self):
        for bits in (32, 64):
            data = pe(bits)
            optsize = (96 if bits == 32 else 112) + 128
            struct.pack_into('<I', data, 0x98 + optsize + 36, 0x20000000)
            directory(data, 0, 0x1200, 0x100, bits)
            put(data, 0x1200, struct.pack('<IIHHIIIIIII', 0, 0, 0, 0, 0, 1, 1, 1, 0x1240, 0x1260, 0x1270))
            put(data, 0x1240, struct.pack('<I', 0x1400))
            put(data, 0x1260, struct.pack('<I', 0x12b0))
            put(data, 0x1270, struct.pack('<H', 0))
            put(data, 0x12b0, b'\xfflpha\0')
            with self.subTest(bits=bits), self.assertRaises(ValueError):
                resolve_code_symbol(bytes(data), '\ufffdlpha')

    def test_resolver_isa_override_requires_a_string(self):
        data, _ = code_elf()
        for mode in (None, False, 0, [], {}):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                resolve_code_symbol(bytes(data), 'public', architecture=mode)

    def test_valid_utf8_identity_is_not_ambiguous_with_lossy_decoded_alias(self):
        data, payload = code_elf()
        public = data.index(b'public\0')
        weak = data.index(b'weak\0')
        data[public:public + 7] = b'\xffa\0\0\0\0\0'
        data[weak:weak + 5] = b'\xef\xbf\xbda\0'
        table = next(s for s in inspect_binary(bytes(data))['sections'] if s['type'] == 11)
        struct.pack_into('<Q', data, table['offset'] + 5 * 24 + 8, 0x1235)
        selected = resolve_code_symbol(bytes(data), '\ufffda')
        self.assertEqual(selected['byte_offset'], payload + 1)
        self.assertEqual(selected['selected_symbol']['source_count'], 1)
