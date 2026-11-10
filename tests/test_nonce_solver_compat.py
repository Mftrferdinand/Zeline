"""Real offline compatibility test for the supplied nonce v3 command shape."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'zeline/skills/zeline-sat-solvers/scripts/nonce_solver_v3.py'


class NonceCompatibilityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('z3'), 'Real Z3 executable is required')
    def test_legacy_cli_returns_verified_bits_with_declared_unused_variables(self):
        with tempfile.TemporaryDirectory() as d:
            source, output = Path(d) / 'clauses.json', Path(d) / 'assignment.txt'
            source.write_text('[[1], [-1, 2]]')
            result = subprocess.run([sys.executable, str(SCRIPT), str(source), '3',
                                     '--vars', '4', '--solver', 'z3', '--out', str(output)],
                                    text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr[:500])
            bits = result.stdout.strip()
            self.assertEqual(len(bits), 4)
            self.assertEqual(bits[:2], '11')
            self.assertTrue(set(bits) <= {'0', '1'})
            self.assertEqual(output.read_text().strip(), bits)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertIn('"verified": true', result.stderr)


if __name__ == '__main__':
    unittest.main()
