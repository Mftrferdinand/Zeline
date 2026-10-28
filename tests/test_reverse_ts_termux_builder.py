"""The optional Termux TS grammar wheel-builder must verify pinned inputs."""
import importlib.util
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/build_reverse_ts_termux.py'


class TypeScriptTermuxBuilderTests(unittest.TestCase):
    def test_builder_has_explicit_wheel_output_and_help(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--help'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--output-dir', result.stdout)
        self.assertNotIn('--install', result.stdout)

    def test_tampered_download_is_rejected_before_extraction(self):
        spec = importlib.util.spec_from_file_location('build_reverse_ts_termux', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.urllib.request, 'urlopen', return_value=io.BytesIO(b'tampered')):
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                module._download('https://example.invalid/test', '0' * 64)


if __name__ == '__main__':
    unittest.main()
