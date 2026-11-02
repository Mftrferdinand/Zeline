"""Failure injection and concurrent task-board writes use temporary data only."""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TaskDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.tasks = importlib.import_module("zeline.tasks")
        self.temp = tempfile.TemporaryDirectory(prefix="zl-task-durability-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "tasks"
        self.scope = patch.object(self.tasks, "tasks_dir", return_value=self.directory)
        self.scope.start()
        self.addCleanup(self.scope.stop)

    def test_directory_sync_does_not_use_unsupported_windows_directory_handles(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        denied = Mock(side_effect=AssertionError('POSIX directory handles unavailable'))
        with patch.object(self.tasks, 'os', SimpleNamespace(name='nt', open=denied, O_RDONLY=0)):
            self.tasks._sync_directory(self.directory)
        denied.assert_not_called()

    def test_module_import_does_not_require_posix_fcntl(self):
        import builtins
        import runpy
        original = builtins.__import__
        def portable_import(name, *args, **kwargs):
            if name == 'fcntl':
                raise ImportError('fcntl unavailable on Windows')
            return original(name, *args, **kwargs)
        with patch.object(builtins, '__import__', portable_import):
            runpy.run_path(self.tasks.__file__, run_name='portable_task_probe')

    def test_archive_failure_preserves_unfinished_board(self):
        self.tasks.update("test", "Unfinished work", "pending")
        before = self.tasks._path("test").read_bytes()
        self.tasks.archive_path().mkdir()  # A real filesystem failure, not a mock.
        with self.assertRaises(OSError):
            self.tasks.clear("test", archive=True)
        self.assertEqual(self.tasks._path("test").read_bytes(), before)

    def test_write_failure_is_reported_and_previous_board_preserved(self):
        self.tasks.update("test", "Original", "pending")
        before = self.tasks._path("test").read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.tasks.update("test", "Another", "pending")
        self.assertEqual(self.tasks._path("test").read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_archive_fsync_failure_preserves_board(self):
        self.tasks.update("test", "Original", "pending")
        before = self.tasks._path("test").read_bytes()
        with patch.object(self.tasks.os, "fsync", side_effect=OSError("sync failed")):
            with self.assertRaisesRegex(OSError, "sync failed"):
                self.tasks.clear("test")
        self.assertEqual(self.tasks._path("test").read_bytes(), before)

    def test_direct_writers_have_unique_temporary_files(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        barrier = threading.Barrier(2)
        paths = []
        original = Path.replace

        def replace(source, target):
            paths.append(source)
            barrier.wait(timeout=5)
            return original(source, target)

        with patch.object(Path, "replace", replace):
            with ThreadPoolExecutor(2) as executor:
                list(executor.map(lambda _: self.tasks._write("test", []), range(2)))
        self.assertEqual(len(set(paths)), 2)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_thread_updates_do_not_lose_tasks(self):
        import time
        from concurrent.futures import ThreadPoolExecutor
        original = self.tasks.load

        def slow_load(identity):
            result = original(identity)
            time.sleep(0.02)
            return result

        with patch.object(self.tasks, "load", slow_load):
            with ThreadPoolExecutor(8) as executor:
                list(executor.map(
                    lambda n: self.tasks.update("test", f"Task {n}", "pending"), range(24)
                ))
        self.assertEqual(len(original("test")), 24)

    def test_clear_and_update_are_one_serial_order(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        self.tasks.update("test", "Original", "pending")
        archiving = threading.Event()
        release = threading.Event()
        updating = threading.Event()
        original = self.tasks.os.fsync

        def pause_sync(fd):
            if threading.current_thread().name.startswith("clearer"):
                archiving.set()
                self.assertTrue(release.wait(5))
            return original(fd)

        def update():
            updating.set()
            return self.tasks.update("test", "New work", "pending")

        with patch.object(self.tasks.os, "fsync", pause_sync):
            with ThreadPoolExecutor(1, thread_name_prefix="clearer") as clearer:
                with ThreadPoolExecutor(1) as updater:
                    cleared = clearer.submit(self.tasks.clear, "test")
                    self.assertTrue(archiving.wait(5))
                    updated = updater.submit(update)
                    self.assertTrue(updating.wait(5))
                    release.set()
                    self.assertEqual(cleared.result(timeout=5), 1)
                    updated.result(timeout=5)
        self.assertEqual([x["task"] for x in self.tasks.load("test")], ["New work"])
        self.assertIn("Original", self.tasks.archive_path().read_text())
        self.assertNotIn("New work", self.tasks.archive_path().read_text())

    def test_read_io_error_does_not_overwrite_board(self):
        self.tasks.update("test", "Original", "pending")
        before = self.tasks._path("test").read_bytes()
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(PermissionError, "denied"):
                self.tasks.update("test", "Another", "pending")
        self.assertEqual(self.tasks._path("test").read_bytes(), before)

    def test_process_updates_do_not_lose_tasks(self):
        import subprocess
        import sys
        script = '''
import sys, time
from pathlib import Path
from zeline import tasks
tasks.tasks_dir = lambda: Path(sys.argv[1])
original = tasks.load
def slow_load(identity):
    result = original(identity)
    time.sleep(0.02)
    return result
tasks.load = slow_load
for n in range(8):
    tasks.update("test", f"Worker{sys.argv[2]} task{n}", "pending")
'''
        workers = [subprocess.Popen(
            [sys.executable, "-c", script, str(self.directory), str(n)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) for n in range(4)]
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=30)
            self.assertEqual(worker.returncode, 0, stderr.decode())
        self.assertEqual(len(self.tasks.load("test")), 32)


if __name__ == "__main__":
    unittest.main()
