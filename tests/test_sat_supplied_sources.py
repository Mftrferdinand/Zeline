"""Offline provenance and boundary checks for the supplied custom probSAT-style engine."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'zeline' / 'skills' / 'zeline-sat-solvers'
ORIGINAL = SKILL / 'assets' / 'supplied' / 'nonce-synced-v1-v3' / 'probsab.c'
ADAPTED = SKILL / 'assets' / 'adapted' / 'probsab.c'


class SuppliedSolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ADAPTED.exists() or not ORIGINAL.exists():
            raise unittest.SkipTest('operator-private uploaded source is not part of public distribution')
        compiler = shutil.which('clang') or shutil.which('gcc')
        if compiler is None:
            raise unittest.SkipTest('Native C compiler is not installed')
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.binary = cls.directory / 'probsab'
        source = ADAPTED if ADAPTED.exists() else ORIGINAL
        subprocess.run([compiler, '-O2', str(source), '-lm', '-o', str(cls.binary)],
                       capture_output=True, check=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_cnf(self, text):
        fixture = self.directory / 'test.cnf'
        fixture.write_text(text)
        return subprocess.run([str(self.binary), str(fixture), '1', '2.6', '0.1'],
                              capture_output=True, text=True, timeout=3)

    def test_valid_sat_assignment_still_satisfies_all_clauses(self):
        result = self.run_cnf('4 3\n1 0\n-1 2 0\n3 0\n')
        self.assertEqual(result.returncode, 0, result.stderr[:200])
        bits = result.stdout.strip()
        self.assertEqual(len(bits), 4)
        self.assertTrue(set(bits) <= {'0', '1'})
        clauses = [[1], [-1, 2], [3]]
        self.assertTrue(all(any((bits[abs(lit) - 1] == '1') == (lit > 0)
                                for lit in clause) for clause in clauses))

    def test_unterminated_clause_is_rejected(self):
        result = self.run_cnf('1 1\n1\n')
        self.assertEqual(result.returncode, 2, result.stdout[:200] + result.stderr[:200])
        self.assertIn('terminator', result.stderr.lower())

    def test_empty_clause_is_rejected_before_local_search(self):
        result = self.run_cnf('1 1\n0\n')
        self.assertEqual(result.returncode, 2, result.stdout[:200] + result.stderr[:200])
        self.assertIn('empty clause', result.stderr.lower())

    def test_invalid_dimensions_are_rejected(self):
        for header in ['-1 1', '1 -1', '1000001 1']:
            with self.subTest(header=header):
                result = self.run_cnf(header + '\n1 0\n')
                self.assertEqual(result.returncode, 2, result.stdout[:200] + result.stderr[:200])
                self.assertIn('dimensions', result.stderr.lower())

    def test_out_of_range_literal_is_rejected(self):
        result = self.run_cnf('1 1\n2 0\n')
        self.assertEqual(result.returncode, 2, result.stdout[:200] + result.stderr[:200])
        self.assertIn('literal range', result.stderr.lower())

    def test_wide_clause_is_rejected_before_fixed_array_write(self):
        result = self.run_cnf('1 1\n' + '1 ' * 17 + '0\n')
        self.assertEqual(result.returncode, 2, result.stdout[:200] + result.stderr[:200])
        self.assertIn('clause width', result.stderr.lower())


class SuppliedProvenanceTests(unittest.TestCase):
    def test_all_original_files_match_the_recorded_archive_members(self):
        if not ORIGINAL.exists():
            self.skipTest('operator-private uploaded source is not part of public distribution')
        manifest = json.loads((SKILL / 'assets' / 'provenance.json').read_text())
        count = 0
        for archive in manifest['archives']:
            for name, digest in archive['files'].items():
                path = SKILL / 'assets' / 'supplied' / archive['name'] / name
                self.assertTrue(path.is_file(), str(path))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
                count += 1
        self.assertEqual(count, 13)

    def test_original_and_adapted_custom_worker_are_distinct(self):
        if not ORIGINAL.exists() or not ADAPTED.exists():
            self.skipTest('operator-private uploaded source is not part of public distribution')
        self.assertNotEqual(ADAPTED.read_bytes(), ORIGINAL.read_bytes())
        self.assertIn('maximum 16', ADAPTED.read_text())
        self.assertIn('literal range', ADAPTED.read_text())


if __name__ == '__main__':
    unittest.main()
