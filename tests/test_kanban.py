"""Tests for zeline/kanban.py — simple kanban board."""

import json
import os
import stat
import unittest

from zeline import kanban


class KanbanTests(unittest.TestCase):
    def setUp(self):
        # Isolate HOME so we don't touch the real ~/.zeline
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="kanban-test-")
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmp
        # Re-point module constant (it was computed at import from real HOME)
        kanban._KANBAN_FILE = kanban.Path.home() / ".zeline" / "kanban.json"

    def tearDown(self):
        import shutil
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_create_and_get(self):
        cid = kanban.create_card("Build feature X", "details here")
        self.assertTrue(cid)
        card = kanban.get_card(cid)
        self.assertEqual(card["title"], "Build feature X")
        self.assertEqual(card["description"], "details here")
        self.assertEqual(card["column"], "todo")

    def test_create_default_column(self):
        cid = kanban.create_card("t")
        self.assertEqual(kanban.get_card(cid)["column"], "todo")

    def test_create_explicit_column(self):
        cid = kanban.create_card("t", column="doing")
        self.assertEqual(kanban.get_card(cid)["column"], "doing")

    def test_create_requires_title(self):
        with self.assertRaises(ValueError):
            kanban.create_card("")
        with self.assertRaises(ValueError):
            kanban.create_card("   ")

    def test_create_bad_column(self):
        with self.assertRaises(ValueError):
            kanban.create_card("t", column="backlog")

    def test_move_card(self):
        cid = kanban.create_card("t")
        card = kanban.move_card(cid, "doing")
        self.assertEqual(card["column"], "doing")
        self.assertEqual(kanban.get_card(cid)["column"], "doing")
        kanban.move_card(cid, "done")
        self.assertEqual(kanban.get_card(cid)["column"], "done")

    def test_move_missing_card(self):
        with self.assertRaises(ValueError):
            kanban.move_card("nope123", "doing")

    def test_move_bad_column(self):
        cid = kanban.create_card("t")
        with self.assertRaises(ValueError):
            kanban.move_card(cid, "archive")

    def test_list_all_and_filtered(self):
        a = kanban.create_card("a", column="todo")
        b = kanban.create_card("b", column="doing")
        all_cards = kanban.list_cards()
        self.assertEqual(len(all_cards), 2)
        todo = kanban.list_cards("todo")
        self.assertEqual([c["id"] for c in todo], [a])
        doing = kanban.list_cards("doing")
        self.assertEqual([c["id"] for c in doing], [b])

    def test_list_bad_column(self):
        with self.assertRaises(ValueError):
            kanban.list_cards("nope")

    def test_delete(self):
        cid = kanban.create_card("t")
        self.assertTrue(kanban.delete_card(cid))
        self.assertIsNone(kanban.get_card(cid))
        self.assertFalse(kanban.delete_card(cid))  # already gone
        self.assertFalse(kanban.delete_card(""))  # empty id

    def test_get_missing(self):
        self.assertIsNone(kanban.get_card("nope"))
        self.assertIsNone(kanban.get_card(""))

    def test_file_perms_0600(self):
        kanban.create_card("t")
        mode = stat.S_IMODE(os.stat(kanban._KANBAN_FILE).st_mode)
        self.assertEqual(mode, 0o600)

    def test_persistence(self):
        cid = kanban.create_card("persist me", column="doing")
        # Simulate fresh process: reload from disk
        board = kanban._load()
        ids = [c["id"] for c in board["cards"]]
        self.assertIn(cid, ids)

    def test_corrupt_file_recovers(self):
        kanban._KANBAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        kanban._KANBAN_FILE.write_text("not json{{{", encoding="utf-8")
        self.assertEqual(kanban.list_cards(), [])
        # File corrupt harus di-backup ke .bak sebelum reset.
        bak = kanban._KANBAN_FILE.with_suffix(".json.bak")
        self.assertTrue(bak.exists(), ".bak backup tidak dibuat")
        self.assertEqual(bak.read_text(encoding="utf-8"), "not json{{{")
        # And it still works after corruption
        cid = kanban.create_card("t")
        self.assertIsNotNone(kanban.get_card(cid))

    def test_board_summary(self):
        kanban.create_card("a", column="todo")
        kanban.create_card("b", column="doing")
        kanban.create_card("c", column="doing")
        s = kanban.board_summary()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["columns"]["todo"], 1)
        self.assertEqual(s["columns"]["doing"], 2)
        self.assertEqual(s["columns"]["done"], 0)

    def test_concurrent_creates(self):
        import threading
        ids = []
        lock = threading.Lock()

        def make(i):
            cid = kanban.create_card(f"card {i}")
            with lock:
                ids.append(cid)

        threads = [threading.Thread(target=make, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(ids)), 20)
        self.assertEqual(len(kanban.list_cards()), 20)


if __name__ == "__main__":
    unittest.main()
