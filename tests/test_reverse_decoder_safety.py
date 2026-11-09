"""Additional decoder correctness guards discovered during real-fixture review."""
import os
import subprocess
import unittest
from unittest.mock import patch
from zeline.reverse_disassembly import decode_instructions


class DecoderSafetyTests(unittest.TestCase):
    def test_far_selector_offset_is_not_a_resolved_linear_target(self):
        result = decode_instructions(bytes.fromhex('ea785634120800'), 'x86')
        flow = result['instructions'][0]['flow']
        self.assertEqual(flow['kind'], 'jump')
        self.assertIsNone(flow['target'])
        self.assertIsNone(flow['fallthrough'])

    def test_computed_arm_pc_write_has_no_absolute_immediate_target(self):
        result = decode_instructions(bytes.fromhex('04f08fe2'), 'arm')
        flow = result['instructions'][0]['flow']
        self.assertEqual(flow['kind'], 'jump')
        self.assertIsNone(flow['target'])
        self.assertIsNone(flow['fallthrough'])

    def test_32bit_decoder_address_window_must_fit_architecture(self):
        for architecture in ('x86', 'arm', 'thumb'):
            with self.subTest(architecture=architecture), self.assertRaises(ValueError):
                decode_instructions(b'\x90', architecture, address=1 << 32)

    def test_a64_al_nv_are_always_taken_but_real_conditions_remain_conditional(self):
        for opcode, conditional in [('0e000054', False), ('0f000054', False),
                                     ('00000054', True), ('01000054', True),
                                     ('000000b4', True), ('00000836', True)]:
            with self.subTest(opcode=opcode):
                result = decode_instructions(bytes.fromhex(opcode + '1f2003d5'), 'aarch64')
                flow = result['instructions'][0]['flow']
                self.assertEqual(flow['conditional'], conditional)
                self.assertEqual(flow['fallthrough'], 4 if conditional else None)
                first_edges = result['blocks'][0]['edges']
                self.assertEqual(any(e['kind'] == 'fallthrough' for e in first_edges), conditional)

    def test_fallthrough_respects_architectural_address_width_without_inventing_wrap(self):
        for arch, code, address, expected in [
                ('x86', '90', 0xffffffff, None),
                ('arm', '0000a0e1', 0xfffffffc, None),
                ('thumb', '00bf', 0xfffffffe, None),
                ('x86', '90', 0xfffffffe, 0xffffffff),
                ('arm', '0000a0e1', 0xfffffff8, 0xfffffffc),
                ('aarch64', '1f2003d5', 0xfffffffffffffffc, None),
                ('x86_64', '90', 0xffffffffffffffff, None)]:
            with self.subTest(architecture=arch, address=address):
                result = decode_instructions(bytes.fromhex(code), arch, address=address)
                self.assertEqual(result['instructions'][0]['flow']['fallthrough'], expected)
                if expected is None:
                    self.assertFalse(any(e['kind'] == 'fallthrough' for e in result['blocks'][0]['edges']))

    def test_ud2_terminates_normal_block_with_unresolved_trap_edge(self):
        for arch in ('x86', 'x86_64'):
            with self.subTest(architecture=arch):
                result = decode_instructions(bytes.fromhex('0f0b90'), arch)
                self.assertEqual(result['instructions'][0]['flow'],
                                 dict(kind='trap', target=None, conditional=False, fallthrough=None))
                self.assertEqual(result['blocks'][0]['instructions'], [0])
                self.assertEqual(result['blocks'][0]['edges'],
                                 [dict(kind='trap', target=None, scope='unresolved')])
                self.assertEqual(result['blocks'][1]['instructions'], [2])
                self.assertEqual(result['instructions'][1]['mnemonic'], 'nop')
                self.assertEqual(result['bytes_consumed'], 3)
                self.assertEqual(result['stop_reason'], 'end')
                self.assertEqual(result['xrefs'], [])

    def test_worker_does_not_inherit_api_credentials_or_library_override(self):
        original = subprocess.Popen
        def launch(*args, **kwargs):
            environment = kwargs.get('env')
            self.assertIsInstance(environment, dict)
            self.assertNotIn('ZELINE_API_KEY', environment)
            self.assertNotIn('LIBCAPSTONE_PATH', environment)
            return original(*args, **kwargs)
        with patch.dict(os.environ, {'ZELINE_API_KEY': 'test-only-marker', 'LIBCAPSTONE_PATH': '/nonexistent/test-only'}):
            with patch('subprocess.Popen', side_effect=launch):
                self.assertEqual(decode_instructions(b'\x90', 'x86_64')['instructions'][0]['mnemonic'], 'nop')
