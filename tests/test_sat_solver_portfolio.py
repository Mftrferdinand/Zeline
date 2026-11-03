"""Offline SAT portfolio contract tests (stdlib only)."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'zeline/skills/zeline-sat-solvers/scripts'
sys.path.insert(0, str(SCRIPTS))
HAS_Z3 = importlib.import_module('portfolio').binary('z3') is not None


class PortfolioTests(unittest.TestCase):
    def common(self):
        self.assertTrue((SCRIPTS / 'solver_common.py').exists(), 'shared parser is not implemented')
        return importlib.import_module('solver_common')

    def test_strict_input_and_original_verification(self):
        c = self.common()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'input.json'
            def load(value, vars=None):
                p.write_text(json.dumps(value))
                return c.load_instance(p, vars)
            f = load({'clauses': [[1, 1], [2, -2]], 'num_vars': 4})
            self.assertEqual(f.num_vars, 4)
            self.assertEqual(f.clauses, [[1]])
            self.assertEqual(f.original, [[1, 1], [2, -2]])
            self.assertTrue(c.verify(f, '1000'))
            self.assertFalse(c.verify(f, '0000'))
            for value in ([[True]], [[0]], [[1.0]], {'clauses': [[2]], 'num_vars': 1}, {'clauses': [], 'num_vars': True}):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    load(value)
            self.assertEqual(load([]).num_vars, 0)
            self.assertEqual(load([[]]).clauses, [[]])
            p.write_text('c comment\np cnf 4 2\n1 1 0\n2 -2 0\n')
            self.assertEqual(c.load_instance(p).num_vars, 4)
            for bad in ('p cnf 1 2\n1 0\n', 'p cnf 1 1\n2 0\n', 'p cnf 1 1\n1\n', '1 0\n', 'p cnf 1 0\np cnf 1 0\n'):
                p.write_text(bad)
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    c.load_instance(p)

    def test_dimacs_limits_stop_before_parsing_unbounded_tail(self):
        common = self.common()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.cnf'
            path.write_text('p cnf 1 1\n1 0\n1 0\ninvalid-tail\n')
            with self.assertRaisesRegex(ValueError, 'clause count'):
                common.load_instance(path)
            path.write_text('p cnf 1 1\n1 1 0\ninvalid-tail\n')
            with patch.object(common, 'MAX_LITERALS', 1), self.assertRaisesRegex(ValueError, 'literal limit'):
                common.load_instance(path)

    def test_json_decoder_recursion_returns_structured_error_not_traceback(self):
        portfolio = importlib.import_module('portfolio')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text('[[1]]')
            # Python builds have different C-decoder depth limits. Exercise the
            # actual decoder failure contract without depending on its threshold.
            with patch.object(self.common().json, 'loads', side_effect=RecursionError('fixture nesting limit')):
                answer = portfolio.solve(path)
            self.assertEqual(answer['status'], 'ERROR')
            self.assertFalse(answer['verified'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX resource contract')
    def test_child_limits_never_raise_existing_stricter_soft_limits(self):
        import resource
        portfolio = importlib.import_module('portfolio')
        with patch.object(resource, 'getrlimit', return_value=(32, 256)), patch.object(
                resource, 'setrlimit') as setter:
            portfolio.child_limits()
        for call in setter.call_args_list:
            self.assertLessEqual(call.args[1][0], 32)
            self.assertEqual(call.args[1][0], call.args[1][1])

    @unittest.skipUnless(os.name == 'posix', 'POSIX resource contract')
    def test_darwin_bounds_growth_after_verified_startup_vm_reservation(self):
        import resource
        portfolio = importlib.import_module('portfolio')
        with patch.object(portfolio.sys, 'platform', 'darwin'), patch.object(
                importlib.import_module('solver_worker'), '_darwin_virtual_size', return_value=4096), patch.object(
                resource, 'getrlimit', return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY)), patch.object(
                resource, 'setrlimit') as setter:
            portfolio.child_limits()
        applied = {call.args[0]: call.args[1] for call in setter.call_args_list}
        self.assertEqual(applied[resource.RLIMIT_DATA], (4096 + 2 * 1024 ** 3,) * 2)
        self.assertEqual(applied[resource.RLIMIT_FSIZE], (portfolio.MAX_OUTPUT_BYTES,) * 2)
        self.assertEqual(applied[resource.RLIMIT_CORE], (0, 0))

    @unittest.skipUnless(os.name == 'posix', 'POSIX executable fixture')
    def test_invalid_executable_is_structured_error(self):
        portfolio = importlib.import_module('portfolio')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'x.json'
            source.write_text('[[1]]')
            bad = root / 'bad-executable'
            bad.write_text('not an executable image')
            bad.chmod(0o700)
            with patch.object(portfolio, 'binary', return_value=str(bad)):
                answer = portfolio.solve(source, solver='kissat')
            self.assertEqual(answer['status'], 'ERROR', answer)
            self.assertNotIn('Traceback', str(answer))

    def test_spawn_setup_failure_is_structured(self):
        portfolio = importlib.import_module('portfolio')
        common = self.common()
        with patch.object(portfolio.subprocess, 'Popen', side_effect=subprocess.SubprocessError('fixture setup')):
            answer = portfolio.run_backend(common.Instance(1, [[1]], [[1]]), 'kissat', sys.executable, 1)
        self.assertEqual(answer['status'], 'ERROR')
        self.assertFalse(answer['verified'])
        self.assertIn('setup', answer['error'])

    @unittest.skipUnless(HAS_Z3, 'optional real Z3 executable is not installed')
    def test_real_z3_and_json_cli(self):
        self.assertTrue((SCRIPTS / 'portfolio.py').exists(), 'portfolio is not implemented')
        p = importlib.import_module('portfolio')
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'x.json'
            f.write_text('{"clauses": [[1], [-1, 2]], "num_vars": 4}')
            result = p.solve(f, solver='z3', timeout=5)
            self.assertEqual(result['status'], 'SAT', result)
            self.assertTrue(result['verified'])
            self.assertEqual(result['assignment'][:2], '11')
            self.assertEqual(len(result['assignment']), 4)
            f.write_text('[[1], [-1]]')
            result = p.solve(f, solver='z3', timeout=5)
            self.assertEqual(result['status'], 'UNSAT', result)
            self.assertFalse(result['verified'])
            self.assertNotIn('assignment', result)
            result = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio.py'), 'solve', str(f), '--solver', 'z3'], text=True, stdout=subprocess.PIPE, timeout=10)
            self.assertEqual(json.loads(result.stdout)['status'], 'UNSAT')

    @unittest.skipUnless(HAS_Z3, 'optional real Z3 executable is not installed')
    def test_output_is_verified_bitstring_and_private(self):
        p = importlib.import_module('portfolio')
        with tempfile.TemporaryDirectory() as d:
            source, output = Path(d) / 'input.json', Path(d) / 'bits.txt'
            source.write_text('{"clauses": [[1]], "num_vars": 3}')
            answer = p.solve(source, solver='z3', out=output)
            self.assertEqual(answer['status'], 'SAT', answer)
            self.assertEqual(output.read_text().strip(), answer['assignment'])
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            source.write_text('[[1], [-1]]')
            failure_out = Path(d) / 'unsat.txt'
            self.assertEqual(p.solve(source, solver='z3', out=failure_out)['status'], 'UNSAT')
            self.assertFalse(failure_out.exists())

    @unittest.skipUnless(HAS_Z3, 'optional real Z3 executable is not installed')
    def test_safe_outputs_and_all_direct_adapters(self):
        p = importlib.import_module('portfolio')
        with tempfile.TemporaryDirectory() as d:
            source, output = Path(d) / 'x.json', Path(d) / 'result.json'
            source.write_text('[[1]]')
            r = p.solve(source, solver='z3', out=output)
            self.assertTrue(output.exists(), 'output is not written')
            self.assertEqual(output.read_text().strip(), r['assignment'])
            original = source.read_bytes()
            self.assertEqual(p.solve(source, solver='z3', out=source)['status'], 'ERROR')
            self.assertEqual(source.read_bytes(), original)
            output.write_text('old')
            self.assertEqual(p.solve(source, solver='z3', out=output)['status'], 'ERROR')
            self.assertEqual(output.read_text(), 'old')
            for name in p.SOLVERS:
                adapter = SCRIPTS / (name + '_solver.py')
                self.assertTrue(adapter.exists(), name)
                r = subprocess.run([sys.executable, str(adapter), '--help'], stdout=subprocess.PIPE, timeout=5)
                self.assertEqual(r.returncode, 0)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO fixture')
    def test_nonregular_input_cannot_block_the_solver_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            fifo = Path(d) / 'input.fifo'
            os.mkfifo(fifo)
            try:
                child = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio.py'),
                                        'solve', str(fifo), '--timeout', '.1'],
                                       capture_output=True, text=True, timeout=.8)
            except subprocess.TimeoutExpired:
                self.fail('solver blocked reading a nonregular input')
            self.assertEqual(json.loads(child.stdout)['status'], 'ERROR')

    def test_cli_errors_have_nonzero_exit_and_no_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            source, out = Path(d) / 'bad.json', Path(d) / 'assignment.txt'
            source.write_text('[[true]]')
            child = subprocess.run([sys.executable, str(SCRIPTS / 'portfolio.py'), 'solve',
                                    str(source), '--out', str(out)],
                                   text=True, capture_output=True, timeout=3)
            self.assertNotEqual(child.returncode, 0)
            self.assertEqual(json.loads(child.stdout)['status'], 'ERROR')
            self.assertFalse(out.exists())

    def test_trivial_formulas_are_decided_without_a_solver_dependency(self):
        p = importlib.import_module('portfolio')
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input.json'
            for value, status, bits in [([], 'SAT', ''), ([[]], 'UNSAT', None),
                    ({'num_vars': 4, 'clauses': [[1, -1], [2, -2]]}, 'SAT', '0000')]:
                source.write_text(json.dumps(value))
                with patch.object(p, 'binary', return_value=None), patch.object(p, 'run_backend') as run:
                    answer = p.solve(source, solver='probsat')
                self.assertEqual(answer['status'], status, answer)
                if bits is not None:
                    self.assertEqual(answer['assignment'], bits)
                    self.assertTrue(answer['verified'])
                run.assert_not_called()

    @unittest.skipUnless(HAS_Z3, 'optional real Z3 executable is not installed')
    def test_late_verified_model_is_not_written_as_a_timely_result(self):
        p = importlib.import_module('portfolio')
        run = p.run_backend
        with tempfile.TemporaryDirectory() as d:
            source, out = Path(d) / 'input.json', Path(d) / 'bits.txt'
            source.write_text('[[1]]')
            def delayed(*args):
                answer = run(*args)
                time.sleep(.2)
                return answer
            with patch.object(p, 'run_backend', side_effect=delayed):
                answer = p.solve(source, solver='z3', timeout=.12, out=out)
            self.assertEqual(answer['status'], 'TIMEOUT', answer)
            self.assertNotIn('assignment', answer)
            self.assertFalse(out.exists())

    def test_parsing_time_counts_against_the_total_deadline(self):
        p = importlib.import_module('portfolio')
        load = p.load_instance
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input.json'
            source.write_text('[[1]]')
            def delayed(*args):
                time.sleep(.06)
                return load(*args)
            with patch.object(p, 'load_instance', side_effect=delayed), patch.object(
                    p, 'run_backend', wraps=p.run_backend) as run:
                answer = p.solve(source, solver='z3', timeout=.02)
            self.assertEqual(answer['status'], 'TIMEOUT', answer)
            run.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'POSIX shebang executable fixture')
    def test_auto_reserves_time_for_a_real_fallback_solver(self):
        p = importlib.import_module('portfolio')
        kissat = p.binary('kissat')
        if kissat is None:
            self.skipTest('real Kissat is not installed')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'input.json'
            source.write_text('[[1], [-1, 2]]')
            slow = root / 'slow'
            slow.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(5)\n')
            slow.chmod(0o700)
            with patch.object(p, 'SOLVERS', ('z3', 'kissat')), patch.object(
                    p, 'binary', side_effect=lambda name: str(slow) if name == 'z3' else kissat):
                answer = p.solve(source, timeout=.6)
            self.assertEqual(answer['status'], 'SAT', answer)
            self.assertEqual(answer['solver'], 'kissat')
            self.assertTrue(answer['verified'])
            self.assertEqual(answer['attempts'][0]['status'], 'TIMEOUT')

    @unittest.skipUnless(os.name == 'posix', 'POSIX shebang executable fixture')
    def test_failure_fixtures_and_total_timeout(self):
        p = importlib.import_module('portfolio')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'x.json'
            source.write_text('[[1]]')
            def fake(name, body):
                f = root / name
                f.write_text('#!' + sys.executable + '\n' + body)
                f.chmod(0o700)
                return str(f)
            bad = fake('bad', "print('s SATISFIABLE'); print('v -1 0')\n")
            with patch.object(p, 'binary', return_value=bad):
                r = p.solve(source, solver='z3')
                self.assertEqual(r['status'], 'ERROR')
                self.assertNotIn('assignment', r)
            slow = fake('slow', 'import time\ntime.sleep(5)\n')
            start = time.monotonic()
            with patch.object(p, 'binary', return_value=slow):
                self.assertEqual(p.solve(source, timeout=.1)['status'], 'TIMEOUT')
            self.assertLess(time.monotonic() - start, 1)
            with patch.object(p, 'binary', return_value=None):
                self.assertEqual(p.solve(source)['status'], 'UNAVAILABLE')
                self.assertFalse(any(x['available'] for x in p.inspect().values()))
            unsat = fake('unsat', "print('s UNSATISFIABLE')\n")
            with patch.object(p, 'binary', return_value=unsat):
                self.assertEqual(p.solve(source, solver='yalsat')['status'], 'UNKNOWN')
            crashed = fake('crashed', "print('s SATISFIABLE'); print('v 1 0'); raise SystemExit(1)\n")
            with patch.object(p, 'binary', return_value=crashed):
                self.assertEqual(p.solve(source, solver='z3')['status'], 'ERROR')
            huge = fake('huge', "print('x'*10000)\n")
            with patch.object(p, 'binary', return_value=huge), patch.object(p, 'MAX_OUTPUT_BYTES', 100):
                self.assertEqual(p.solve(source, solver='z3')['status'], 'ERROR')

    @unittest.skipUnless(os.name == 'posix', 'POSIX shebang executable fixture')
    def test_backend_protocols_and_local_search_guards(self):
        p = importlib.import_module('portfolio')
        c = self.common()
        formula = c.Instance(3, [[1]], [[1]])
        r = p.parse_output('100\nSAT flips=42\n', formula, 'probsat', 0)
        self.assertEqual(r['status'], 'SAT')
        self.assertTrue(r['verified'])
        self.assertEqual(p.parse_output('', formula, 'probsat', 1)['status'], 'UNKNOWN')
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'x.json'
            source.write_text(json.dumps([list(range(1, 18))]))
            with patch.object(p, 'binary', return_value=sys.executable):
                r = p.solve(source, solver='probsat')
            self.assertEqual(r['status'], 'ERROR')
            self.assertIn('16', r['error'])
            source.write_text('[[]]')
            with patch.object(p, 'binary', return_value=sys.executable):
                self.assertEqual(p.solve(source, solver='probsat')['status'], 'UNSAT')
            # Fixture only validates command/input and returns failure, never a fake solution.
            fixture = Path(d) / 'protocol'
            fixture.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\nassert len(sys.argv)==5\nassert sys.argv[2]=="1"\nassert sys.argv[3]=="2.3"\nassert float(sys.argv[4])>0\nassert Path(sys.argv[1]).read_text().startswith("3 1\\n1 0\\n")\nraise SystemExit(1)\n')
            fixture.chmod(0o700)
            source.write_text('{"clauses": [[1, 1], [2, -2]], "num_vars": 3}')
            with patch.object(p, 'binary', return_value=str(fixture)):
                self.assertEqual(p.solve(source, solver='probsat')['status'], 'UNKNOWN')
            # MiniSat writes its model to argv[2], never a stale shared filename.
            fixture.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\nassert len(sys.argv)==3\nassert not Path(sys.argv[2]).exists()\nraise SystemExit(1)\n')
            with patch.object(p, 'binary', return_value=str(fixture)):
                r = p.solve(source, solver='minisat')
                self.assertEqual(r['status'], 'ERROR')
                self.assertEqual(r['error'], 'solver exit code 1')

    @unittest.skipUnless(os.name == 'posix', 'POSIX shebang executable fixture')
    def test_child_resource_limits_and_cleanup(self):
        p = importlib.import_module('portfolio')
        # Measure the actual kernel cap against the SAME pre-exec process VM.
        # ASLR/allocator reservations differ after exec; comparing two process
        # baselines is not a valid upper-bound assertion (and was flaky on macOS).
        probe = """
