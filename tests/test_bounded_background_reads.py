"""Background log inspection must not undo bounded foreground capture."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock


class BoundedBackgroundReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = self.temp.name
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.tools = importlib.import_module("zeline.tools")
        self.path = Path(self.temp.name) / "output.log"
        self.process = mock.Mock(pid=123, returncode=0)
        self.process.poll.return_value = 0
        self.job = self.tools._BackgroundJob("fixture", "finite fixture", self.process, self.path, 1.0)
        self.tools._BG_JOBS[self.job.job_id] = self.job

    def tearDown(self):
        self.tools._BG_JOBS.clear()
        if self.old is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old
        self.temp.cleanup()

    def test_poll_reads_a_bounded_byte_window_and_keeps_a_continuation(self):
        self.path.write_bytes(b"x" * 1_048_576)
        tracemalloc.start()
        try:
            result = self.tools._bg_new_output(self.job)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLessEqual(self.job.read_offset, 50_000)
        self.assertLess(peak, 768_000)
        self.assertIn("poll again", result)

    def test_log_tails_bounded_bytes_without_whole_file_read_text(self):
        self.path.write_bytes(b"x" * 1_048_576 + b"\nlast one\nlast two\n")
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("whole-file read forbidden")):
            result = self.tools._process_control("log", self.job.job_id, lines=2)
        self.assertIn("last one", result)
        self.assertIn("last two", result)
        self.assertIn("bounded", result)

    def test_poll_does_not_split_a_multibyte_character_across_windows(self):
        self.path.write_bytes("aaaaéx\n".encode())
        with mock.patch.object(self.tools, "_BACKGROUND_READ_BYTES", 5):
            first = self.tools._bg_new_output(self.job)
            self.assertEqual(self.job.read_offset, 4)
            second = self.tools._bg_new_output(self.job)
        self.assertNotIn("\ufffd", first + second)
        self.assertIn("éx", second)

    def test_poll_cursor_does_not_skip_later_bytes(self):
        self.path.write_bytes(b"head\n")
        first = self.tools._bg_new_output(self.job)
        with self.path.open("ab") as stream:
            stream.write(b"tail\n")
        second = self.tools._bg_new_output(self.job)
        self.assertIn("head", first)
        self.assertNotIn("head", second)
        self.assertIn("tail", second)
        self.assertEqual(self.job.read_offset, self.path.stat().st_size)


if __name__ == "__main__":
    unittest.main()
