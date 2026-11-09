"""Synthetic inert PE fixtures."""
import struct
import unittest

from zeline.reverse_pe_imports import inspect_pe_imports


def pe(bits=32):
    data = bytearray(0x2200)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    minimum = 96 if bits == 32 else 112
    optsize = minimum + 128
    struct.pack_into('<HHIIIHH', data, 0x84,
                     0x14c if bits == 32 else 0x8664, 1, 0, 0, 0, optsize, 0)
    struct.pack_into('<H', data, 0x98, 0x10b if bits == 32 else 0x20b)
    struct.pack_into('<II', data, 0x98 + 56, 0x4000, 0x200)
    struct.pack_into('<I', data, 0x98 + minimum - 4, 16)
    struct.pack_into('<8sIIIIIIHHI', data, 0x98 + optsize,
                     b'.rdata', 0x2000, 0x1000, 0x2000, 0x200, 0, 0, 0, 0, 0)
    return data


def directory(data, index, rva, size, bits=32):
    struct.pack_into('<II', data, 0x98 + (96 if bits == 32 else 112) + index * 8,
                     rva, size)


def put(data, rva, value):
    start = rva - 0x1000 + 0x200
    data[start:start + len(value)] = value


def imports(bits=32, fallback=False):
    data = pe(bits)
    directory(data, 1, 0x1000, 40, bits)
    put(data, 0x1000, struct.pack('<IIIII', 0 if fallback else 0x1100,
                                 0, 0, 0x1080, 0x1100))
    put(data, 0x1080, b'KERNEL32.dll\0')
    fmt, high = ('I', 1 << 31) if bits == 32 else ('Q', 1 << 63)
    put(data, 0x1100, struct.pack('<' + fmt * 3, 0x1180, high | 7, 0))
    put(data, 0x1180, struct.pack('<H', 23) + b'ExitProcess\0')
    return data


class PEImportsTests(unittest.TestCase):
    def test_named_ordinal_and_fallback_pe32_pe64(self):
        for bits in (32, 64):
            for fallback in (False, True):
                with self.subTest(bits=bits, fallback=fallback):
                    result = inspect_pe_imports(bytes(imports(bits, fallback)))
                    page = result['imports']
                    self.assertTrue(page['available'])
                    self.assertEqual(page['total_entries'], 2)
                    named, ordinal = page['entries']
                    self.assertEqual(named['dll'], 'KERNEL32.dll')
                    self.assertEqual(named['name'], 'ExitProcess')
                    self.assertEqual(named['hint'], 23)
                    self.assertEqual(named['thunk_rva'], 0x1100)
                    self.assertEqual(named['thunk_offset'], 0x300)
                    self.assertEqual(ordinal['ordinal'], 7)
                    self.assertIsNone(ordinal['name'])
                    self.assertFalse(result['symbols']['available'])
                    self.assertIn('COFF/PDB', ' '.join(result['limitations']))


class PEExportsTests(unittest.TestCase):
    def test_exports_forwarder_holes_unnamed_and_alias(self):
        for bits in (32, 64):
            data = pe(bits)
            directory(data, 0, 0x1200, 0x100, bits)
            put(data, 0x1200, struct.pack('<IIHHIIIIIII',
                0, 0, 0, 0, 0x1280, 10, 4, 3, 0x1240, 0x1260, 0x1270))
            put(data, 0x1240, struct.pack('<IIII', 0x1400, 0, 0x1290, 0x1410))
            put(data, 0x1260, struct.pack('<III', 0x12b0, 0x12c0, 0x12d0))
            put(data, 0x1270, struct.pack('<HHH', 0, 2, 0))
            put(data, 0x1280, b'example.dll\0')
            put(data, 0x1290, b'OTHER.#8\0')
            put(data, 0x12b0, b'Alpha\0')
            put(data, 0x12c0, b'Forward\0')
            put(data, 0x12d0, b'Alias\0')
            result = inspect_pe_imports(bytes(data))['exports']
            self.assertEqual(result['total_entries'], 3)
            first, forwarded, unnamed = result['entries']
            self.assertEqual(first['names'], ['Alpha', 'Alias'])
            self.assertEqual(first['ordinal'], 10)
            self.assertEqual(first['rva'], 0x1400)
            self.assertEqual(first['offset'], 0x600)
            self.assertEqual(forwarded['forwarder'], 'OTHER.#8')
            self.assertEqual(unnamed['names'], [])
            self.assertEqual(unnamed['ordinal'], 13)


