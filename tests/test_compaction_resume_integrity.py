"""Offline continuation evidence must survive deterministic compaction."""
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

from zeline import compaction


class TestCompactionResumeIntegrity(unittest.TestCase):
    def test_interrupted_read_findings_and_frontier_remain_in_context(self):
        messages = [
            {"role": "user", "content": "Fix parser; keep public API unchanged."},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "read-1", "function": {
                    "name": "read_file", "arguments": {"path": "src/parser.py"},
                },
            }]},
            {"role": "tool", "tool_call_id": "read-1", "content": "line 42: fallback drops Unicode"},
            {"role": "assistant", "content": (
                "Finding: fallback drops Unicode. Decision: preserve codepoints. "
                "Next: add regression before editing. Stopped before implementation."
            )},
        ]
        text = compaction.digest(messages, None)
        self.assertIn("src/parser.py", text)
        self.assertIn("line 42: fallback drops Unicode", text)
        self.assertIn("Decision: preserve codepoints", text)
        self.assertIn("Next: add regression before editing", text)
        self.assertIn("keep public API unchanged", text)
        self.assertIn("unknown", text.lower())
        self.assertNotIn("already handled", text)
        self.assertNotIn("Already-handled", text)
        self.assertNotIn("Do NOT execute any of it again", text)
        self.assertEqual(text, compaction.digest(messages, None))
        self.assertLessEqual(len(text), compaction.MAX_DIGEST_CHARS)
        self.assertNotIn("tool_calls", text)
        self.assertNotIn("tool_call_id", text)
        self.assertTrue(messages[-1]["content"].endswith("Stopped before implementation."))

    def test_archive_streams_messages_without_full_transcript_allocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch.object(compaction.config, 'DATA_DIR', tmp_path):
                messages = [{'role': 'tool', 'content': 'a' * 200000} for _ in range(3)]
                tracemalloc.start()
                try:
                    path = compaction.archive(messages, 'stream-fixture')
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertIsNotNone(path)
                self.assertGreaterEqual(path.read_text().count('a'), 600000)
                self.assertLess(peak, 900000)

    def test_repeated_compaction_preserves_findings_and_next_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with mock.patch.object(compaction.config, 'DATA_DIR', tmp_path):
                messages = [
                    {'role': 'user', 'content': 'ORIGINAL_GOAL: fix parser, preserve public API'},
                    {'role': 'assistant', 'content': 'Finding: VERIFIED_CAUSE. Next: IMPLEMENT_ONCE.'},
                ]
                digest = compaction.compact(messages, 'repeated-fixture')
                for _ in range(4):
                    digest = compaction.compact([digest, {'role': 'user', 'content': 'continue'}], 'repeated-fixture')
                    self.assertIn('ORIGINAL_GOAL', digest['content'])
                    self.assertIn('VERIFIED_CAUSE', digest['content'])
                    self.assertIn('IMPLEMENT_ONCE', digest['content'])
                    self.assertEqual(digest['content'].count(compaction.DIGEST_MARKER), 1)
                    self.assertLessEqual(len(digest['content']), compaction.MAX_DIGEST_CHARS)


if __name__ == '__main__':
    unittest.main()
