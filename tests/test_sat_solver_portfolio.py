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


def resource_check_script(marker, baseline_file):
    """Check the inherited cap against the VM measurement that actually set it."""
    return ('import json,os,sys\nfrom pathlib import Path\n'
            'if os.name == "posix" or sys.platform == "darwin":\n'
            ' import resource\n'
            ' a,hard=resource.getrlimit(resource.RLIMIT_DATA)\n'
            ' f,fhard=resource.getrlimit(resource.RLIMIT_FSIZE)\n'
            ' context={"pid":os.getpid(),"data":(a,hard),"fsize":(f,fhard)}\n'
            ' if sys.platform == "darwin":\n'
            '  original=json.loads(Path(' + repr(str(baseline_file)) + ').read_text())\n'
            '  context["preexec"]=original\n'
            '  print(context, file=sys.stderr, flush=True)\n'
            '  assert original["pid"]==os.getpid(), context\n'
            '  assert a>2*1024**3 and 0<a-original["baseline"]<=2*1024**3, context\n'
            ' else:\n'
            '  assert 0<a<=2*1024**3, context\n'
            ' assert a==hard and 0<f<=16*1024**2 and f==fhard, context\n'
            'Path(' + repr(str(marker)) + ').write_text("ok")\n')


def python_workers(root, baseline_file=None):
    """Execute fixture scripts with Python; keep the backend's argument list."""
    popen = subprocess.Popen
    def launch(argv, *args, **kwargs):
        if Path(argv[0]).parent == root:
            argv = [sys.executable, *argv]
        elif len(argv) > 4 and argv[1] == '-c' and Path(argv[4]).parent == root:
            if baseline_file is not None:
                # Observe the exact real probe result used by child_limits, before
                # the same PID execs the fixture. No production environment hook.
                setup = ('import portfolio,json; probe=portfolio._darwin_virtual_size; '
                         'portfolio._darwin_virtual_size=lambda: '
                         f'(open({str(baseline_file)!r}, "w").write('
                         'json.dumps({"pid":os.getpid(),"baseline":(b:=probe())})),b)[1]; ')
                wrapper = argv[2].replace('from portfolio import child_limits;',
                                          setup + 'from portfolio import child_limits;')
                if wrapper == argv[2]:
                    raise AssertionError('Darwin limit wrapper instrumentation did not match')
                argv = [*argv[:2], wrapper, *argv[3:]]
            argv = [*argv[:4], sys.executable, *argv[4:]]
        return popen(argv, *args, **kwargs)
    return patch.object(subprocess, 'Popen', side_effect=launch)


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
            if os.name == 'posix':
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
            with python_workers(root), patch.object(p, 'SOLVERS', ('z3', 'kissat')), patch.object(
                    p, 'binary', side_effect=lambda name: str(slow) if name == 'z3' else kissat):
                answer = p.solve(source, timeout=.6)
            self.assertEqual(answer['status'], 'SAT', answer)
            self.assertEqual(answer['solver'], 'kissat')
            self.assertTrue(answer['verified'])
            self.assertEqual(answer['attempts'][0]['status'], 'TIMEOUT')

    def test_failure_fixtures_and_total_timeout(self):
        p = importlib.import_module('portfolio')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'x.json'
            source.write_text('[[1]]')
            worker_patch = python_workers(root)
            worker_patch.start()
            self.addCleanup(worker_patch.stop)
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
            worker_patch = python_workers(Path(d))
            worker_patch.start()
            self.addCleanup(worker_patch.stop)
            source.write_text('{"clauses": [[1, 1], [2, -2]], "num_vars": 3}')
            with patch.object(p, 'binary', return_value=str(fixture)):
                self.assertEqual(p.solve(source, solver='probsat')['status'], 'UNKNOWN')
            # MiniSat writes its model to argv[2], never a stale shared filename.
            fixture.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\nassert len(sys.argv)==3\nassert not Path(sys.argv[2]).exists()\nraise SystemExit(1)\n')
            with patch.object(p, 'binary', return_value=str(fixture)):
                r = p.solve(source, solver='minisat')
                self.assertEqual(r['status'], 'ERROR')
                self.assertEqual(r['error'], 'solver exit code 1')

    def test_resource_fixture_uses_preexec_baseline_not_fresh_vm(self):
        # A valid cap can exceed fresh-VM + 2 GiB after exec replaces mappings.
        from types import SimpleNamespace
        gib = 1024 ** 3
        with tempfile.TemporaryDirectory() as d:
            marker, baseline_file = Path(d) / 'limited', Path(d) / 'baseline'
            baseline_file.write_text(json.dumps({'pid': os.getpid(), 'baseline': 400 * gib}))
            resource = SimpleNamespace(RLIMIT_DATA=1, RLIMIT_FSIZE=2,
                                       getrlimit=lambda kind: ((402 * gib,) * 2 if kind == 1
                                                               else (16 * 1024 ** 2,) * 2))
            p = importlib.import_module('portfolio')
            with patch.object(sys, 'platform', 'darwin'), patch.dict(
                    sys.modules, {'resource': resource}), patch.object(
                    p, '_darwin_virtual_size', return_value=399 * gib) as fresh_probe:
                exec(resource_check_script(marker, baseline_file), {})
            fresh_probe.assert_not_called()
            self.assertTrue(marker.exists())

    def test_resource_fixture_rejects_unbounded_or_wrong_child_limits(self):
        from types import SimpleNamespace
        gib = 1024 ** 3
        with tempfile.TemporaryDirectory() as d:
            marker, baseline_file = Path(d) / 'limited', Path(d) / 'baseline'
            for data, fsize, pid in [(403 * gib, 16 * 1024 ** 2, os.getpid()),
                                     (-1, 16 * 1024 ** 2, os.getpid()),
                                     (402 * gib, 17 * 1024 ** 2, os.getpid()),
                                     (402 * gib, 16 * 1024 ** 2, -1)]:
                baseline_file.write_text(json.dumps({'pid': pid, 'baseline': 400 * gib}))
                resource = SimpleNamespace(RLIMIT_DATA=1, RLIMIT_FSIZE=2,
                                           getrlimit=lambda kind: (data, data) if kind == 1
                                           else (fsize, fsize))
                with self.subTest(data=data, fsize=fsize, pid=pid), patch.object(
                        sys, 'platform', 'darwin'), patch.dict(sys.modules, {'resource': resource}):
                    with self.assertRaises(AssertionError):
                        exec(resource_check_script(marker, baseline_file), {})
                    self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX exec/resource-limit acceptance')
    def test_resource_baseline_recorder_survives_same_pid_exec(self):
        # Exercise the test-only recorder and python_workers argument rewriting
        # even on Linux. Native Darwin alone validates the real libproc baseline.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            marker, baseline_file = root / 'limited', root / 'baseline'
            fixture = root / 'check'
            fixture.write_text(resource_check_script(marker, baseline_file))
            wrapper = ('import os,sys; sys.path.insert(0,sys.argv.pop(1)); '
                       'import portfolio; portfolio.sys.platform="darwin"; '
                       + ('portfolio._darwin_virtual_size=lambda: 0; '
                          if sys.platform != 'darwin' else '')
                       + 'from portfolio import child_limits; child_limits(); '
                       'os.execv(sys.argv[1],sys.argv[1:])')
            with python_workers(root, baseline_file):
                child = subprocess.Popen([sys.executable, '-c', wrapper, str(SCRIPTS), str(fixture)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, (stdout, stderr))
            recorded = json.loads(baseline_file.read_text())
            self.assertEqual(recorded['pid'], child.pid)
            if sys.platform == 'darwin':
                self.assertGreater(recorded['baseline'], 0)
            else:
                self.assertEqual(recorded['baseline'], 0)
            self.assertTrue(marker.exists())

    def test_child_resource_limits_and_cleanup(self):
        p = importlib.import_module('portfolio')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'x.json'
            source.write_text('[[1]]')
            fixture = root / 'check'
            marker = root / 'limited'
            baseline_file = root / 'preexec-baseline'
            fixture.write_text(resource_check_script(marker, baseline_file) + 'raise SystemExit(1)\n')
            fixture.chmod(0o700)
            worker_patch = python_workers(root, baseline_file)
            worker_patch.start()
            self.addCleanup(worker_patch.stop)
            # parse_output sees combined child stdout/stderr, before the temporary
            # backend directory is removed; solve's ERROR alone loses traceback.
            with patch.object(p, 'binary', return_value=str(fixture)), patch.object(
                    p, 'parse_output', wraps=p.parse_output) as parsed:
                answer = p.solve(source, solver='z3')
            captured = [call.args[0] for call in parsed.call_args_list]
            self.assertTrue(marker.exists(),
                            f'child resource limits not applied: {answer}; child output={captured}')
            self.assertEqual(answer['status'], 'ERROR', answer)
            pidfile = root / 'pid'
            fixture.write_text('import os,time\nfrom pathlib import Path\npid=os.fork() if os.name == "posix" else os.getpid()\nif pid==0:\n time.sleep(5)\nelse:\n Path(' + repr(str(pidfile)) + ').write_text(str(pid))\n time.sleep(5)\n')
            with patch.object(p, 'binary', return_value=str(fixture)):
                self.assertEqual(p.solve(source, solver='z3', timeout=.3)['status'], 'TIMEOUT')
            self.assertTrue(pidfile.exists())
            pid = int(pidfile.read_text())
            if os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.OpenProcess(0x1000, False, pid)
                if handle:
                    try:
                        code = wintypes.DWORD()
                        self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                        self.assertNotEqual(code.value, 259, 'timed-out worker is still active')
                    finally:
                        kernel.CloseHandle(handle)
                else:
                    self.assertEqual(ctypes.get_last_error(), 87, 'cannot inspect timed-out worker')
                return
            # A killed orphan may briefly remain as a zombie; it cannot execute.
            proc = Path('/proc') / str(pid) / 'stat'
            end = time.monotonic() + 1
            def state():
                try:
                    return proc.read_text().split()[2]
                except (FileNotFoundError, ProcessLookupError):
                    return None
            while state() not in (None, 'Z') and time.monotonic() < end:
                time.sleep(.01)
            self.assertIn(state(), (None, 'Z'))


if __name__ == '__main__':
    unittest.main()
