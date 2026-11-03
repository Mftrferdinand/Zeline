"""Extended engine registration and strict native-protocol contracts."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'zeline/skills/zeline-sat-solvers/scripts'
sys.path.insert(0, str(SCRIPTS))
p = importlib.import_module('portfolio')
c = importlib.import_module('solver_common')


class ExtendedSolverTests(unittest.TestCase):
    def test_extra_theory_constraints_cannot_be_silently_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'compound.json'
            for field in ['xor', 'xors', 'xor_clauses', 'smt2', 'assertions', 'constraints']:
                with self.subTest(field=field):
                    source.write_text(json.dumps({'clauses': [[1]], field: [[-1]]}))
                    with self.assertRaisesRegex(ValueError, 'unsupported.*constraints'):
                        c.load_instance(source)
                    self.assertEqual(p.solve(source, solver='cryptominisat', timeout=1)['status'], 'ERROR')

    @unittest.skipUnless(os.name == 'posix', 'POSIX executable fixture')
    def test_glucose_file_with_only_literals_keeps_stdout_status(self):
        # Explicit fixture for a protocol observed from the real Glucose binary, not a solver benchmark.
        formula = c.Instance(4, [[1], [-1, 2], [3]], [[1], [-1, 2], [3]])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            worker = root / 'glucose'
            worker.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\n'
                              "Path(sys.argv[2]).write_text('1 2 3 -4 0\\n')\n"
                              "print('s SATISFIABLE')\nraise SystemExit(10)\n")
            worker.chmod(0o700)
            with patch.dict(os.environ, {'ZELINE_SAT_BIN_DIR': str(root)}):
                answer = p.run_backend(formula, 'glucose', str(worker), 2)
            self.assertEqual(answer['status'], 'SAT', answer)
            self.assertEqual(answer['assignment'], '1110')
            self.assertTrue(answer['verified'])

    def test_bitwuzla_models_exhaustively_match_small_truth_tables(self):
        import itertools
        formulas = [[[1], [-1, 2], [3]], [[1, -2], [-1, 2], [2, 3]],
                    [[1, 2, 3], [-1, -2, -3]]]
        for clauses in formulas:
            formula = c.Instance(3, clauses, clauses)
            for bits in itertools.product([False, True], repeat=3):
                expected = all(any(bits[abs(lit) - 1] == (lit > 0) for lit in clause)
                               for clause in clauses)
                text = 'sat\n(\n' + ''.join(f'(define-fun v{i+1} () Bool {str(bit).lower()})\n'
                                              for i, bit in enumerate(bits)) + ')'
                answer = p.parse_output(text, formula, 'bitwuzla', 0)
                with self.subTest(clauses=clauses, bits=bits):
                    self.assertEqual(answer['status'] == 'SAT', expected)
                    self.assertEqual(answer['verified'], expected)

    def test_packaging_guard_imports_without_python311_tomllib(self):
        import runpy
        with patch.dict(sys.modules, {'tomllib': None}):
            try:
                runpy.run_path(str(SCRIPTS.parents[3] / 'tests/test_sat_public_packaging.py'),
                               run_name='packaging_guard_compat')
            except ImportError:
                self.fail('packaging tests require tomllib, unavailable on supported Python 3.10')

    def test_available_extended_engines_really_solve_and_verify(self):
        names = [name for name in ['probsat_official', 'cryptominisat', 'bitwuzla', 'glucose', 'minisat']
                 if p.binary(name)]
        if not names:
            self.skipTest('optional extended executables are not installed')
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input.json'
            for name in names:
                with self.subTest(solver=name):
                    source.write_text(json.dumps({'num_vars': 4, 'clauses': [[1], [-1, 2], [3]]}))
                    answer = p.solve(source, solver=name, timeout=3)
                    self.assertEqual(answer['status'], 'SAT', answer)
                    self.assertTrue(answer['verified'])
                    bits = answer['assignment']
                    self.assertEqual(len(bits), 4)
                    self.assertTrue(all(any((bits[abs(lit)-1]=='1')==(lit>0) for lit in clause)
                                        for clause in [[1], [-1, 2], [3]]))
                    if name not in p.LOCAL:
                        source.write_text('[[1], [-1]]')
                        self.assertEqual(p.solve(source, solver=name, timeout=3)['status'], 'UNSAT')
            # Also exercise the actual named CLI, not just the imported helper.
            source.write_text('[[1],[-1,2]]')
            child = subprocess.run([sys.executable, str(SCRIPTS / (names[0]+'_solver.py')),
                                    str(source), '--timeout', '3'],
                                   capture_output=True, text=True, timeout=5)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertTrue(json.loads(child.stdout)['verified'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX shebang executable fixture')
    def test_official_probat_and_crypto_cli_are_not_custom_probasab(self):
        formula = c.Instance(3, [[1]], [[1]])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ['probsat_official', 'cryptominisat']:
                marker = root / name
                worker = root / ('check-' + name)
                check = ('assert sys.argv[1]=="-a"\nassert sys.argv[-1]=="1"\n'
                         'assert Path(sys.argv[2]).read_text().startswith("p cnf 3 1\\n")\n'
                         if name == 'probsat_official' else
                         'assert "--threads=1" in sys.argv\nassert "--verb=0" in sys.argv\n'
                         'assert Path(sys.argv[-1]).read_text().startswith("p cnf 3 1\\n")\n')
                worker.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\n'
                                  + check + 'Path(' + repr(str(marker)) + ').write_text("ok")\nraise SystemExit(1)\n')
                worker.chmod(0o700)
                answer = p.run_backend(formula, name, str(worker), 2)
                self.assertTrue(marker.exists(), name)
                self.assertNotIn('assignment', answer)
                self.assertEqual(answer['status'], 'ERROR')
        self.assertEqual(p.parse_output('s UNSATISFIABLE\n', formula, 'probsat_official', 0)['status'], 'UNKNOWN')

    def test_bitwuzla_multiline_models_are_verified_not_trusted(self):
        formula = c.Instance(3, [[1], [-1, 2]], [[1], [-1, 2]])
        text = 'sat\n(\n (define-fun v1 () Bool\n true)\n (define-fun v2 () Bool true)\n)\n'
        answer = p.parse_output(text, formula, 'bitwuzla', 0)
        self.assertEqual(answer['status'], 'SAT', answer)
        self.assertEqual(answer['assignment'], '110')
        self.assertTrue(answer['verified'])
        for bad in [text.replace('v1 () Bool\n true', 'v1 () Bool\n false'),
                    text + '(define-fun v1 () Bool false)', text.replace('v1', 'v4'),
                    'sat\nbanana', text + 'unsat\n']:
            with self.subTest(bad=bad):
                self.assertEqual(p.parse_output(bad, formula, 'bitwuzla', 0)['status'], 'ERROR')
        self.assertEqual(p.parse_output('unsat\n', formula, 'bitwuzla', 0)['status'], 'UNSAT')
        self.assertEqual(p.parse_output('unknown\n', formula, 'bitwuzla', 0)['status'], 'UNKNOWN')
        self.assertEqual(p.parse_output(text, formula, 'bitwuzla', 1)['status'], 'ERROR')

    @unittest.skipUnless(os.name == 'posix', 'POSIX shebang executable fixture')
    def test_bitwuzla_receives_boolean_smt_not_dimacs(self):
        formula = c.Instance(3, [[1], [-1, 2]], [[1], [-1, 2]])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            marker = root / 'protocol-ok'
            worker = root / 'check'
            worker.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\n'
                'assert "--print-model" in sys.argv\n'
                'text=Path(sys.argv[-1]).read_text()\n'
                'assert "(set-logic QF_BV)" in text\n'
                'assert "(declare-fun v3 () Bool)" in text\n'
                'assert "(assert v1)" in text and "(or v1)" not in text\n'
                'assert "(assert (or (not v1) v2))" in text\n'
                'assert text.endswith("(check-sat)\\n")\n'
                'Path(' + repr(str(marker)) + ').write_text("ok")\nraise SystemExit(1)\n')
            worker.chmod(0o700)
            answer = p.run_backend(formula, 'bitwuzla', str(worker), 2)
            self.assertTrue(marker.exists(), 'Bitwuzla got the wrong input/CLI protocol')
            self.assertEqual(answer['status'], 'ERROR')

    def test_extended_backends_are_discoverable_with_real_binary_names(self):
        self.assertTrue({'cryptominisat', 'probsat_official', 'bitwuzla'} <= set(p.SOLVERS))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name in ['cryptominisat5', 'probSAT', 'bitwuzla']:
                binary = root / name
                binary.write_text('#!' + sys.executable + '\nraise SystemExit(1)\n')
                binary.chmod(0o700)
            with patch.dict(os.environ, {'ZELINE_SAT_BIN_DIR': str(root)}):
                for key, expected in [('cryptominisat', 'cryptominisat5'),
                                      ('probsat_official', 'probSAT'), ('bitwuzla', 'bitwuzla')]:
                    self.assertEqual(p.binary(key), str((root / expected).resolve()))
                self.assertFalse(p.inspect()['probsat_official']['complete'])


if __name__ == '__main__':
    unittest.main()