class PEBoundsTests(unittest.TestCase):
    def test_absent_directories_and_page_contract(self):
        result = inspect_pe_imports(bytes(pe()))
        self.assertEqual(result['imports'], dict(entries=[], offset=0,
            total_entries=0, next_offset=None, exhausted=True, available=True))
        self.assertEqual(result['exports'], result['imports'])
        self.assertFalse(result['symbols']['available'])
        data = pe()
        struct.pack_into('<I', data, 0x98 + 92, 0)
        self.assertEqual(inspect_pe_imports(bytes(data))['imports']['total_entries'], 0)

    def test_pagination_and_validation(self):
        data = imports()
        put(data, 0x1100, struct.pack('<' + 'I' * 151, *([0x80000007] * 150), 0))
        for offset, limit, count, following in ((0, 100, 100, 100),
                (100, 100, 50, None), (149, 1, 1, None), (500, 10, 0, None)):
            page = inspect_pe_imports(bytes(data), offset=offset, limit=limit)['imports']
            self.assertEqual(len(page['entries']), count)
            self.assertEqual(page['total_entries'], 150)
            self.assertEqual(page['next_offset'], following)
            self.assertEqual(page['exhausted'], following is None)
        for kwargs in ({'offset': -1}, {'limit': 0}, {'limit': 101},
                       {'offset': True}, {'limit': 1.5}):
            with self.assertRaises(ValueError):
                inspect_pe_imports(bytes(data), **kwargs)

    def test_invalid_ranges_and_truncation(self):
        cases = []
        for rva, size in ((0, 40), (0x1000, 0), (0x9000, 40),
                          (0x2ff0, 40), (0xfffffff0, 40)):
            data = imports()
            directory(data, 1, rva, size)
            cases.append(data)
        data = imports()
        cases.append(data[:-1])
        for raw, virtual, size, vsize in ((0x100, 0x1000, 0x100, 0x100),
                (0x200, 0x100, 0x2000, 0x2000),
                (0x200, 0x1000, 0x100, 0x2000)):
            data = imports()
            struct.pack_into('<IIII', data, 0x178 + 8, vsize, virtual, size, raw)
            cases.append(data)
        for data in cases:
            with self.subTest(case=cases.index(data)), self.assertRaises(ValueError):
                inspect_pe_imports(bytes(data))

    def test_unterminated_descriptors_thunks_names(self):
        data = imports()
        directory(data, 1, 0x1000, 20)
        with self.assertRaises(ValueError):
            inspect_pe_imports(bytes(data))
        data = imports()
        put(data, 0x1080, b'x' * 512)
        with self.assertRaises(ValueError):
            inspect_pe_imports(bytes(data))
        data = imports()
        put(data, 0x1100, struct.pack('<I', 0x1100))
        put(data, 0x1102, b'x' * 512)
        with self.assertRaises(ValueError):
            inspect_pe_imports(bytes(data))

    def test_cancellation_mid_scan(self):
        calls = 0
        def stop():
            nonlocal calls
            calls += 1
            return calls >= 30
        data = imports()
        # Enough actual entries to cancel mid-scan, independent of how many
        # redundant checks a particular string lookup implementation performs.
        put(data, 0x1100, struct.pack('<' + 'I' * 151, *([0x80000007] * 150), 0))
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            inspect_pe_imports(bytes(data), should_stop=stop)
        self.assertEqual(calls, 30)
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            inspect_pe_imports(bytes(pe()), should_stop=lambda: True)

    def test_export_bad_counts_and_ordinal(self):
        for functions, names, ordinal in ((0xffffffff, 0, 0),
                (1, 0xffffffff, 0), (1, 1, 7)):
            data = pe()
            directory(data, 0, 0x1200, 0x100)
            put(data, 0x1200, struct.pack('<IIHHIIIIIII',
                0, 0, 0, 0, 0, 1, functions, names, 0x1240, 0x1260, 0x1270))
            put(data, 0x1240, struct.pack('<I', 0x1400))
            put(data, 0x1260, struct.pack('<I', 0x1280))
            put(data, 0x1270, struct.pack('<H', ordinal))
            with self.assertRaises(ValueError):
                inspect_pe_imports(bytes(data))

    def test_export_null_address_table_rejected(self):
        data = pe()
        directory(data, 0, 0x1200, 0x100)
        put(data, 0x1200, struct.pack('<IIHHIIIIIII',
            0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0))
        with self.assertRaisesRegex(ValueError, 'table'):
            inspect_pe_imports(bytes(data))


