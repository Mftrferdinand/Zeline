"""Native static disassembly preserves evidence, path policy and byte cursors."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zeline.reverse_engine import analyze


class DisassemblyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = bytes.fromhex('90e800000000c3')
        (self.root / 'code.bin').write_bytes(self.code)

    def test_raw_byte_pagination_addresses_and_evidence(self):
        options = dict(architecture='x86_64', base_address=0x1000, limit=1)
        first = analyze('disassemble', 'code.bin', self.root, **options)
        findings = first['findings']
        self.assertEqual(findings['instructions'][0]['address'], 0x1000)
        self.assertEqual(findings['instructions'][0]['bytes_hex'], '90')
        self.assertEqual(findings['next_byte_offset'], 1)
        self.assertFalse(findings['exhausted'])
        self.assertEqual(first['artifact']['sha256'], hashlib.sha256(self.code).hexdigest())
        self.assertEqual(first, analyze('disassemble', 'code.bin', self.root, **options))
        second = analyze('disassemble', 'code.bin', self.root, byte_offset=1, **options)
        instruction = second['findings']['instructions'][0]
        self.assertEqual(instruction['flow']['kind'], 'call')
        self.assertEqual(instruction['flow']['target'], 0x1006)
        self.assertEqual(instruction['file_offset'], 1)
        self.assertEqual(second['findings']['next_byte_offset'], 6)
        last = analyze('disassemble', 'code.bin', self.root, byte_offset=6, **options)
        self.assertTrue(last['findings']['exhausted'])
        self.assertIsNone(last['findings']['next_byte_offset'])
        self.assertEqual((self.root / 'code.bin').read_bytes(), self.code)

    def test_no_decode_skipping_and_no_false_exhaustion(self):
        (self.root / 'incomplete.bin').write_bytes(bytes.fromhex('900f'))
        result = analyze('disassemble', 'incomplete.bin', self.root, architecture='x86_64')
        findings = result['findings']
        self.assertEqual(len(findings['instructions']), 1)
        self.assertEqual(findings['stop_reason'], 'undecodable')
        self.assertEqual(findings['stopped_at_byte_offset'], 1)
        self.assertEqual(findings['bytes_remaining'], 1)
        self.assertFalse(findings['exhausted'])
        self.assertIsNone(findings['next_byte_offset'])

    def test_arm_implicit_pc_writes_do_not_invent_linear_fallthrough(self):
        for architecture, code in [('arm', '1080bde8'), ('thumb', '10bd')]:
            with self.subTest(architecture=architecture):
                (self.root / 'return.raw').write_bytes(bytes.fromhex(code))
                result = analyze('disassemble', 'return.raw', self.root, architecture=architecture)
                flow = result['findings']['instructions'][0]['flow']
                self.assertIn(flow['kind'], ('jump', 'return'))
                self.assertIsNone(flow['target'])
                self.assertIsNone(flow['fallthrough'])

    def test_unsigned_control_target_does_not_leak_signed_binding_storage(self):
        (self.root / 'wrap.raw').write_bytes(bytes.fromhex('e9faffffff'))
        result = analyze('disassemble', 'wrap.raw', self.root, architecture='x86_64')
        self.assertEqual(result['findings']['instructions'][0]['flow']['target'], (1 << 64) - 1)

    def test_32bit_address_overflow_is_rejected_before_decoding(self):
        for architecture in ('x86', 'arm', 'thumb'):
            with self.subTest(architecture=architecture), self.assertRaises(ValueError):
                analyze('disassemble', 'code.bin', self.root, architecture=architecture, base_address=1 << 32)

    def test_byte_cursor_is_not_entry_offset_and_options_are_action_specific(self):
        with self.assertRaises(ValueError):
            analyze('disassemble', 'code.bin', self.root, offset=1, architecture='x86_64')
        for options in [dict(architecture='x86_64'), dict(byte_offset=0), dict(base_address=0)]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                analyze('inspect', 'code.bin', self.root, **options)

    def test_path_and_cancellation_guards_precede_worker(self):
        with self.assertRaises(ValueError):
            analyze('disassemble', '../private.bin', self.root, architecture='x86_64')
        with self.assertRaises(InterruptedError):
            analyze('disassemble', 'code.bin', self.root, architecture='x86_64', should_stop=lambda: True)

    def test_old_binary_inspection_does_not_spawn_a_decoder(self):
        from tests.test_reverse_binary import elf
        (self.root / 'metadata.elf').write_bytes(elf())
        with patch('subprocess.Popen', side_effect=AssertionError('unexpected subprocess')):
            result = analyze('binary', 'metadata.elf', self.root)
        self.assertEqual(result['status'], 'ok')


class DisassemblyDispatchTests(unittest.TestCase):
    def test_schema_and_real_profile_gates(self):
        from tests.test_public_core import fresh_zeline
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {'ZELINE_HOME': str(root / 'state')}):
                _, _, tools = fresh_zeline(root / 'state')
                workspace = root / 'workspace'
                workspace.mkdir()
                (workspace / 'code.bin').write_bytes(bytes.fromhex('90c3'))
                definition = next(d for d in tools.TOOL_DEFS if d.name == 'reverse_engineer')
                properties = definition.parameters['properties']
                self.assertIn('disassemble', properties['action']['enum'])
                self.assertIn('references', properties['action']['enum'])
                self.assertIn('target_address', properties)
                self.assertIn('byte_offset', properties)
                self.assertIn('architecture', properties)
                self.assertIn('base_address', properties)
                self.assertIn('symbol', properties)
                executor = tools.ToolExecutor('cli:disassembly-test', 'workspace', workspace)
                self.assertIsNone(executor.mcp)
                arguments = dict(action='disassemble', path='code.bin', architecture='x86_64', limit=1)
                result = json.loads(executor.run('reverse_engineer', arguments))
                self.assertEqual(result['findings']['next_byte_offset'], 1)
                from tests.test_reverse_symbol_navigation import code_elf
                fixture, payload = code_elf()
                (workspace / 'code.elf').write_bytes(fixture)
                named = dict(action='disassemble', path='code.elf', symbol='public', limit=1)
                selected = json.loads(executor.run('reverse_engineer', named))
                self.assertEqual(selected['findings']['byte_offset'], payload)
                self.assertEqual(selected['findings']['selected_symbol']['name'], 'public')
                fixture[payload:payload + 4] = bytes.fromhex('ebfe90c3')
                (workspace / 'refs.elf').write_bytes(fixture)
                refs = dict(action='references', path='refs.elf', symbol='public', limit=1)
                reference = json.loads(executor.run('reverse_engineer', refs))
                self.assertEqual(reference['findings']['matches'][0]['kind'], 'jump')
                numeric = dict(action='references', path='refs.elf', target_address=0x1234, limit=1)
                self.assertEqual(json.loads(executor.run('reverse_engineer', numeric))['findings']['target']['address'], 0x1234)
                safe = tools.ToolExecutor('telegram:disassembly-test', 'safe', workspace)
                with patch('subprocess.Popen', side_effect=AssertionError('safe profile spawned worker')):
                    self.assertTrue(safe.run('reverse_engineer', arguments).startswith('ERROR'))
                    self.assertTrue(safe.run('reverse_engineer', named).startswith('ERROR'))
                    self.assertTrue(safe.run('reverse_engineer', refs).startswith('ERROR'))
