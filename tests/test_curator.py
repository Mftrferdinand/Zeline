"""Tests skill curator: scan, archive, restore, prune."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import curator


def _make_skill(root: Path, name: str, description: str, age_days: float = 0) -> Path:
    """Buat skill dummy; age_days>0 memundurkan mtime seluruh isinya."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    if age_days:
        old = time.time() - age_days * 86400
        for path in skill_dir.rglob("*"):
            os.utime(path, (old, old))
        os.utime(skill_dir, (old, old))
    return skill_dir


def _read_ledger(ledger: Path) -> list[dict]:
    if not ledger.is_file():
        return []
    return [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class CuratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.skills = Path(self.temp.name) / "skills"
        self.skills.mkdir()
        self.ledger = Path(self.temp.name) / "ledger.jsonl"
        _make_skill(
            self.skills,
            "old-skill",
            "Skill lama yang sudah tidak dipakai lagi untuk keperluan apapun",
            age_days=120,
        )
        _make_skill(
            self.skills,
            "dup-a",
            "Membantu user menganalisa pasar crypto harian dengan cepat dan akurat",
        )
        _make_skill(
            self.skills,
            "dup-b",
            "Membantu user menganalisa pasar crypto harian dengan lambat tapi pasti",
        )

    def _scan(self, **kwargs):
        return curator.scan(skills_dir=self.skills, ledger_path=self.ledger, **kwargs)

    def test_scan_detects_stale_and_duplicates(self):
        entries = {e["name"]: e for e in self._scan()}
        self.assertEqual(set(entries), {"old-skill", "dup-a", "dup-b"})

        old = entries["old-skill"]
        self.assertTrue(old["stale"])
        self.assertGreater(old["age_days"], 100)
        self.assertGreater(old["size_kb"], 0)
        self.assertIn("T", old["mtime"])  # format ISO

        dup_a = entries["dup-a"]
        dup_b = entries["dup-b"]
        self.assertFalse(dup_a["stale"])
        self.assertFalse(dup_b["stale"])
        self.assertIn("dup-b", dup_a["possible_duplicates"])
        self.assertIn("dup-a", dup_b["possible_duplicates"])
        self.assertEqual(old["possible_duplicates"], [])

    def test_scan_skips_archive_dir(self):
        (self.skills / ".archive").mkdir()
        entries = self._scan()
        self.assertEqual(len(entries), 3)

    def test_archive_moves_and_logs(self):
        dst = curator.archive("old-skill", skills_dir=self.skills, ledger_path=self.ledger)
        self.assertFalse((self.skills / "old-skill").exists())
        self.assertTrue(dst.is_dir())
        self.assertTrue(dst.parent.name == ".archive")
        self.assertRegex(dst.name, r"^old-skill-\d{8}-\d{6}$")
        self.assertTrue((dst / "SKILL.md").is_file())

        records = _read_ledger(self.ledger)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "archive")
        self.assertEqual(records[0]["name"], "old-skill")
        self.assertIn("ts", records[0])

    def test_archive_missing_or_already_archived_errors(self):
        with self.assertRaises(curator.CuratorError):
            curator.archive("nope", skills_dir=self.skills, ledger_path=self.ledger)
        curator.archive("old-skill", skills_dir=self.skills, ledger_path=self.ledger)
        with self.assertRaisesRegex(curator.CuratorError, "sudah di-archive"):
            curator.archive("old-skill", skills_dir=self.skills, ledger_path=self.ledger)
        with self.assertRaises(curator.CuratorError):
            curator.archive("../evil", skills_dir=self.skills, ledger_path=self.ledger)

    def test_restore_brings_back(self):
        curator.archive("old-skill", skills_dir=self.skills, ledger_path=self.ledger)
        dst = curator.restore("old-skill", skills_dir=self.skills, ledger_path=self.ledger)
        self.assertEqual(dst, self.skills / "old-skill")
        self.assertTrue((dst / "SKILL.md").is_file())
        self.assertFalse(any((self.skills / ".archive").iterdir()))

        records = _read_ledger(self.ledger)
        self.assertEqual([r["action"] for r in records], ["archive", "restore"])

    def test_restore_missing_errors(self):
        with self.assertRaises(curator.CuratorError):
            curator.restore("nope", skills_dir=self.skills, ledger_path=self.ledger)

    def test_prune_dry_run_moves_nothing(self):
        plan = curator.prune(skills_dir=self.skills, ledger_path=self.ledger)
        self.assertEqual([e["name"] for e in plan], ["old-skill"])
        self.assertTrue((self.skills / "old-skill").is_dir())
        self.assertNotIn("archived_to", plan[0])
        self.assertEqual(_read_ledger(self.ledger), [])

    def test_prune_apply_archives_stale(self):
        plan = curator.prune(apply=True, skills_dir=self.skills, ledger_path=self.ledger)
        self.assertEqual([e["name"] for e in plan], ["old-skill"])
        self.assertIn("archived_to", plan[0])
        self.assertFalse((self.skills / "old-skill").exists())
        self.assertTrue((self.skills / "dup-a").is_dir())
        self.assertTrue((self.skills / "dup-b").is_dir())
        records = _read_ledger(self.ledger)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["action"], "archive")


if __name__ == "__main__":
    unittest.main()