class PEAdditionalBoundsTests(unittest.TestCase):
    def test_string_cannot_cross_adjacent_sections(self):
        data = imports()
        struct.pack_into('<H', data, 0x86, 2)
        struct.pack_into('<IIII', data, 0x180, 0x1000, 0x1000, 0x1000, 0x200)
        struct.pack_into('<8sIIIIIIHHI', data, 0x1a0,
            b'.other', 0x1000, 0x2000, 0x1000, 0x1200, 0, 0, 0, 0, 0)
        struct.pack_into('<I', data, 0x20c, 0x1ffe)
        put(data, 0x1ffe, b'cross\0')
        with self.assertRaises(ValueError):
            inspect_pe_imports(bytes(data))

    def test_overlapping_rva_and_file_regions(self):
        for rva, raw in ((0x1800, 0x1200), (0x3000, 0x400)):
            data = imports()
            struct.pack_into('<H', data, 0x86, 2)
            struct.pack_into('<8sIIIIIIHHI', data, 0x1a0,
                b'.other', 0x100, rva, 0x100, raw, 0, 0, 0, 0, 0)
            with self.assertRaisesRegex(ValueError, 'overlap'):
                inspect_pe_imports(bytes(data))

    def test_descriptor_scan_budget(self):
        data = pe()
        directory(data, 1, 0x1000, 257 * 20)
        for index in range(256):
            put(data, 0x1000 + index * 20,
                struct.pack('<IIIII', 0x2800, 0, 0, 0x2810, 0x2800))
        put(data, 0x2810, b'dll\0')
        with self.assertRaisesRegex(ValueError, 'budget'):
            inspect_pe_imports(bytes(data))

    def test_thunk_scan_budget(self):
        data = imports()
        data.extend(bytes(0x20000 - len(data)))
        struct.pack_into('<IIII', data, 0x180, 0x1fe00, 0x1000, 0x1fe00, 0x200)
        put(data, 0x1100, struct.pack('<I', 0x80000007) * 16384)
        with self.assertRaisesRegex(ValueError, 'budget'):
            inspect_pe_imports(bytes(data), limit=1)

    def test_export_page_and_unterminated_forwarder(self):
        data = pe()
        directory(data, 0, 0x1200, 0x100)
        put(data, 0x1200, struct.pack('<IIHHIIIIIII',
            0, 0, 0, 0, 0, 1, 3, 0, 0x1240, 0, 0))
        put(data, 0x1240, struct.pack('<III', 0x1400, 0, 0x1410))
        page = inspect_pe_imports(bytes(data), offset=1, limit=1)['exports']
        self.assertEqual(page['total_entries'], 2)
        self.assertEqual(page['entries'][0]['ordinal'], 3)
        self.assertTrue(page['exhausted'])
        put(data, 0x1240, struct.pack('<I', 0x12fe))
        put(data, 0x12fe, b'bad\0')
        with self.assertRaisesRegex(ValueError, 'string'):
            inspect_pe_imports(bytes(data))


class PEThunkRangeTests(unittest.TestCase):
    def test_thunk_array_cannot_cross_adjacent_sections(self):
        data = imports()
        struct.pack_into('<H', data, 0x86, 2)
        struct.pack_into('<IIII', data, 0x180, 0x1000, 0x1000, 0x1000, 0x200)
        struct.pack_into('<8sIIIIIIHHI', data, 0x1a0,
            b'.other', 0x1000, 0x2000, 0x1000, 0x1200, 0, 0, 0, 0, 0)
        put(data, 0x1000, struct.pack('<IIIII', 0x1ffc, 0, 0, 0x1080, 0x1ffc))
        put(data, 0x1ffc, struct.pack('<II', 0x80000007, 0))
        with self.assertRaises(ValueError):
            inspect_pe_imports(bytes(data))
