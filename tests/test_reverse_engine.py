"""Static engine behavior exercised on inert workspace fixtures."""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


class ReverseEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def engine(self):
        self.assertIsNotNone(importlib.util.find_spec('zeline.reverse_engine'),
                             'The built-in static engine must exist')
        from zeline import reverse_engine
        return reverse_engine

    def test_zip_inventory_never_extracts_and_flags_traversal(self):
        import zipfile
        target = self.root / 'sample.zip'
        with zipfile.ZipFile(target, 'w') as archive:
            archive.writestr('../outside.txt', 'inert fixture')
            archive.writestr('safe.txt', 'hello')
        result = self.engine().analyze('inventory', 'sample.zip', self.root, limit=1)
        entry = result['findings']['entries'][0]
        self.assertEqual(entry['path'], '../outside.txt')
        self.assertTrue(entry['unsafe_path'])
        self.assertEqual(result['findings']['next_offset'], 1)
        self.assertFalse((self.root.parent / 'outside.txt').exists())
        self.assertFalse((self.root / 'safe.txt').exists())

    def test_directory_inventory_is_paged_without_following_children(self):
        for index in range(5):
            (self.root / f'file-{index}.bin').write_bytes(bytes([index]))
        first = self.engine().analyze('inventory', '.', self.root, limit=2)
        self.assertEqual(len(first['findings']['entries']), 2)
        self.assertEqual(first['findings']['next_offset'], 2)
        self.assertIsNone(first['artifact']['sha256'])
        entries = list(first['findings']['entries'])
        offset = first['findings']['next_offset']
        while offset is not None:
            page = self.engine().analyze('inventory', '.', self.root, offset=offset, limit=2)
            entries.extend(page['findings']['entries'])
            offset = page['findings']['next_offset']
        self.assertEqual({entry['path'] for entry in entries}, {f'file-{i}.bin' for i in range(5)})

    def test_rejects_traversal_and_outside_absolute_path(self):
        engine = self.engine()
        for name in ['../private.bin', str(self.root.parent / 'private.bin')]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                engine.analyze('inspect', name, self.root)

    def test_rejects_observed_symlink_escape(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'private.bin'
            target.write_bytes(b'PRIVATE_TEST_FIXTURE')
            try:
                (self.root / 'link.bin').symlink_to(target)
                (self.root / 'linkdir').symlink_to(Path(outside), target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('host cannot create symlinks')
            for name in ['link.bin', 'linkdir/private.bin']:
                with self.subTest(name=name), self.assertRaises((OSError, ValueError)):
                    self.engine().analyze('inspect', name, self.root)

    def test_limits_and_cancellation_are_explicit(self):
        (self.root / 'small.bin').write_bytes(b'hello')
        engine = self.engine()
        with self.assertRaises(InterruptedError):
            engine.analyze('inspect', 'small.bin', self.root, should_stop=lambda: True)
        for options in [{'limit': 0}, {'offset': -1}, {'limit': True}, {'min_length': 0}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                engine.analyze('inspect', 'small.bin', self.root, **options)
        (self.root / 'large.bin').write_bytes(b'\x00' * (engine.MAX_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            engine.analyze('inspect', 'large.bin', self.root)

    def test_ascii_strings_page_with_exact_byte_offsets(self):
        (self.root / 'strings.bin').write_bytes(b'\x00alpha\x00beta\x00gamma\x00')
        first = self.engine().analyze('strings', 'strings.bin', self.root, limit=2)
        self.assertEqual([(m['value'], m['offset']) for m in first['findings']['matches']],
                         [('alpha', 1), ('beta', 7)])
        self.assertEqual(first['findings']['next_offset'], 2)
        second = self.engine().analyze('strings', 'strings.bin', self.root, offset=2, limit=2)
        self.assertEqual(second['findings']['matches'][0]['value'], 'gamma')
        self.assertIsNone(second['findings']['next_offset'])

    def test_strings_report_long_match_truncation(self):
        (self.root / 'long.bin').write_bytes(b'A' * 2000)
        result = self.engine().analyze('strings', 'long.bin', self.root)
        match = result['findings']['matches'][0]
        self.assertTrue(match['truncated'])
        self.assertEqual(match['length_bytes'], 2000)
        self.assertLessEqual(len(match['value']), 256)

    def test_inspect_has_full_digest_and_reproducible_evidence(self):
        data = b'\x00HELLO_STATIC_WORLD\xff'
        (self.root / 'sample.bin').write_bytes(data)
        engine = self.engine()
        first = engine.analyze('inspect', 'sample.bin', self.root)
        second = engine.analyze('inspect', 'sample.bin', self.root)
        self.assertEqual(first['artifact']['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(first['artifact']['size_bytes'], len(data))
        self.assertEqual(first['operation'], 'inspect')
        self.assertEqual(first['status'], 'ok')
        self.assertEqual(first['evidence_id'], second['evidence_id'])
        self.assertEqual(first['findings']['format'], 'unknown')
        self.assertTrue(first['limitations'])
