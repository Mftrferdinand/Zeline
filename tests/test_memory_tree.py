"""Tests for zeline/memory_tree.py (Obsidian vault view)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class MemoryTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "vault"
        self.ident = "test:memory-tree"

    def tearDown(self):
        # Clean identity data
        from zeline import memory as mem, goals, config
        for p in [mem._episodes_path(self.ident), goals._path(self.ident)]:
            try:
                p.unlink()
            except OSError:
                pass
        sp = Path(config.DATA_DIR) / "vault-sync.json"
        try:
            st = json.loads(sp.read_text())
            st.pop(self.ident, None)
            if any(not k.startswith("_") for k in st):
                sp.write_text(json.dumps(st, indent=2))
            else:
                sp.unlink()
        except (OSError, ValueError):
            pass

    def test_export_creates_structure(self):
        from zeline import memory as mem, memory_tree as mt
        mem.add_episode(self.ident, "Test deploy", ["Step one", "Step two"], source="test")
        res = mt.export_vault(self.ident, self.vault)
        self.assertTrue(res["ok"])
        self.assertTrue((self.vault / "README.md").is_file())
        self.assertTrue((self.vault / "goals.md").is_file())
        self.assertTrue((self.vault / "user.md").is_file())
        dailies = list((self.vault / "daily").glob("*.md"))
        self.assertEqual(len(dailies), 1)
        text = dailies[0].read_text()
        self.assertIn("Test deploy", text)
        self.assertIn("Step one", text)

    def test_wikilinks_present(self):
        from zeline import memory as mem, memory_tree as mt
        mem.add_episode(self.ident, "Alpha task", ["did alpha"], source="test")
        mem.add_episode(self.ident, "Alpha review", ["reviewed alpha"], source="test")
        files = mt.build_vault_files(self.ident)
        # topics/alpha.md should exist (2 mentions) with wikilinks
        self.assertIn("topics/alpha.md", files)
        self.assertIn("[[daily/", files["topics/alpha.md"])
        self.assertIn("[[", files["README.md"])

    def test_sync_no_changes(self):
        from zeline import memory as mem, memory_tree as mt
        mem.add_episode(self.ident, "Sync test", ["x"], source="test")
        mt.export_vault(self.ident, self.vault)
        res = mt.sync_vault(self.ident, self.vault)
        self.assertEqual(res["changed"], 0)
        self.assertEqual(res["removed"], 0)

    def test_sync_picks_up_changes(self):
        from zeline import memory as mem, memory_tree as mt
        mem.add_episode(self.ident, "First", ["x"], source="test")
        mt.export_vault(self.ident, self.vault)
        mem.add_episode(self.ident, "Second", ["y"], source="test")
        res = mt.sync_vault(self.ident, self.vault)
        self.assertGreaterEqual(res["changed"], 1)

    def test_no_path_escape(self):
        from zeline import memory_tree as mt
        # _write_files must not escape vault root
        n = mt._write_files(self.vault, {"../evil.md": "x", "ok.md": "y"})
        self.assertEqual(n, 1)
        self.assertFalse((self.vault / ".." / "evil.md").exists())
        self.assertTrue((self.vault / "ok.md").is_file())

    def test_vault_path_default(self):
        from zeline import memory_tree as mt
        p = mt.vault_path()
        self.assertEqual(p, Path.home() / "zeline-vault")

    def test_unique_rel_suffixes_on_collision(self):
        # M1: duplicate slugs must not silently overwrite each other.
        from zeline import memory_tree as mt
        files: dict[str, str] = {}
        r1 = mt._unique_rel(files, "topics/deploy.md")
        files[r1] = "a"
        r2 = mt._unique_rel(files, "topics/deploy.md")
        files[r2] = "b"
        r3 = mt._unique_rel(files, "topics/deploy.md")
        files[r3] = "c"
        self.assertEqual(r1, "topics/deploy.md")
        self.assertEqual(r2, "topics/deploy-2.md")
        self.assertEqual(r3, "topics/deploy-3.md")
        self.assertEqual(set(files), {r1, r2, r3})

    def test_write_files_0600(self):
        # M2: vault files must be owner-only readable.
        import stat as _stat
        from zeline import memory_tree as mt
        mt._write_files(self.vault, {"a.md": "x", "sub/b.md": "y"})
        for rel in ("a.md", "sub/b.md"):
            mode = _stat.S_IMODE((self.vault / rel).stat().st_mode)
            self.assertEqual(mode, 0o600, rel)


class MemoryTreeTzTests(unittest.TestCase):
    """A2-L8: date formatting is timezone-aware (UTC), not naive local."""

    def test_fmt_date_utc(self):
        from zeline import memory_tree as mt
        # 2024-10-07 05:46:40 UTC
        self.assertEqual(mt._fmt_date(1728280000.0), "2024-10-07")

    def test_fmt_dt_utc(self):
        from zeline import memory_tree as mt
        self.assertEqual(mt._fmt_dt(1728280000.0), "2024-10-07 05:46")


if __name__ == "__main__":
    unittest.main()
