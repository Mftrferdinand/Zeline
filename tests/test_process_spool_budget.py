"""Foreground preservation must bound disk spool as well as RAM."""
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class TestProcessSpoolBudget(unittest.TestCase):
    def test_foreground_spool_has_a_disk_budget_and_explicit_handoff_omission(self):
        output = importlib.import_module("zeline.process_output")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = b"first\n" + b"x" * 65_536 + b"\nlast\n"
            stream = tempfile.TemporaryFile(mode="w+b")
            stream.write(payload)
            stream.seek(0)
            process = SimpleNamespace(pid=12345, stdout=stream, poll=lambda: 0)
            target = tmp_path / "promoted.log"
            with mock.patch.object(output, "SPOOL_BUDGET", 16_384, create=True):
                capture = output.ProcessOutput(process)
                try:
                    self.assertTrue(capture.done.wait(3), "finite fixture reader did not finish")
                    self.assertLessEqual(os.fstat(capture.spool.fileno()).st_size, 16_384)
                    handle = target.open("wb", buffering=0)
                    capture.promote(handle)
                    text = target.read_bytes()
                    self.assertIn(b"TRUNCATED", text)
                    self.assertTrue(text.endswith(b"last\n"))
                finally:
                    capture.finish()


if __name__ == '__main__':
    unittest.main()
