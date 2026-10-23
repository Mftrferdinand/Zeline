"""Native binary details preserve headers and expose bounded table pages."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_reverse_binary import elf, pe


class BinaryDetailsTests(unittest.TestCase):
    def reader(self):
        from zeline import reverse_binary
        self.assertTrue(hasattr(reverse_binary, 'inspect_binary_details'),
                        'A paginated binary-details API is required')
        return reverse_binary

    def test_header_contract_preserved_without_symbol_tables(self):
        module = self.reader()
        for fixture in [bytes(elf()), bytes(pe())]:
            with self.subTest(format=fixture[:4]):
                headers = module.inspect_binary(fixture)
                details = module.inspect_binary_details(fixture)
                self.assertEqual(details['metadata'], headers['metadata'])
                self.assertEqual(details['sections'], headers['sections'])
                for name in ['imports', 'exports', 'symbols']:
                    page = details[name]
                    self.assertEqual(page['entries'], [])
                    self.assertEqual(page['total_entries'], 0)
                    self.assertIsNone(page['next_offset'])
                    self.assertTrue(page['exhausted'])
                self.assertTrue(details['limitations'])
                if details['format'] == 'elf':
                    self.assertIn('section names', ' '.join(details['limitations']))

    def test_details_validate_pagination_and_cancellation(self):
        module = self.reader()
        for options in [{'offset': -1}, {'limit': 0}, {'limit': 101}, {'offset': True}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                module.inspect_binary_details(bytes(elf()), **options)
        with self.assertRaises(InterruptedError):
            module.inspect_binary_details(bytes(elf()), should_stop=lambda: True)

    def test_real_native_engine_forwards_binary_page_parameters(self):
        module = self.reader()
        from zeline.reverse_engine import analyze
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'target.elf').write_bytes(bytes(elf()))
            with patch.object(module, 'inspect_binary_details', wraps=module.inspect_binary_details) as reader:
                result = analyze('binary', 'target.elf', root, offset=7, limit=2)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['findings']['symbols']['offset'], 7)
            self.assertEqual(reader.call_args.kwargs['offset'], 7)
            self.assertEqual(reader.call_args.kwargs['limit'], 2)
