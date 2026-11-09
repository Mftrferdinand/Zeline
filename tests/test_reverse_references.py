"""Incoming references are bounded direct-transfer observations, not a call graph."""
import struct
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from zeline.reverse_binary import inspect_binary
from zeline.reverse_engine import analyze
from tests.test_reverse_binary import elf


def code_image(code):
    data = elf()
    header = inspect_binary(bytes(data))
    payload = header['sections'][1]['offset']
    data[payload:] = code
    struct.pack_into('<Q', data, 64 + 64 + 32, len(code))
    return bytes(data), payload


def two_regions(first=b'\xc3'):
    names = b'\0.one\0.two\0.shstrtab\0'
    start = 64 + 4 * 64
    a = start + len(names)
    b = a + len(first) + 17
    second = b'\xe9' + struct.pack('<i', 0x1000 - (0x2000 + 5))
    data = bytearray(b + len(second))
    data[:64] = elf()[:64]
    struct.pack_into('<Q', data, 24, 0x2000)
    struct.pack_into('<HH', data, 60, 4, 3)
    for index, name, address, offset, size, flags, kind in (
            (1, 1, 0x1000, a, len(first), 6, 1), (2, 6, 0x2000, b, len(second), 6, 1),
            (3, 11, 0, start, len(names), 0, 3)):
        struct.pack_into('<IIQQQQIIQQ', data, 64 + index * 64,
                         name, kind, flags, address, offset, size, 0, 0, 1, 0)
    data[start:start + len(names)] = names
    data[a:a + len(first)] = first
    data[b:] = second
    return bytes(data), a, b


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_code(self, code, **options):
        data, payload = code_image(code)
        (self.root / 'code.elf').write_bytes(data)
        result = analyze('references', 'code.elf', self.root, **options)
        return result, payload

    def test_direct_incoming_calls_jumps_and_unresolved_are_distinct(self):
        result, payload = self.run_code(bytes.fromhex('e80100000090c3ebfdffd0'), target_address=0x401006)
        f = result['findings']
        self.assertEqual([m['kind'] for m in f['matches']], ['call', 'jump'])
        self.assertEqual([m['file_offset'] for m in f['matches']], [payload, payload + 7])
        self.assertEqual(f['unresolved_calls_jumps'], 1)
        self.assertEqual(f['coverage']['bytes_decoded'], 11)
        self.assertFalse(f['coverage']['whole_program_complete'])
        self.assertTrue(f['exhausted'])
        self.assertEqual(result, analyze('references', 'code.elf', self.root, target_address=0x401006))

    def test_symbol_selects_target_not_scan_start_and_allows_continuation(self):
        from tests.test_reverse_symbol_navigation import code_elf
        data, payload = code_elf()
        data[payload:payload + 4] = bytes.fromhex('ebfe90c3')
        (self.root / 'named.elf').write_bytes(data)
        options = dict(symbol='public', limit=1)
        first = analyze('references', 'named.elf', self.root, **options)['findings']
        self.assertEqual(first['target']['address'], 0x1234)
        self.assertEqual(first['target']['declaration']['name'], 'public')
        self.assertEqual(first['matches'][0]['kind'], 'jump')
        self.assertEqual(first['next_byte_offset'], payload + 2)
        second = analyze('references', 'named.elf', self.root, byte_offset=first['next_byte_offset'], **options)['findings']
        self.assertEqual(second['matches'], [])
        self.assertEqual(second['coverage']['eligible_bytes_before_page'], 2)

    def test_pages_advance_across_declared_regions_not_entrypoint_or_gap(self):
        data, first, second = two_regions()
        (self.root / 'two.elf').write_bytes(data)
        page = analyze('references', 'two.elf', self.root, target_address=0x1000, limit=1)['findings']
        self.assertEqual(page['coverage']['start_byte_offset'], first)
        self.assertEqual(page['next_byte_offset'], second)
        self.assertFalse(page['exhausted'])
        last = analyze('references', 'two.elf', self.root, target_address=0x1000,
                       byte_offset=second, limit=1)['findings']
        self.assertEqual(last['matches'][0]['file_offset'], second)
        self.assertEqual(last['coverage']['eligible_bytes_before_page'], 1)
        self.assertTrue(last['exhausted'])
        self.assertFalse(last['coverage']['whole_program_complete'])

    def test_instruction_budget_and_coverage_do_not_claim_prior_pages(self):
        result, payload = self.run_code(b'\x90' * 9000, target_address=0x401000, limit=100)
        f = result['findings']
        self.assertEqual(f['coverage']['instructions_decoded'], 100)
        self.assertEqual(f['coverage']['bytes_decoded'], 100)
        self.assertEqual(f['next_byte_offset'], payload + 100)
        self.assertEqual(f['matches'], [])
        self.assertFalse(f['exhausted'])
        later = analyze('references', 'code.elf', self.root, target_address=0x401000,
                        byte_offset=payload + 8999, limit=1)['findings']
        self.assertEqual(later['coverage']['eligible_bytes_before_page'], 8999)
        self.assertFalse(later['coverage']['whole_program_complete'])
        self.assertTrue(later['exhausted'])

    def test_undecodable_tail_does_not_silently_skip_to_another_region(self):
        data, first, second = two_regions(bytes.fromhex('900f'))
        (self.root / 'two.elf').write_bytes(data)
        f = analyze('references', 'two.elf', self.root, target_address=0x1000)['findings']
        self.assertEqual(f['stop_reason'], 'undecodable')
        self.assertEqual(f['coverage']['bytes_decoded'], 1)
        self.assertEqual(f['coverage']['end_byte_offset'], first + 1)
        self.assertIsNone(f['next_byte_offset'])
        self.assertEqual(f['next_region_byte_offset'], second)
        self.assertFalse(f['exhausted'])

    def test_pe32_and_pe64_use_image_base_and_exclude_raw_padding(self):
        from tests.test_reverse_binary import pe
        for bits in (32, 64):
            data = pe(bits)
            header = inspect_binary(bytes(data))
            payload = header['sections'][0]['offset']
            section = 0x80 + 24 + (224 if bits == 32 else 240)
            struct.pack_into('<I', data, section + 16, 8)
            data[payload:] = bytes.fromhex('ebfec390cccccccc')
            (self.root / 'code.dll').write_bytes(data)
            result = analyze('references', 'code.dll', self.root, target_address=0x401000)['findings']
            with self.subTest(bits=bits):
                self.assertEqual(result['matches'][0]['address'], 0x401000)
                self.assertEqual(result['coverage']['eligible_bytes_total'], 4)
                self.assertEqual(result['coverage']['bytes_decoded'], 4)
                self.assertTrue(result['exhausted'])

    def test_zero_address_is_a_real_target_not_a_missing_selector(self):
        code = b'\xe9' + struct.pack('<i', 0 - (0x401000 + 5))
        result, _ = self.run_code(code, target_address=0)
        self.assertEqual(result['findings']['matches'][0]['target'], 0)

    def test_invalid_controls_fail_before_worker(self):
        data, _ = code_image(b'\xc3')
        (self.root / 'code.elf').write_bytes(data)
        controls = [dict(), dict(symbol='x', target_address=1), dict(target_address=True),
                    dict(target_address=-1), dict(target_address=1 << 64), dict(target_address='1'),
                    dict(target_address=1, byte_offset=True), dict(target_address=1, offset=1),
                    dict(target_address=1, architecture=False), dict(target_address=1, architecture='arm'),
                    dict(target_address=1, base_address=0), dict(target_address=1, limit=True),
                    dict(target_address=1, limit=101), dict(target_address=1, byte_offset=0)]
        with patch('subprocess.Popen', side_effect=AssertionError('invalid request launched a worker')):
            for options in controls:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    analyze('references', 'code.elf', self.root, **options)
            for action in ('inspect', 'binary', 'disassemble', 'source', 'strings', 'inventory'):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    analyze(action, 'code.elf', self.root, target_address=0)

    def test_unrelocated_compressed_and_overlapping_images_are_rejected(self):
        base, _, _ = two_regions()
        for variant in ('rel', 'core', 'compressed', 'overlap'):
            data = bytearray(base)
            if variant in ('rel', 'core'):
                struct.pack_into('<H', data, 16, 1 if variant == 'rel' else 4)
            elif variant == 'compressed':
                struct.pack_into('<Q', data, 64 + 64 + 8, 6 | 0x800)
            else:
                struct.pack_into('<Q', data, 64 + 2 * 64 + 16, 0x1000)
            (self.root / 'bad.elf').write_bytes(data)
            with self.subTest(variant=variant), patch('subprocess.Popen', side_effect=AssertionError('bad image launched worker')):
                with self.assertRaises(ValueError):
                    analyze('references', 'bad.elf', self.root, target_address=0x1000)

    def test_path_raw_and_cancellation_fail_without_launching_target(self):
        (self.root / 'raw.bin').write_bytes(b'\xc3')
        with patch('subprocess.Popen', side_effect=AssertionError('unexpected worker')):
            with self.assertRaises(ValueError):
                analyze('references', '../raw.bin', self.root, target_address=0)
            with self.assertRaises(ValueError):
                analyze('references', 'raw.bin', self.root, target_address=0, architecture='x86_64')
            with self.assertRaises(InterruptedError):
                analyze('references', 'raw.bin', self.root, target_address=0, should_stop=lambda: True)

    def test_big_endian_elf_preserves_data_order_and_a64_instruction_order(self):
        data = elf(64, '>')
        struct.pack_into('>H', data, 18, 183)
        payload = inspect_binary(bytes(data))['sections'][1]['offset']
        data[payload:] = bytes.fromhex('00000014')
        (self.root / 'be.elf').write_bytes(data)
        f = analyze('references', 'be.elf', self.root, target_address=0x401000)['findings']
        self.assertEqual(f['data_endianness'], 'big')
        self.assertEqual(f['endianness'], 'little')
        self.assertEqual(f['format'], 'elf')
        self.assertEqual(f['matches'][0]['bytes_hex'], '00000014')
