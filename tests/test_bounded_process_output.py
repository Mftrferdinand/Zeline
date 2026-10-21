"""Small real subprocess regressions for bounded foreground capture."""
import importlib
import sys
import tempfile
import unittest
from unittest.mock import patch


class BoundedProcessOutputTests(unittest.TestCase):
    def setUp(self):
        self.tools = importlib.import_module('zeline.tools')

    def run_code(self, source, **kwargs):
        with tempfile.TemporaryDirectory() as cwd:
            return self.tools._run_tracked(
                [sys.executable, '-c', source], shell=False, cwd=cwd,
                seconds=3, identity='fixture:bounded', **kwargs)

    def test_foreground_retains_bounded_head_tail_without_communicate(self):
        with patch.object(self.tools.subprocess.Popen, 'communicate',
                          side_effect=AssertionError('full-output buffering forbidden')):
            code, output, timed_out = self.run_code(
                "import os;os.write(1,b'HEAD'+b'x'*60000+b'TAIL')")
        self.assertEqual(code, 0)
        self.assertFalse(timed_out)
        self.assertLessEqual(len(output.encode()), 50000)
        self.assertTrue(output.startswith('HEAD'))
        self.assertTrue(output.endswith('TAIL'))
        self.assertIn('TRUNCATED', output)

    def test_promotion_after_reader_exit_closes_log(self):
        import subprocess
        from zeline.process_output import ProcessOutput
        process = subprocess.Popen([sys.executable, '-c', "print('done')"],
                                   stdout=subprocess.PIPE, bufsize=0)
        capture = ProcessOutput(process)
        process.wait(timeout=2)
        self.assertTrue(capture.done.wait(1))
        with tempfile.TemporaryFile() as handle:
            capture.promote(handle)
            self.assertTrue(handle.closed)

    def test_thread_start_failure_closes_allocated_resources(self):
        from types import SimpleNamespace
        output = importlib.import_module('zeline.process_output')
        failures = ['start']
        if output.os.name != 'nt':
            failures.insert(0, 'set_blocking')
        for failure in failures:
            with self.subTest(failure=failure):
                stream = tempfile.TemporaryFile()
                spool = tempfile.TemporaryFile()
                process = SimpleNamespace(stdout=stream, pid=123)
                target = (patch.object(output.os, 'set_blocking', side_effect=OSError('setup failed'))
                          if failure == 'set_blocking' else
                          patch.object(output.threading.Thread, 'start', side_effect=RuntimeError('setup failed')))
                try:
                    with patch.object(output.tempfile, 'TemporaryFile', return_value=spool), target:
                        with self.assertRaisesRegex((OSError, RuntimeError), 'setup failed'):
                            output.ProcessOutput(process)
                    self.assertTrue(spool.closed)
                    self.assertTrue(stream.closed)
                finally:
                    spool.close()
                    stream.close()

    def test_drain_and_close_errors_remain_observable_and_always_signal_done(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        output = importlib.import_module('zeline.process_output')
        for failure in ('write', 'stdout_close', 'sink_close'):
            with self.subTest(failure=failure), tempfile.TemporaryFile() as stream:
                stream.write(b'payload')
                stream.seek(0)
                stdout = Mock(wraps=stream)
                stdout.fileno.return_value = stream.fileno()
                process = SimpleNamespace(stdout=stdout, pid=123, poll=lambda: 0)
                with patch.object(output.threading.Thread, 'start'):
                    capture = output.ProcessOutput(process)
                sink = Mock()
                try:
                    if failure == 'stdout_close':
                        stdout.close.side_effect = OSError('close failed')
                        capture.sink = sink
                    elif failure == 'sink_close':
                        capture.sink = sink
                        sink.close.side_effect = OSError('close failed')
                    if failure == 'write':
                        with patch.object(capture.spool, 'write', side_effect=OSError('write failed')):
                            capture._drain()
                    else:
                        capture._drain()
                    self.assertTrue(capture.done.is_set())
                    self.assertIsInstance(capture.error, OSError)
                    if failure != 'write':
                        sink.close.assert_called_once()
                    with self.assertRaisesRegex(OSError, 'capture failed'):
                        capture.render()
                    with tempfile.TemporaryFile() as handle:
                        with self.assertRaisesRegex(OSError, 'capture failed'):
                            capture.promote(handle)
                finally:
                    capture.spool.close()

    def test_small_output_exit_code_and_utf8_are_preserved(self):
        code, output, timed_out = self.run_code(
            "import os,sys;os.write(1,('x'*19999+'€'+'end').encode());sys.exit(7)")
        self.assertEqual((code, timed_out), (7, False))
        self.assertEqual(output, 'x'*19999+'€'+'end')

    def test_timeout_returns_partial_output_and_closes_reader(self):
        import threading
        import time
        from pathlib import Path
        real_popen = self.tools.subprocess.Popen
        started = None
        with tempfile.TemporaryDirectory() as cwd:
            ready = Path(cwd) / 'ready.signal'

            def spawn_ready(*args, **kwargs):
                nonlocal started
                process = real_popen(*args, **kwargs)
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists():
                        if process.poll() is not None or time.monotonic() >= deadline:
                            self.fail('fixture did not announce readiness')
                        time.sleep(.005)
                    # The timeout assertion measures execution after startup,
                    # not whether Python booted within a 150ms CPU timeslice.
                    started = time.monotonic()
                    return process
                except BaseException:
                    self.tools._terminate_group(process)
                    process.stdout.close()
                    raise

            with patch.object(self.tools.subprocess, 'Popen', side_effect=spawn_ready):
                code, output, timed_out = self.tools._run_tracked(
                    [sys.executable, '-c', "import os,time;os.write(1,b'ready');"
                     "open('ready.signal','w').close();time.sleep(2)"],
                    shell=False, cwd=cwd, seconds=.15, identity='fixture:timeout')
        self.assertEqual(code, -1)
        self.assertTrue(timed_out)
        self.assertEqual(output, 'ready')
        assert started is not None
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertFalse(any(t.name.startswith('zeline-output-') for t in threading.enumerate()))

    @unittest.skipIf(sys.platform == 'win32', 'POSIX inherited pipe fixture')
    def test_parent_exit_does_not_wait_for_inherited_stdout(self):
        import threading
        import time
        started = time.monotonic()
        code, output, timed_out = self.run_code(
            "import os,time;pid=os.fork();"
            "\nif pid==0: time.sleep(.9);os._exit(0)\n"
            "os.write(1,b'parent');os._exit(0)")
        self.assertEqual((code, output, timed_out), (0, 'parent', False))
        self.assertLess(time.monotonic() - started, .8)
        self.assertFalse(any(t.name.startswith('zeline-output-') for t in threading.enumerate()))

    def test_invalid_bytes_still_obey_rendered_byte_budget(self):
        code, output, timed_out = self.run_code("import os;os.write(1,b'\\xff'*60000)")
        self.assertEqual(code, 0)
        self.assertFalse(timed_out)
        self.assertLessEqual(len(output.encode('utf-8')), 50000)

    def test_invalid_bytes_below_raw_budget_still_obey_rendered_budget(self):
        code, output, timed_out = self.run_code("import os;os.write(1,b'\\xff'*19000)")
        self.assertEqual((code, timed_out), (0, False))
        self.assertIn('TRUNCATED', output)
        self.assertLessEqual(len(output.encode('utf-8')), 50000)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX inherited pipe fixture')
    def test_slow_descendant_output_closes_after_parent_grace(self):
        import time
        started = time.monotonic()
        code, output, timed_out = self.run_code(
            "import os,time;pid=os.fork();"
            "\nif pid==0:\n"
            " time.sleep(.05)\n"
            " try:\n"
            "  for _ in range(30): os.write(1,b'child');time.sleep(.02)\n"
            " except BrokenPipeError: pass\n"
            " os._exit(0)\n"
            "os.write(1,b'parent');os._exit(0)")
        self.assertEqual((code, timed_out), (0, False))
        self.assertTrue(output.startswith('parent'))
        self.assertIn('child', output)
        self.assertLess(time.monotonic() - started, .9)

    def test_promotion_preserves_all_bytes_exactly_once(self):
        import threading
        import time
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / 'ready'
            release = Path(tmp) / 'release'
            stop = threading.Event()
            result = []
            before = set(self.tools._BG_JOBS)
            source = (
                "import os,time,pathlib;"
                "os.write(1,b'A'*60000);"
                f"pathlib.Path({str(ready)!r}).touch();"
                f"p=pathlib.Path({str(release)!r});"
                "\nwhile not p.exists(): time.sleep(.01)\n"
                "os.write(1,b'B'*12000)")
            with patch.object(self.tools, '_bg_log_dir', return_value=Path(tmp)):
                worker = threading.Thread(target=lambda: result.append(
                    self.tools._run_tracked(
                        [sys.executable, '-c', source], shell=False, cwd=tmp,
                        seconds=3, identity='fixture:bounded',
                        should_stop=stop.is_set,
                        interrupt_reason=lambda: 'interrupt')))
                worker.start()
                try:
                    deadline = time.monotonic() + 3
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(ready.exists())
                    stop.set()
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertTrue(result)
                    jobs = [v for k, v in self.tools._BG_JOBS.items() if k not in before]
                    self.assertEqual(len(jobs), 1)
                    job = jobs[0]
                    release.touch()
                    job.process.wait(timeout=2)
                    deadline = time.monotonic() + 2
                    while not job.process.stdout.closed and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(job.process.stdout.closed)
                    self.assertEqual(job.log_path.read_bytes(), b'A'*60000+b'B'*12000)
                finally:
                    release.touch()
                    stop.set()
                    worker.join(2)
                    for key in set(self.tools._BG_JOBS) - before:
                        job = self.tools._BG_JOBS.pop(key)
                        self.tools._terminate_group(job.process)


if __name__ == '__main__':
    unittest.main()
