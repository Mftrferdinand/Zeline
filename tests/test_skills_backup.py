"""Skill edits are checkpointed before they destroy the previous version.

Reflection can call manage_skill autonomously — patch a skill, overwrite a
reference file, or delete a skill it thinks is a duplicate. Before this change a
wrong self-write was unrecoverable: the previous procedural memory was simply
gone. These tests pin a safety net that reuses the SAME checkpoint store the
file tools use (bounded, private, 0600), so a bad skill edit can be recovered
with `zeline undo`, without inventing a second backup mechanism.

Design guarantees under test:

- patch/write-overwrite/delete snapshot the previous bytes FIRST;
- a brand-new skill (create) or a brand-new reference file makes NO spurious
  checkpoint — there is nothing to back up;
- the snapshot is best-effort: if it fails, the edit still proceeds (a safety
  net that refuses work is not a safety net).
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    return (
        importlib.import_module("zeline.skills"),
        importlib.import_module("zeline.checkpoints"),
    )


class SkillBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.old_home = os.environ.get("ZELINE_HOME")
        self.skills, self.checkpoints = _fresh(self.home)
        self.skills._ensure_dirs()

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_home
        self.temp.cleanup()

    def _skill_md(self, name: str) -> Path:
        return self.skills.PRIVATE_SKILLS_DIR / name / self.skills.SKILL_ENTRY

    # ------------------------------------------------------------------ create
    def test_creating_a_skill_makes_no_checkpoint(self):
        self.skills.manage_skill("create", name="demo", content="# Demo\n> d\nlangkah")
        # Nothing existed before, so there is nothing to back up.
        self.assertEqual(self.checkpoints.list_checkpoints(), [])

    # ------------------------------------------------------------------- patch
    def test_patching_a_skill_checkpoints_the_previous_content(self):
        self.skills.manage_skill("create", name="demo", content="# Demo\n> d\nlangkah lama")
        before = self._skill_md("demo").read_text(encoding="utf-8")
        self.skills.manage_skill("patch", name="demo", old_text="lama", new_text="baru")

        entries = self.checkpoints.list_checkpoints(path=self._skill_md("demo"))
        self.assertEqual(len(entries), 1)
        # Restoring the checkpoint brings back the exact pre-patch bytes.
        ok, _msg = self.checkpoints.restore(entries[0]["id"], workspace=None)
        self.assertTrue(ok)
        self.assertEqual(self._skill_md("demo").read_text(encoding="utf-8"), before)

    def test_patch_still_succeeds_when_backup_fails(self):
        self.skills.manage_skill("create", name="demo", content="# Demo\n> d\nlangkah lama")
        with mock.patch.object(self.checkpoints, "snapshot", side_effect=RuntimeError("disk full")):
            result = self.skills.manage_skill("patch", name="demo", old_text="lama", new_text="baru")
        self.assertIn("Patched", result)
        self.assertIn("baru", self._skill_md("demo").read_text(encoding="utf-8"))

    # -------------------------------------------------------------- write_file
    def test_overwriting_a_reference_file_checkpoints_the_old_one(self):
        self.skills.manage_skill("create", name="demo", content="# Demo\n> d\nlangkah")
        self.skills.manage_skill("write_file", name="demo", file_path="references/api.md", content="v1")
        # First write of a brand-new reference: nothing to back up.
        self.assertEqual(self.checkpoints.list_checkpoints(), [])
        self.skills.manage_skill("write_file", name="demo", file_path="references/api.md", content="v2")
        ref = self.skills.PRIVATE_SKILLS_DIR / "demo" / "references" / "api.md"
        entries = self.checkpoints.list_checkpoints(path=ref)
        self.assertEqual(len(entries), 1)

    # ------------------------------------------------------------------ delete
    def test_deleting_a_skill_checkpoints_every_file_first(self):
        self.skills.manage_skill("create", name="doomed", content="# Doomed\n> d\nlangkah")
        self.skills.manage_skill("write_file", name="doomed", file_path="references/x.md", content="ref body")
        skill_md = self._skill_md("doomed")
        ref = self.skills.PRIVATE_SKILLS_DIR / "doomed" / "references" / "x.md"

        self.skills.manage_skill("delete", name="doomed", absorbed_into="")
        self.assertFalse(skill_md.exists())

        # Both files are recoverable, so a wrong autonomous delete is not fatal.
        md_ckpts = self.checkpoints.list_checkpoints(path=skill_md)
        ref_ckpts = self.checkpoints.list_checkpoints(path=ref)
        self.assertEqual(len(md_ckpts), 1)
        self.assertEqual(len(ref_ckpts), 1)
        ok, _ = self.checkpoints.restore(md_ckpts[0]["id"], workspace=None)
        self.assertTrue(ok)
        self.assertIn("Doomed", skill_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
