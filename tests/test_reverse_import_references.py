"""Static IAT-slot identity is never a resolved runtime call destination."""
import struct
import tempfile
from pathlib import Path
import unittest

from tests.test_reverse_pe_imports import imports
from zeline.reverse_engine import analyze


def import_image(bits=64, code=None):
    data = imports(bits)
    base = 0x400000 if bits == 32 else 0x140000000
    struct.pack_into('<I' if bits == 32 else '<Q', data, 0x98 + (28 if bits == 32 else 24), base)
    struct.pack_into('<H', data, 0x86, 2)
    if code is None:
        operand = base + 0x1100 if bits == 32 else 0x1100 - (0x3000 + 6)
        code = b'\xff\x15' + struct.pack('<I' if bits == 32 else '<i', operand) + b'\xff\xd0\xc3'
    section = 0x98 + (224 if bits == 32 else 240) + 40
    struct.pack_into('<8sIIIIIIHHI', data, section, b'.text', len(code), 0x3000,
                     len(code), 0x2200, 0, 0, 0, 0, 0x60000020)
    data.extend(code)
    return bytes(data), base


class ImportReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_named_iat_operand_is_static_identity_not_runtime_target(self):
        for bits in (32, 64):
            data, base = import_image(bits)
            (self.root/'image.dll').write_bytes(data)
            with self.subTest(bits=bits):
                result = analyze('references', 'image.dll', self.root, import_iat_rva=0x1100)
                page = result['findings']
                self.assertEqual(page['target']['import']['dll'], 'KERNEL32.dll')
                self.assertEqual(page['target']['import']['name'], 'ExitProcess')
                self.assertEqual(page['target']['address'], base + 0x1100)
                self.assertEqual(page['target']['address_role'], 'iat_slot_preferred_va')
                self.assertIsNone(page['target']['runtime_target'])
                self.assertIsNone(page['target']['runtime_provider'])
                self.assertEqual(len(page['matches']), 1)
                hit = page['matches'][0]
                self.assertEqual(hit['address'], base + 0x3000)
                self.assertEqual(hit['static_import_iat_rva'], 0x1100)
                self.assertIsNone(hit['target'])
                self.assertEqual(page['unresolved_calls_jumps'], 2)
                self.assertFalse(page['coverage']['whole_program_complete'])
                self.assertTrue(page['exhausted'])

    def scan_image(self, data, **options):
        (self.root / 'image.dll').write_bytes(data)
        return analyze('references', 'image.dll', self.root, **options)

    def test_ordinal_indirect_jump_and_page_cursor_preserve_identity(self):
        import hashlib
        for bits in (32, 64):
            slot = 0x1100 + bits // 8
            base = 0x400000 if bits == 32 else 0x140000000
            code = bytearray()
            for opcode in (b'\xff\x15', b'\xff\x25'):
                value = base + slot if bits == 32 else slot - (0x3000 + len(code) + 6)
                code.extend(opcode + struct.pack('<I' if bits == 32 else '<i', value))
            data, _ = import_image(bits, bytes(code))
            with self.subTest(bits=bits):
                first = self.scan_image(data, import_iat_rva=slot, limit=1)
                last = self.scan_image(data, import_iat_rva=slot, limit=1,
                                       byte_offset=first['findings']['next_byte_offset'])
                self.assertEqual(first['artifact']['sha256'], hashlib.sha256(data).hexdigest())
                self.assertEqual(first['artifact'], last['artifact'])
                self.assertEqual(first['findings']['target'], last['findings']['target'])
                self.assertEqual(last['findings']['target']['import']['ordinal'], 7)
                self.assertIsNone(last['findings']['target']['import']['name'])
                self.assertEqual(first['findings']['matches'][0]['kind'], 'call')
                self.assertEqual(last['findings']['matches'][0]['kind'], 'jump')
                self.assertEqual(last['findings']['coverage']['eligible_bytes_before_page'], 6)
                self.assertTrue(last['findings']['exhausted'])
                self.assertEqual(last, self.scan_image(data, import_iat_rva=slot, limit=1,
                                                      byte_offset=0x2206))

    def test_import_lookup_does_not_consume_or_resolve_loaded_iat_pointer(self):
        from tests.test_reverse_pe_imports import put
        for bits in (32, 64):
            data, _ = import_image(bits)
            data = bytearray(data)
            width = bits // 8
            put(data, 0x1200, data[0x300:0x300 + 3 * width])
            put(data, 0x1000, struct.pack('<IIIII', 0x1200, 123, 0, 0x1080, 0x1100))
            put(data, 0x1100, b'\xff' * (2 * width))
            with self.subTest(bits=bits):
                f = self.scan_image(bytes(data), import_iat_rva=0x1100)['findings']
                self.assertEqual(f['target']['import']['name'], 'ExitProcess')
                self.assertEqual(len(f['matches']), 1)
                self.assertIsNone(f['target']['runtime_target'])
                self.assertIsNone(f['matches'][0]['target'])

    def test_direct_jump_to_slot_is_not_an_import_operand_match(self):
        for bits in (32, 64):
            code = b'\xe9' + struct.pack('<i', 0x1100 - (0x3000 + 5))
            data, base = import_image(bits, code)
            with self.subTest(bits=bits):
                imported = self.scan_image(data, import_iat_rva=0x1100)['findings']
                direct = self.scan_image(data, target_address=base + 0x1100)['findings']
                self.assertEqual(imported['matches'], [])
                self.assertEqual(len(direct['matches']), 1)
                self.assertEqual(direct['matches'][0]['target'], base + 0x1100)

    def test_segment_index_register_and_address_size_forms_stay_unknown(self):
        for bits in (32, 64):
            base = 0x400000
            absolute = struct.pack('<I', base + 0x1100)
            variants = {
                'register': b'\xff\xd0',
                'index': b'\xff\x14\x85' + absolute,
                'segment': (b'\x64\xff\x15' + absolute if bits == 32 else
                            b'\x64\xff\x15' + struct.pack('<i', 0x1100 - 0x3007)),
                'address_size': (b'\x67\xff\x16\x00\x11' if bits == 32 else
                                 b'\x67\xff\x15' + struct.pack('<i', 0x1100 - 0x3007)),
                'far': b'\xff\x1d' + absolute,
            }
            for label, code in variants.items():
                data, _ = import_image(bits, code)
                data = bytearray(data)
                struct.pack_into('<I' if bits == 32 else '<Q', data,
                                 0x98 + (28 if bits == 32 else 24), base)
                with self.subTest(bits=bits, form=label):
                    f = self.scan_image(bytes(data), import_iat_rva=0x1100)['findings']
                    self.assertEqual(f['matches'], [])
                    self.assertIsNone(f['target']['runtime_target'])

    def test_bad_selectors_and_other_actions_fail_before_worker(self):
        from unittest.mock import patch
        data, _ = import_image()
        (self.root / 'image.dll').write_bytes(data)
        cases = [dict(import_iat_rva=x) for x in (True, -1, 2**32, '4352', 1.0)]
        cases += [dict(import_iat_rva=0x1100, **extra) for extra in (
            dict(symbol='ExitProcess'), dict(target_address=0), dict(base_address=0),
            dict(offset=1), dict(byte_offset=0), dict(architecture='arm'))]
        with patch('subprocess.Popen', side_effect=AssertionError('invalid input started a worker')):
            for options in cases:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    analyze('references', 'image.dll', self.root, **options)
            for action in ('inspect', 'strings', 'binary', 'inventory', 'source', 'disassemble'):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    analyze(action, 'image.dll', self.root, import_iat_rva=0x1100)

    def test_missing_duplicate_malformed_and_budgeted_tables_fail_closed(self):
        from unittest.mock import patch
        from tests.test_reverse_pe_imports import directory, put
        original, _ = import_image()
        variants = {}
        data = bytearray(original)
        directory(data, 1, 0, 0, 64)
        variants['absent'] = data
        data = bytearray(original)
        directory(data, 1, 0x1000, 60, 64)
        put(data, 0x1014, bytes(data[0x200:0x214]))
        variants['duplicate'] = data
        data = bytearray(original)
        directory(data, 1, 0x1000, 60, 64)
        put(data, 0x1014, struct.pack('<IIIII', 0, 0, 0, 0, 0x1100))
        variants['malformed_after_match'] = data
        data = bytearray(original)
        put(data, 0x1000, struct.pack('<IIIII', 0, 123, 0, 0x1080, 0x1100))
        variants['bound_without_lookup'] = data
        with patch('subprocess.Popen', side_effect=AssertionError('bad table started a worker')):
            for label, data in variants.items():
                with self.subTest(variant=label), self.assertRaises(ValueError):
                    self.scan_image(bytes(data), import_iat_rva=0x1100)
            for slot in (0x1101, 0x1110, 0xdeadbeef):
                with self.subTest(slot=slot), self.assertRaises(ValueError):
                    self.scan_image(original, import_iat_rva=slot)
            with patch('zeline.reverse_pe_imports.MAX_THUNKS', 1), self.assertRaisesRegex(ValueError, 'budget'):
                self.scan_image(original, import_iat_rva=0x1100)

    def test_import_selection_streams_beyond_first_binary_table_page(self):
        from tests.test_reverse_pe_imports import put
        for bits in (32, 64):
            width = bits // 8
            slot = 0x1400 + 129 * width
            base = 0x400000 if bits == 32 else 0x140000000
            value = base + slot if bits == 32 else slot - 0x3006
            code = b'\xff\x15' + struct.pack('<I' if bits == 32 else '<i', value)
            data, _ = import_image(bits, code)
            data = bytearray(data)
            put(data, 0x1000, struct.pack('<IIIII', 0x1400, 0, 0, 0x1080, 0x1400))
            fmt = 'I' if bits == 32 else 'Q'
            put(data, 0x1400, struct.pack('<' + fmt * 131,
                                        *([0x1180] * 129 + [(1 << (bits - 1)) | 9, 0])))
            with self.subTest(bits=bits):
                page = self.scan_image(bytes(data), import_iat_rva=slot)['findings']
                self.assertEqual(page['target']['import']['ordinal'], 9)
                self.assertEqual(len(page['matches']), 1)

    def test_cancellation_mid_import_scan_returns_no_partial_evidence(self):
        from unittest.mock import patch
        from zeline.reverse_references import scan_references
        data, _ = import_image()
        calls = 0

        def cancelled():
            nonlocal calls
            calls += 1
            return calls > 12

        with patch('subprocess.Popen', side_effect=AssertionError('cancelled scan started a worker')):
            with self.assertRaisesRegex((ValueError, InterruptedError), 'cancelled'):
                scan_references(data, import_iat_rva=0x1100, should_stop=cancelled)
