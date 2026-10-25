"""Hash-bound continuation through the native dispatcher on inert artifacts."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_public_core import fresh_zeline


class ReverseContinuationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        override = patch.dict(os.environ, {'ZELINE_HOME': str(self.root / 'state')})
        override.start()
        self.addCleanup(override.stop)
        self.config, _, self.tools = fresh_zeline(self.root / 'state')
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.path = self.workspace / 'fixture.bin'
        self.path.write_bytes(b'\x00alpha\x00bravo\x00charlie\x00')
        self.executor = self.tools.ToolExecutor('cli:continuation-test', 'workspace', self.workspace)

    def run_tool(self, **options):
        return self.executor.run('reverse_engineer', dict(action='strings', path='fixture.bin', **options))

    def test_matching_digest_continues_the_real_string_page(self):
        first = json.loads(self.run_tool(limit=1))
        second_raw = self.run_tool(offset=first['findings']['next_offset'], limit=1,
                                   expected_sha256=first['artifact']['sha256'])
        self.assertFalse(second_raw.startswith('ERROR'), second_raw)
        second = json.loads(second_raw)
        self.assertEqual(second['findings']['matches'][0]['value'], 'bravo')
        self.assertEqual(first['artifact']['sha256'], second['artifact']['sha256'])
        properties = next(d for d in self.tools.TOOL_DEFS if d.name == 'reverse_engineer').parameters['properties']
        self.assertEqual(properties['expected_sha256']['type'], 'string')
        self.assertEqual(properties['expected_sha256']['minLength'], 64)
        self.assertEqual(properties['expected_sha256']['maxLength'], 64)


    def test_changed_file_is_rejected_before_decoder_dispatch(self):
        first = json.loads(self.run_tool(limit=1))
        self.path.write_bytes(b'\x90\xc3')
        decoder = importlib.import_module('zeline.reverse_disassembly')
        with patch.object(decoder, 'decode_instructions', side_effect=AssertionError('decoder must not run')) as worker:
            raw = self.executor.run('reverse_engineer', dict(action='disassemble', path='fixture.bin',
                architecture='x86_64', expected_sha256=first['artifact']['sha256']))
        self.assertTrue(raw.startswith('ERROR static analysis:'), raw)
        self.assertIn('digest mismatch', raw)
        worker.assert_not_called()


    def test_mismatch_rejects_all_parser_paths_before_analysis(self):
        expected = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.path.write_bytes(b'changed fixture')
        operations = [
            ('binary', 'zeline.reverse_binary', 'inspect_binary_details', {}),
            ('source', 'zeline.reverse_source', 'inspect_source', {'language': 'python'}),
            ('references', 'zeline.reverse_references', 'scan_references', {'target_address': 0}),
        ]
        for action, module_name, function, options in operations:
            module = importlib.import_module(module_name)
            with self.subTest(action=action), patch.object(module, function) as parser:
                raw = self.executor.run('reverse_engineer', dict(
                    action=action, path='fixture.bin', expected_sha256=expected, **options))
                self.assertTrue(raw.startswith('ERROR static analysis:'), raw)
                self.assertIn('digest mismatch', raw)
                parser.assert_not_called()

    def test_matching_digest_continues_real_instruction_pages(self):
        self.path.write_bytes(b'\x90\x90\xc3')
        first = json.loads(self.executor.run('reverse_engineer', dict(action='disassemble',
            path='fixture.bin', architecture='x86_64', limit=1)))
        second = json.loads(self.executor.run('reverse_engineer', dict(action='disassemble',
            path='fixture.bin', architecture='x86_64', limit=1,
            byte_offset=first['findings']['next_byte_offset'], expected_sha256=first['artifact']['sha256'])))
        self.assertEqual(first['findings']['bytes_consumed'], 1)
        self.assertEqual(second['findings']['byte_offset'], 1)
        self.assertEqual(first['artifact'], second['artifact'])
        self.assertNotEqual(first['evidence_id'], second['evidence_id'])

    def test_zip_inventory_can_bind_its_full_file_digest(self):
        import zipfile
        archive_path = self.workspace / 'fixture.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('first.txt', 'one')
            archive.writestr('second.txt', 'two')
        options = dict(action='inventory', path='fixture.zip', limit=1)
        first = json.loads(self.executor.run('reverse_engineer', options))
        options.update(offset=first['findings']['next_offset'], expected_sha256=first['artifact']['sha256'])
        second = json.loads(self.executor.run('reverse_engineer', options))
        self.assertEqual(second['findings']['entries'][0]['path'], 'second.txt')
        self.assertEqual(first['artifact'], second['artifact'])
        with zipfile.ZipFile(archive_path, 'a') as archive:
            archive.writestr('third.txt', 'three')
        raw = self.executor.run('reverse_engineer', options)
        self.assertTrue(raw.startswith('ERROR static analysis:'), raw)
        self.assertIn('digest mismatch', raw)
        self.assertFalse((self.workspace / 'first.txt').exists())

    def test_invalid_expected_digests_are_rejected_before_file_capture(self):
        engine = importlib.import_module('zeline.reverse_engine')
        invalid = ['', 'a' * 63, 'a' * 65, 'g' * 64, 'a' * 64 + '\n', True, 17, [], {}]
        for expected in invalid:
            with self.subTest(expected=expected), patch.object(engine, '_file_bytes', wraps=engine._file_bytes) as capture:
                with self.assertRaisesRegex(ValueError, '64 hexadecimal'):
                    engine.analyze('inspect', 'fixture.bin', self.workspace, expected_sha256=expected)
                capture.assert_not_called()

    def test_directory_cannot_claim_a_content_digest(self):
        raw = self.executor.run('reverse_engineer', dict(action='inventory', path='.', expected_sha256='0' * 64))
        self.assertTrue(raw.startswith('ERROR static analysis:'), raw)
        self.assertIn('directories', raw)

    def test_digest_accepts_uppercase_hex_without_changing_evidence(self):
        first = json.loads(self.run_tool(limit=1))
        raw = self.run_tool(limit=1, expected_sha256=first['artifact']['sha256'].upper())
        self.assertFalse(raw.startswith('ERROR'), raw)
        self.assertEqual(json.loads(raw), first)

    def test_expected_digest_does_not_bypass_safe_or_disabled_profile(self):
        expected = hashlib.sha256(self.path.read_bytes()).hexdigest()
        options = dict(action='inspect', path='fixture.bin', expected_sha256=expected)
        public = self.tools.ToolExecutor('telegram:continuation-test', 'safe', self.workspace)
        self.assertTrue(public.run('reverse_engineer', options).startswith('ERROR'))
        with patch.object(self.config, 'DISABLED_TOOLS', ['reverse_engineer']):
            disabled = self.tools.ToolExecutor('cli:continuation-test', 'workspace', self.workspace)
            self.assertTrue(disabled.run('reverse_engineer', options).startswith('ERROR'))


if __name__ == '__main__':
    unittest.main()
