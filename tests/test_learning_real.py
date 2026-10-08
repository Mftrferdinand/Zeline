"""Real tests for learning.py (H3: was zero coverage)."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch


class LearningSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _patch_dir(self):
        from zeline import learning
        p = patch.object(learning, 'learned_dir', return_value=Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        return learning

    def test_save_creates_skill_dir(self):
        learning = self._patch_dir()
        path = learning.save_learned_skill("test-skill", "desc", "content here")
        p = Path(path)
        self.assertTrue(p.is_dir())
        self.assertTrue((p / "SKILL.md").is_file())
        self.assertTrue((p / "references" / "details.md").is_file())
        self.assertTrue((p / "scripts").is_dir())

    def test_save_sanitizes_newlines(self):
        """M2: newline injection should be neutralized."""
        learning = self._patch_dir()
        path = learning.save_learned_skill(
            "evil\n# injected", "desc\nnot-quote", "content")
        skill_md = Path(path) / "SKILL.md"
        text = skill_md.read_text()
        # No raw newlines from name/desc should create new markdown blocks
        lines = text.splitlines()
        # First line should be the sanitized title (single line)
        self.assertTrue(lines[0].startswith("# "))
        self.assertNotIn("# injected", lines)

    def test_save_truncates_long_name(self):
        """M1: very long names should not crash."""
        learning = self._patch_dir()
        path = learning.save_learned_skill("x" * 300, "desc", "content")
        # Should not raise OSError
        self.assertTrue(Path(path).is_dir())

    def test_save_no_collision(self):
        """H2: rapid saves should not clobber each other."""
        learning = self._patch_dir()
        p1 = learning.save_learned_skill("race", "d1", "content-AAA")
        p2 = learning.save_learned_skill("race", "d2", "content-BBB")
        p3 = learning.save_learned_skill("race", "d3", "content-CCC")
        self.assertNotEqual(p1, p2)
        self.assertNotEqual(p2, p3)
        # All content preserved
        self.assertIn("content-AAA", (Path(p1) / "references" / "details.md").read_text())
        self.assertIn("content-BBB", (Path(p2) / "references" / "details.md").read_text())
        self.assertIn("content-CCC", (Path(p3) / "references" / "details.md").read_text())

    def test_improve_new_format(self):
        """H1: improve should work on directory-format skills."""
        learning = self._patch_dir()
        path = learning.save_learned_skill("my-skill", "desc", "original")
        result = learning.improve_learned_skill("my-skill", "improvement note")
        self.assertEqual(result, path)
        skill_md = Path(path) / "SKILL.md"
        self.assertIn("improvement note", skill_md.read_text())
        details_md = Path(path) / "references" / "details.md"
        self.assertIn("improvement note", details_md.read_text())

    def test_improve_not_found(self):
        learning = self._patch_dir()
        with self.assertRaises(FileNotFoundError):
            learning.improve_learned_skill("nonexistent", "x")

    def test_list_finds_both_formats(self):
        learning = self._patch_dir()
        learning.save_learned_skill("new-skill", "new desc", "c")
        # Legacy format
        legacy = Path(self.tmp.name) / "legacy-skill.md"
        legacy.write_text("# Legacy\n\n> old desc\n\ncontent\n")
        skills = learning.list_learned_skills()
        names = [s["name"] for s in skills]
        self.assertIn("new-skill", names)
        self.assertIn("Legacy", names)

    def test_slugify_path_traversal(self):
        from zeline import learning
        self.assertEqual(learning._slugify("../../../etc"), "etc")
        self.assertEqual(learning._slugify("a/b"), "a-b")
        self.assertNotIn("/", learning._slugify("a/b/c"))


class DedupTests(unittest.TestCase):
    def test_dedup_actually_runs(self):
        """M3 fix: test must execute assertions."""
        from zeline import gepa
        with tempfile.TemporaryDirectory() as tmp:
            seq_path = Path(tmp) / "s.jsonl"
            with patch.object(gepa, '_sequences_path', return_value=seq_path):
                # Create a clear subsequence scenario:
                # Record A-B-C-D 5x (long pattern)
                for _ in range(5):
                    for tool in ["a", "b", "c", "d"]:
                        gepa.record_tool_call(tool, True)
                patterns = gepa.extract_patterns(min_length=2, min_occurrences=2)
                # Must return at least one pattern
                self.assertGreater(len(patterns), 0, "No patterns extracted at all")
                # Verify no pattern is a strict subsequence of another
                seqs = [tuple(p["sequence"]) for p in patterns]
                for i, s1 in enumerate(seqs):
                    for j, s2 in enumerate(seqs):
                        if i != j and len(s2) > len(s1):
                            is_subseq = any(
                                s2[k:k+len(s1)] == s1
                                for k in range(len(s2) - len(s1) + 1)
                            )
                            self.assertFalse(
                                is_subseq,
                                f"Pattern {s1} is subsequence of {s2} - dedup failed"
                            )


if __name__ == "__main__":
    unittest.main()
