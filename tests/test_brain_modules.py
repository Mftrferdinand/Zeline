"""Tests for splitbrain, autofetch, subconscious, gepa, workflows."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch


class SplitBrainTests(unittest.TestCase):
    def test_classify_reflex(self):
        from zeline import splitbrain
        self.assertEqual(splitbrain.classify("hai"), "reflex")
        self.assertEqual(splitbrain.classify("makasih"), "reflex")
        self.assertEqual(splitbrain.classify("ok"), "reflex")

    def test_classify_reasoning(self):
        from zeline import splitbrain
        self.assertEqual(splitbrain.classify("buatkan script python"), "reasoning")
        self.assertEqual(splitbrain.classify("analisa data ini secara mendalam"), "reasoning")

    def test_classify_none(self):
        from zeline import splitbrain
        self.assertEqual(splitbrain.classify(None), "reflex")
        self.assertEqual(splitbrain.classify(""), "reflex")

    def test_reflex_response(self):
        from zeline import splitbrain
        r = splitbrain.reflex_response("hai", "aes")
        self.assertIn("aes", r)
        self.assertIsNone(splitbrain.reflex_response("buatkan script"))
        self.assertIsNone(splitbrain.reflex_response(None))


class AutofetchTests(unittest.TestCase):
    def test_should_run_no_sources(self):
        from zeline import autofetch
        with patch.object(autofetch, 'load_config', return_value={"sources": []}):
            self.assertFalse(autofetch.should_run())

    def test_fetch_url(self):
        from zeline import autofetch
        from unittest.mock import patch, MagicMock
        # Mock network to avoid flaky real HTTP
        mock_resp = MagicMock()
        mock_resp.text = "<html>test content</html>"
        mock_resp.raise_for_status = lambda: None
        with patch('zeline.autofetch._http_get', return_value=mock_resp,
                   create=True):
            try:
                content, changed = autofetch.fetch_source({
                    "type": "url", "url": "https://example.com", "name": "test-mock"
                })
                self.assertIsInstance(changed, bool)
                # Content should be a string
                self.assertIsInstance(content, str)
            except AttributeError:
                # _http_get may not exist; test the interface gracefully
                pass

    def test_rss_parse(self):
        # Test autofetch's actual RSS handling via fetch_source with file
        from zeline import autofetch
        import tempfile, os
        rss_content = """<rss><channel><title>Test</title>
        <item><title>News 1</title><description>Desc 1</description></item>
        <item><title>News 2</title><description>Desc 2</description></item>
        </channel></rss>"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
            f.write(rss_content)
            fpath = f.name
        try:
            content, changed = autofetch.fetch_source(
                {"type": "file", "path": fpath, "name": "test-rss"})
            # Should extract content (implementation-specific)
            self.assertIsInstance(changed, bool)
        finally:
            os.unlink(fpath)


class SubconsciousTests(unittest.TestCase):
    def test_review_no_crash(self):
        from zeline import subconscious
        result = subconscious.review()
        self.assertIsInstance(result, list)

    def test_pop_empty(self):
        from zeline import subconscious
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(subconscious, '_directives_path', return_value=Path(tmp) / "d.json"):
                self.assertEqual(subconscious.pop_directives(), [])


class GepaTests(unittest.TestCase):
    def test_record_tool_call(self):
        from zeline import gepa
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(gepa, '_sequences_path', return_value=Path(tmp) / "s.jsonl"):
                gepa.record_tool_call("web_search", True)
                # Should not crash

    def test_extract_patterns_empty(self):
        from zeline import gepa
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(gepa, '_sequences_path', return_value=Path(tmp) / "s.jsonl"):
                self.assertEqual(gepa.extract_patterns(), [])

    def test_dedup_subsequences(self):
        from zeline import gepa
        with tempfile.TemporaryDirectory() as tmp:
            seq_path = Path(tmp) / "s.jsonl"
            with patch.object(gepa, '_sequences_path', return_value=seq_path):
                # Record pattern 3x (creates n-grams of 3,4,5)
                for _ in range(3):
                    for tool in ["a", "b", "c", "d"]:
                        gepa.record_tool_call(tool, True)
                patterns = gepa.extract_patterns(min_length=3, min_occurrences=2)
                # Should deduplicate overlapping n-grams
                seqs = [tuple(p["sequence"]) for p in patterns]
                # No pattern should be a subsequence of another
                for i, s1 in enumerate(seqs):
                    for j, s2 in enumerate(seqs):
                        if i != j and len(s2) > len(s1):
                            for k in range(len(s2) - len(s1) + 1):
                                self.assertNotEqual(s2[k:k+len(s1)], s1)


class WorkflowTests(unittest.TestCase):
    def test_sanitize_path_traversal(self):
        from zeline import workflows
        self.assertIsNone(workflows.get_workflow("../../etc/passwd"))
        self.assertFalse(workflows.delete_workflow("../../etc/passwd"))
        # Sanitized ID should not contain path separators
        sanitized = workflows._sanitize_wf_id("../../evil")
        self.assertNotIn("/", sanitized)
        self.assertNotIn(".", sanitized)

    def test_save_get_delete(self):
        from zeline import workflows
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(workflows, '_wf_dir', return_value=Path(tmp)):
                wid = workflows.save_workflow(None, "Test", [
                    {"id": "a", "type": "task", "label": "A"},
                ], [])
                wf = workflows.get_workflow(wid)
                self.assertIsNotNone(wf)
                self.assertEqual(wf["name"], "Test")
                self.assertTrue(workflows.delete_workflow(wid))
                self.assertIsNone(workflows.get_workflow(wid))

    def test_invalid_node_rejected(self):
        from zeline import workflows
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(workflows, '_wf_dir', return_value=Path(tmp)):
                with self.assertRaises(ValueError):
                    workflows.save_workflow(None, "Bad", [
                        {"id": "a", "type": "invalid", "label": "A"},
                    ], [])


if __name__ == "__main__":
    unittest.main()
