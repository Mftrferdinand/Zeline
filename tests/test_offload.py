"""Tool-output offload: large results go to disk, not to the bit bucket."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from zeline import config, offload, tools


class OffloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        patcher = mock.patch.object(config, "DATA_DIR", Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_small_output_is_returned_unchanged(self):
        self.assertEqual(offload.maybe_offload("hello", 100), "hello")
        self.assertFalse(offload.root().exists())

    def test_large_output_is_written_and_pointer_returned(self):
        text = "\n".join(f"line {index}" for index in range(500))
        result = offload.maybe_offload(text, 100)
        self.assertIn("Output too large for context", result)
        self.assertIn("read_file(", result)
        files = list(offload.root().glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(encoding="utf-8"), text)
        self.assertIn(str(files[0]), result)

    def test_pointer_preview_shows_head_and_tail(self):
        text = "\n".join(f"line {index}" for index in range(500))
        result = offload.maybe_offload(text, 100)
        self.assertIn("line 0", result)
        self.assertIn("line 499", result)
        self.assertIn("lines omitted", result)
        self.assertNotIn("line 250", result)

    def test_pointer_is_far_smaller_than_the_payload(self):
        text = "x" * 400_000
        result = offload.maybe_offload(text, 12_000)
        self.assertLess(len(result), 4_000, "pointer must not itself flood the context")

    def test_identical_output_is_stored_once(self):
        text = "y" * 50_000
        first = offload.maybe_offload(text, 100)
        second = offload.maybe_offload(text, 100)
        self.assertEqual(first, second)
        self.assertEqual(len(list(offload.root().glob("*.txt"))), 1)

    def test_write_failure_falls_back_to_truncation_without_a_dangling_pointer(self):
        text = "z" * 50_000
        with mock.patch.object(offload, "store", return_value=None):
            result = offload.maybe_offload(text, 100)
        self.assertIn("offload to disk failed", result)
        self.assertNotIn("read_file(", result)

    def test_long_preview_lines_are_capped(self):
        text = "\n".join(["q" * 50_000] * 40)
        result = offload.maybe_offload(text, 100)
        self.assertIn("[line truncated]", result)
        for line in result.splitlines():
            self.assertLess(len(line), offload.PREVIEW_LINE_CHARS + 200)

    def test_prune_removes_stale_payloads(self):
        target = offload.store("k" * 50_000)
        assert target is not None
        import os
        stale = 1
        os.utime(target, (stale, stale))
        self.assertEqual(offload.prune(), 1)
        self.assertFalse(target.exists())

    def test_is_offload_path_rejects_outside_paths(self):
        target = offload.store("m" * 50_000)
        assert target is not None
        self.assertTrue(offload.is_offload_path(target))
        self.assertFalse(offload.is_offload_path(Path("/etc/passwd")))


class ReadFileWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(config, "DATA_DIR", self.workspace / "data")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_offset_and_limit_return_a_window_with_a_continuation_hint(self):
        target = self.workspace / "big.txt"
        target.write_text("\n".join(f"row {index}" for index in range(100)), encoding="utf-8")
        result = tools._read_file("big.txt", self.workspace, offset=10, limit=5)
        self.assertIn("row 9", result)
        self.assertIn("row 13", result)
        self.assertNotIn("row 14", result)
        self.assertIn("offset=15", result)

    def test_offset_past_end_is_an_error_not_an_empty_read(self):
        target = self.workspace / "small.txt"
        target.write_text("only one line", encoding="utf-8")
        result = tools._read_file("small.txt", self.workspace, offset=99)
        self.assertTrue(result.startswith("ERROR read file:"))

    def test_offloaded_payload_is_readable_outside_the_workspace(self):
        text = "\n".join(f"payload {index}" for index in range(2_000))
        target = offload.store(text)
        assert target is not None
        result = tools._read_file(str(target), self.workspace, offset=1, limit=3)
        self.assertIn("payload 0", result)
        self.assertIn("payload 2", result)

    def test_arbitrary_absolute_paths_stay_blocked(self):
        result = tools._read_file("/etc/hostname", self.workspace)
        self.assertTrue(result.startswith("ERROR read file:"))

    def test_oversized_file_read_offloads_instead_of_silently_cutting(self):
        target = self.workspace / "huge.txt"
        target.write_text("w" * 40_000, encoding="utf-8")
        result = tools._read_file("huge.txt", self.workspace)
        self.assertIn("Output too large for context", result)
        self.assertNotIn("truncated, file too long", result)


class TruncateOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        patcher = mock.patch.object(config, "DATA_DIR", Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_empty_output_message_is_preserved(self):
        self.assertEqual(tools._truncate_output("   "), "(no output)")

    def test_shell_output_over_the_limit_is_recoverable(self):
        result = tools._truncate_output("s" * 30_000)
        self.assertIn("read_file(", result)
        self.assertNotIn("... [output truncated]", result)


if __name__ == "__main__":
    unittest.main()