import sys, json, resource
sys.path.insert(0, sys.argv[1])
import solver_worker as worker
inherited = resource.getrlimit(resource.RLIMIT_DATA)
observed = []
original = worker._darwin_virtual_size
def measure():
    size = original()
    observed.append(size)
    return size
worker._darwin_virtual_size = measure
worker.child_limits()
requested = 2 * 1024**3 + (observed[0] if sys.platform == 'darwin' else 0)
ceilings = [value for value in inherited if value != resource.RLIM_INFINITY]
expected = min([requested, *ceilings])
assert resource.getrlimit(resource.RLIMIT_DATA) == (expected, expected)
assert resource.getrlimit(resource.RLIMIT_FSIZE) == (16*1024**2, 16*1024**2)
assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
print(json.dumps({'enforced_data': expected, 'startup_vm': observed}))
"""
        import subprocess
        enforced = subprocess.run([sys.executable, '-c', probe, str(SCRIPTS)],
                                  capture_output=True, text=True, timeout=10)
        self.assertEqual(enforced.returncode, 0, enforced.stderr)
        self.assertGreater(json.loads(enforced.stdout)['enforced_data'], 0)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'x.json'
            source.write_text('[[1]]')
            fixture = root / 'check'
            marker = root / 'limited'
            body = 'import resource\nfrom pathlib import Path\na=resource.getrlimit(resource.RLIMIT_DATA)[0]\nf=resource.getrlimit(resource.RLIMIT_FSIZE)[0]\nassert a>0 and a!=resource.RLIM_INFINITY\nassert 0<f<=16*1024**2\nassert resource.getrlimit(resource.RLIMIT_CORE)==(0,0)\n'
            fixture.write_text('#!' + sys.executable + '\n' + body + 'Path(' + repr(str(marker)) + ').write_text("ok")\nraise SystemExit(1)\n')
            fixture.chmod(0o700)
            with patch.object(p, 'binary', return_value=str(fixture)):
                p.solve(source, solver='z3')
            self.assertTrue(marker.exists(), 'child resource limits not applied')
            pidfile = root / 'pid'
            fixture.write_text('#!' + sys.executable + '\nimport os,time\nfrom pathlib import Path\npid=os.fork()\nif pid==0:\n time.sleep(5)\nelse:\n Path(' + repr(str(pidfile)) + ').write_text(str(pid))\n time.sleep(5)\n')
            with patch.object(p, 'binary', return_value=str(fixture)):
                self.assertEqual(p.solve(source, solver='z3', timeout=.3)['status'], 'TIMEOUT')
            self.assertTrue(pidfile.exists())
            pid = int(pidfile.read_text())
            # A killed orphan may briefly remain as a zombie; it cannot execute.
            proc = Path('/proc') / str(pid) / 'stat'
            end = time.monotonic() + 1
            while proc.exists() and proc.read_text().split()[2] != 'Z' and time.monotonic() < end:
                time.sleep(.01)
            if proc.exists():
                self.assertEqual(proc.read_text().split()[2], 'Z')


if __name__ == '__main__':
    unittest.main()
