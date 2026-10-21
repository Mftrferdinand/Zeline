"""Regressions for review blockers in resumable reads and stored windows."""
import tempfile
import unittest
from pathlib import Path

from zeline import file_io, session_store


class ReviewIOPersistenceTests(unittest.TestCase):
    def test_invalid_utf8_page_reports_error_without_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.txt"
            path.write_bytes(b"\xff" * 60_000)

            result = file_io.read_text_window(path, limit=1)

        self.assertTrue(result.startswith("ERROR read file:"), result[:300])
        self.assertIn("utf-8", result.lower())
        self.assertNotIn("next_byte_offset=", result)
        self.assertNotIn("more_results=", result)
        self.assertLessEqual(len(result.encode()), file_io.MAX_OUTPUT_BYTES)

    def test_latest_tool_batch_does_not_exempt_assistant_suffix(self):
        for checkpoint in (False, True):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as directory:
                persistence = session_store.SessionPersistence(Path(directory) / "sessions.db")
                goal = {"role": "user", "content": "ORIGINAL_TASK: repair verified cause"}
                call = {"role": "assistant", "content": "Read evidence", "tool_calls": [
                    {"id": "read-1", "type": "function", "function": {
                        "name": "read_file", "arguments": '{"path":"evidence.txt"}'}}]}
                result = {"role": "tool", "tool_call_id": "read-1", "content": "VERIFIED_RESULT"}
                findings = [{"role": "assistant", "content": f"Finding {i}"} for i in range(100)]
                history = [goal, call, result, *findings]
                save = persistence.save_checkpoint if checkpoint else persistence.save

                save("review", history)
                restored, _ = session_store.SessionPersistence(persistence.path).load("review")

                stored = [m for m in restored if not m.get("content", "").startswith("[Zeline recovery]")]
                self.assertLessEqual(len(stored), session_store.MAX_STORED_MESSAGES)
                self.assertIn(goal, stored)
                self.assertIn(call, stored)
                self.assertIn(result, stored)
                self.assertEqual(stored[-1], findings[-1])
                self.assertEqual(sum(bool(m.get("tool_calls")) for m in stored), 1)
                self.assertEqual(sum(m.get("role") == "tool" for m in stored), 1)
                self.assertTrue(any(session_store.compaction.is_digest(m) for m in stored))
                self.assertEqual(history, [goal, call, result, *findings])
                self.assertEqual(
                    any(m.get("content", "").startswith("[Zeline recovery]") for m in restored),
                    checkpoint,
                )


if __name__ == "__main__":
    unittest.main()
