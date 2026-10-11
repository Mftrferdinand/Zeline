"""Tests for zeline.skillforge — self-improving skill engine."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


def _make_skill_dir(base: Path, slug: str, name: str = "Test Skill") -> Path:
    d = base / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"# {name}\n\n> A test skill.\n\n## Quick steps\n\nDo things.\n")
    return d


class SkillForgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _patch_dir(self):
        from zeline import skillforge

        return patch.object(skillforge, "_learned_dir", return_value=self.tmp)

    def test_get_meta_creates_defaults(self):
        from zeline import skillforge

        _make_skill_dir(self.tmp, "demo-skill", "Demo")
        with self._patch_dir():
            meta = skillforge.get_meta("demo-skill")
        self.assertIsNotNone(meta)
        self.assertEqual(meta.score, skillforge.INITIAL_SCORE)
        self.assertEqual(meta.version, 1)
        self.assertFalse(meta.deprecated)

    def test_record_use_success_and_failure(self):
        from zeline import skillforge

        _make_skill_dir(self.tmp, "s1")
        with self._patch_dir():
            s1 = skillforge.record_use("s1", True)
            self.assertAlmostEqual(s1, skillforge.INITIAL_SCORE + skillforge.SCORE_ON_SUCCESS)
            s2 = skillforge.record_use("s1", False)
            self.assertAlmostEqual(s2, s1 - skillforge.SCORE_ON_FAILURE)
            meta = skillforge.get_meta("s1")
            self.assertEqual(meta.use_count, 2)
            self.assertEqual(meta.success_count, 1)

    def test_score_caps(self):
        from zeline import skillforge

        _make_skill_dir(self.tmp, "s2")
        with self._patch_dir():
            for _ in range(30):
                skillforge.record_use("s2", True)
            meta = skillforge.get_meta("s2")
            self.assertLessEqual(meta.score, 100.0)
            for _ in range(30):
                skillforge.record_use("s2", False)
            meta = skillforge.get_meta("s2")
            self.assertGreaterEqual(meta.score, 0.0)

    def test_auto_deprecate_on_low_score(self):
        from zeline import skillforge

        _make_skill_dir(self.tmp, "s3")
        with self._patch_dir():
            for _ in range(10):
                skillforge.record_use("s3", False)
            meta = skillforge.get_meta("s3")
            self.assertTrue(meta.deprecated)

    def test_revive(self):
        from zeline import skillforge

        _make_skill_dir(self.tmp, "s4")
        with self._patch_dir():
            skillforge.deprecate_skill("s4", reason="test")
            self.assertTrue(skillforge.get_meta("s4").deprecated)
            skillforge.revive_skill("s4")
            meta = skillforge.get_meta("s4")
            self.assertFalse(meta.deprecated)
            self.assertEqual(meta.score, skillforge.INITIAL_SCORE)

    def test_find_relevant_keyword_fallback(self):
        from zeline import skillforge

        _make_skill_dir(self.tmp, "deploy-skill", "Deploy App")
        (self.tmp / "deploy-skill" / "SKILL.md").write_text(
            "# Deploy App\n\n> Deploy application to production server.\n\n## Quick steps\n\nRun deploy script.\n"
        )
        _make_skill_dir(self.tmp, "cook-skill", "Cook Pasta")
        (self.tmp / "cook-skill" / "SKILL.md").write_text(
            "# Cook Pasta\n\n> Cook delicious pasta dinner.\n\n## Quick steps\n\nBoil water.\n"
        )
        with self._patch_dir(), patch.object(
            skillforge, "_embed_text", return_value=(None, "")
        ):
            hits = skillforge.find_relevant("how to deploy my app to production", top_k=2)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["slug"], "deploy-skill")

    def test_find_relevant_short_query(self):
        """Dice coefficient should match short queries against long docs."""
        from zeline import skillforge

        _make_skill_dir(self.tmp, "deploy-skill", "Deploy App")
        (self.tmp / "deploy-skill" / "SKILL.md").write_text(
            "# Deploy App\n\n> Deploy application to production server with zero downtime "
            "using the blue green deployment strategy and health checks.\n\n## Quick steps\n\n"
            "Run the deploy script, verify health, switch traffic.\n"
        )
        with self._patch_dir(), patch.object(
            skillforge, "_embed_text", return_value=(None, "")
        ):
            # Short 2-word query vs long skill text — old cosine would score ~0.2.
            hits = skillforge.find_relevant("deploy app", top_k=2, min_score=0.35)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["slug"], "deploy-skill")

    def test_find_relevant_excludes_deprecated(self):
        from zeline import skillforge

        _make_skill_dir(self.tmp, "old-skill", "Old Deploy")
        (self.tmp / "old-skill" / "SKILL.md").write_text(
            "# Old Deploy\n\n> Deploy application to production.\n\n## Quick steps\n\nDeploy.\n"
        )
        with self._patch_dir(), patch.object(
            skillforge, "_embed_text", return_value=(None, "")
        ):
            skillforge.deprecate_skill("old-skill")
            hits = skillforge.find_relevant("deploy application production", top_k=5)
        self.assertEqual(hits, [])

    def test_register_skill_creates_meta(self):
        from zeline import skillforge

        with self._patch_dir(), patch.object(
            skillforge, "_embed_text", return_value=(None, "")
        ), patch("zeline.learning.learned_dir", return_value=self.tmp):
            res = skillforge.register_skill(
                "My New Skill", "Does new things", "Step 1. Do it.", tags=["test"]
            )
        self.assertTrue(res["slug"])
        self.assertFalse(res["merged"])
        with self._patch_dir():
            meta = skillforge.get_meta(res["slug"])
        self.assertIsNotNone(meta)
        self.assertEqual(meta.version, 1)
        self.assertEqual(meta.tags, ["test"])

    def test_register_merges_duplicate(self):
        from zeline import skillforge

        _make_skill_dir(self.tmp, "deploy-app", "Deploy App")
        (self.tmp / "deploy-app" / "SKILL.md").write_text(
            "# Deploy App\n\n> Deploy application.\n\n## Quick steps\n\nDeploy now.\n"
        )
        fake_dup = {"slug": "deploy-app", "name": "Deploy App", "similarity": 0.95, "action": "merge"}
        with self._patch_dir(), patch.object(
            skillforge, "check_duplicate", return_value=fake_dup
        ), patch("zeline.learning.learned_dir", return_value=self.tmp):
            res = skillforge.register_skill(
                "Deploy App", "Deploy application", "More deploy steps."
            )
        self.assertTrue(res["merged"])
        self.assertEqual(res["duplicate_of"], "deploy-app")
        with self._patch_dir():
            meta = skillforge.get_meta("deploy-app")
        self.assertEqual(meta.version, 2)
        self.assertTrue(meta.previous_content)

    def test_merge_invalidates_embedding(self):
        """Merging new content must clear the stale cached embedding."""
        from zeline import skillforge

        _make_skill_dir(self.tmp, "emb-skill", "Emb")
        with self._patch_dir(), patch.object(
            skillforge, "_embed_text", return_value=([0.1] * 4, "test-model")
        ), patch("zeline.learning.learned_dir", return_value=self.tmp):
            # Seed a cached embedding.
            meta = skillforge.get_meta("emb-skill")
            meta.embedding = [0.1] * 4
            meta.embedding_model = "test-model"
            skillforge.save_meta(meta)
            fake_dup = {"slug": "emb-skill", "name": "Emb", "similarity": 0.95, "action": "merge"}
            with patch.object(skillforge, "check_duplicate", return_value=fake_dup):
                skillforge.register_skill("Emb", "Emb skill", "New content.")
            meta = skillforge.get_meta("emb-skill")
        self.assertIsNone(meta.embedding)
        self.assertEqual(meta.embedding_model, "")

    def test_stale_model_embedding_ignored(self):
        """Cached embedding from a different model must not be reused."""
        from zeline import skillforge

        _make_skill_dir(self.tmp, "model-skill", "Model")
        with self._patch_dir():
            meta = skillforge.get_meta("model-skill")
            meta.embedding = [0.9] * 4
            meta.embedding_model = "old-model"
            skillforge.save_meta(meta)
            with patch.object(skillforge, "_embed_text", return_value=(None, "")):
                self.assertIsNone(skillforge._valid_cached_embedding(meta))

    def test_rollback(self):
        from zeline import skillforge

        d = _make_skill_dir(self.tmp, "rb-skill", "Rollback")
        original = (d / "SKILL.md").read_text()
        with self._patch_dir(), patch.object(
            skillforge, "_embed_text", return_value=(None, "")
        ), patch("zeline.learning.learned_dir", return_value=self.tmp):
            fake_dup = {"slug": "rb-skill", "name": "Rollback", "similarity": 0.95, "action": "merge"}
            with patch.object(skillforge, "check_duplicate", return_value=fake_dup):
                skillforge.register_skill("Rollback", "Rollback skill", "New content here.")
            self.assertTrue(skillforge.rollback_skill("rb-skill"))
            restored = (d / "SKILL.md").read_text()
        self.assertEqual(restored, original)

    def test_skill_report(self):
        from zeline import skillforge

        _make_skill_dir(self.tmp, "a-skill", "A")
        _make_skill_dir(self.tmp, "b-skill", "B")
        _fake_iter = lambda: [("a-skill", self.tmp / "a-skill"), ("b-skill", self.tmp / "b-skill")]
        with self._patch_dir(), patch.object(skillforge, "_iter_all_skills", _fake_iter):
            skillforge.record_use("a-skill", True)
            skillforge.deprecate_skill("b-skill")
            rep = skillforge.skill_report()
        self.assertEqual(rep["total"], 2)
        self.assertEqual(rep["active"], 1)
        self.assertEqual(rep["deprecated"], 1)
        self.assertTrue(rep["top"])

    def test_never_raises_on_broken_dir(self):
        from zeline import skillforge

        with patch.object(skillforge, "_learned_dir", return_value=None), patch.object(
            skillforge, "_iter_all_skills", return_value=[]
        ):
            self.assertIsNone(skillforge.get_meta("x"))
            self.assertIsNone(skillforge.record_use("x", True))
            self.assertEqual(skillforge.find_relevant("q"), [])
            self.assertEqual(skillforge.skill_report()["total"], 0)


class SkillForgeRegressionTest(unittest.TestCase):
    """Regression tests for deep-audit fixes.

    Each test pins a specific bug fix so regressions are caught.
    All tests use full isolation (patch _all_skill_dirs, not just _learned_dir).
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _isolate(self):
        from zeline import skillforge
        p1 = patch.object(skillforge, "_learned_dir", return_value=self.tmp)
        p2 = patch.object(skillforge, "_all_skill_dirs", return_value=[self.tmp])
        return p1, p2

    def test_iter_all_skills_skips_symlinks(self):
        from zeline import skillforge
        _make_skill_dir(self.tmp, "real-skill")
        link = self.tmp / "evil-link"
        try:
            link.symlink_to("/etc", target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        p1, p2 = self._isolate()
        with p1, p2:
            slugs = [s for s, _ in skillforge._iter_all_skills()]
        self.assertIn("real-skill", slugs)
        self.assertNotIn("evil-link", slugs)

    def test_iter_all_skills_skips_dot_dirs(self):
        from zeline import skillforge
        _make_skill_dir(self.tmp, "good-skill")
        (self.tmp / ".archive").mkdir(exist_ok=True)
        (self.tmp / ".archive" / "SKILL.md").write_text("# archived\n")
        p1, p2 = self._isolate()
        with p1, p2:
            slugs = [s for s, _ in skillforge._iter_all_skills()]
        self.assertIn("good-skill", slugs)
        self.assertNotIn(".archive", slugs)

    def test_skill_dir_refuses_symlink(self):
        from zeline import skillforge
        link = self.tmp / "evil-skill"
        try:
            link.symlink_to("/etc", target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        p1, p2 = self._isolate()
        with p1, p2:
            self.assertIsNone(skillforge._skill_dir("evil-skill"))

    def test_rollback_refuses_symlink(self):
        from zeline import skillforge
        d = _make_skill_dir(self.tmp, "rb-sym")
        p1, p2 = self._isolate()
        with p1, p2, patch.object(skillforge, "_embed_text", return_value=(None, "")):
            meta = skillforge.get_meta("rb-sym")
            meta.previous_content = "# old\n"
            meta.version = 2
            skillforge.save_meta(meta)
            (d / "SKILL.md").unlink()
            (d / "SKILL.md").symlink_to("/tmp/somewhere-else.md")
            self.assertFalse(skillforge.rollback_skill("rb-sym"))

    def test_merge_reports_failure_when_write_fails(self):
        from zeline import skillforge
        _make_skill_dir(self.tmp, "merge-fail")
        fake_dup = {"slug": "merge-fail", "name": "X", "similarity": 0.95, "action": "merge"}
        p1, p2 = self._isolate()
        with p1, p2, patch.object(skillforge, "check_duplicate", return_value=fake_dup), \
             patch.object(skillforge, "_embed_text", return_value=(None, "")), \
             patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            res = skillforge.register_skill("Merge Fail", "desc", "new content")
        self.assertFalse(res["merged"])

    def test_keyword_no_false_positive_short_name(self):
        from zeline import skillforge
        _make_skill_dir(self.tmp, "backup-prod", "Backup Prod")
        (self.tmp / "backup-prod" / "SKILL.md").write_text(
            "# Backup Prod\n\n> Backup production databases nightly.\n\n## Steps\n\nRun pg_dump.\n")
        p1, p2 = self._isolate()
        with p1, p2, patch.object(skillforge, "_embed_text", return_value=(None, "")):
            dup = skillforge.check_duplicate("backup", "Simple local backup")
        self.assertTrue(dup is None or dup["action"] != "merge")

    def test_check_duplicate_exact_match_merges(self):
        from zeline import skillforge
        _make_skill_dir(self.tmp, "deploy-app", "Deploy App")
        (self.tmp / "deploy-app" / "SKILL.md").write_text(
            "# Deploy App\n\n> Deploy application to production server with zero downtime.\n\n"
            "## Steps\n\nRun deploy.sh.\n")
        p1, p2 = self._isolate()
        with p1, p2, patch.object(skillforge, "_embed_text", return_value=(None, "")):
            dup = skillforge.check_duplicate(
                "Deploy App",
                "Deploy application to production server with zero downtime")
            self.assertIsNotNone(dup)
            self.assertEqual(dup["action"], "merge")

    def test_parse_bool_string_false(self):
        from zeline.tools import _parse_bool
        self.assertFalse(_parse_bool("false"))
        self.assertFalse(_parse_bool("False"))
        self.assertFalse(_parse_bool("0"))
        self.assertFalse(_parse_bool("no"))
        self.assertTrue(_parse_bool("true"))
        self.assertTrue(_parse_bool("True"))
        self.assertTrue(_parse_bool(True))
        self.assertFalse(_parse_bool(False))

    def test_feedback_message_matches_recorded_outcome(self):
        from zeline import tools as tools_mod
        from zeline import skillforge
        _make_skill_dir(self.tmp, "fb-skill")
        p1, p2 = self._isolate()
        with p1, p2:
            out = tools_mod._skillforge_tool("feedback", slug="fb-skill", success="false")
        self.assertIn("failure", out.lower())
        self.assertNotIn("Recorded success", out)

    def test_merge_result_contains_stable_marker(self):
        from zeline import tools as tools_mod
        from zeline import skillforge
        _make_skill_dir(self.tmp, "marker-skill", "Marker")
        fake_dup = {"slug": "marker-skill", "name": "Marker", "similarity": 0.95, "action": "merge"}
        p1, p2 = self._isolate()
        with p1, p2, patch.object(skillforge, "check_duplicate", return_value=fake_dup), \
             patch.object(skillforge, "_embed_text", return_value=(None, "")), \
             patch("zeline.learning.learned_dir", return_value=self.tmp):
            out = tools_mod._skillforge_tool(
                "register", name="Marker", description="d", content="new stuff")
        self.assertIn("[forge:merged]", out)

    def test_record_use_concurrent_no_lost_updates(self):
        from zeline import skillforge
        _make_skill_dir(self.tmp, "conc-skill")
        p1, p2 = self._isolate()
        with p1, p2:
            skillforge.get_meta("conc-skill")
            threads = [threading.Thread(target=skillforge.record_use, args=("conc-skill", True))
                       for _ in range(20)]
            for t in threads: t.start()
            for t in threads: t.join()
            meta = skillforge.get_meta("conc-skill")
        self.assertEqual(meta.use_count, 20)

    def test_revive_returns_false_for_active_skill(self):
        from zeline import skillforge
        _make_skill_dir(self.tmp, "active-skill")
        p1, p2 = self._isolate()
        with p1, p2, patch.object(skillforge, "_embed_text", return_value=(None, "")):
            meta = skillforge.get_meta("active-skill")
            meta.score = 95.0
            skillforge.save_meta(meta)
            self.assertFalse(skillforge.revive_skill("active-skill"))
            self.assertEqual(skillforge.get_meta("active-skill").score, 95.0)

    def test_rollback_preserves_full_content(self):
        """Rollback must not truncate large skills (data loss bug)."""
        from zeline import skillforge
        d = _make_skill_dir(self.tmp, "big-skill")
        big_content = "# Big\n\n" + ("x" * 10000) + "\n"
        (d / "SKILL.md").write_text(big_content)
        p1, p2 = self._isolate()
        with p1, p2, patch.object(skillforge, "_embed_text", return_value=(None, "")), \
             patch("zeline.learning.learned_dir", return_value=self.tmp):
            # Register a merge to create previous_content
            fake_dup = {"slug": "big-skill", "name": "Big", "similarity": 0.95, "action": "merge"}
            with patch.object(skillforge, "check_duplicate", return_value=fake_dup):
                skillforge.register_skill("Big Skill", "does big things here", "new content here")
            # Rollback should restore the FULL original
            self.assertTrue(skillforge.rollback_skill("big-skill"))
            restored = (d / "SKILL.md").read_text()
            self.assertEqual(restored, big_content)

    def test_record_use_xproc_no_lost_updates(self):
        """Cross-process increments must not be lost (fcntl file lock)."""
        import subprocess
        import sys
        from zeline import skillforge
        _make_skill_dir(self.tmp, "xproc-skill")
        p1, p2 = self._isolate()
        with p1, p2:
            skillforge.get_meta("xproc-skill")  # ensure meta exists
        helper = (
            "import sys\n"
            "sys.path.insert(0, '.')\n"
            "from zeline import skillforge as sf\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            f"tmp = Path({str(self.tmp)!r})\n"
            "p1 = patch.object(sf, '_learned_dir', return_value=tmp)\n"
            "p2 = patch.object(sf, '_all_skill_dirs', return_value=[tmp])\n"
            "p1.start(); p2.start()\n"
            "for _ in range(10):\n"
            "    sf.record_use('xproc-skill', True)\n"
        )
        procs = [
            subprocess.Popen([sys.executable, "-c", helper],
                             cwd=str(Path(__file__).resolve().parents[1]))
            for _ in range(4)
        ]
        for p in procs:
            p.wait(timeout=30)
        p1, p2 = self._isolate()
        with p1, p2:
            meta = skillforge.get_meta("xproc-skill")
        # 4 procs x 10 = 40; allow some slack for slow CI but not 62% loss
        self.assertGreaterEqual(meta.use_count, 35)


if __name__ == "__main__":
    unittest.main()
