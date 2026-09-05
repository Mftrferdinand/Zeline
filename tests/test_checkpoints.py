"""Contract tests for file checkpoints and `zeline undo`.

Three invariants carry the weight:

- **A snapshot never blocks a write.** If the store is broken, the write still
  lands. A safety net that refuses work is worse than no safety net.
- **An undo is itself undoable.** Restoring snapshots the current bytes first,
  so an operator who undoes the wrong thing is not stranded.
- **The store is bounded and private.** Snapshots hold source content, so the
  directory is 0700, blobs are 0600, and the oldest entries are pruned.

Also pinned: a brand-new file produces no checkpoint (there is no previous
content to restore), and a rejected edit does not create one.
"""
from __future__ import annotations

import importlib
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    cfg = importlib.import_module("zeline.config")
    checkpoints = importlib.import_module("zeline.checkpoints")
    tools = importlib.import_module("zeline.tools")
    cli = importlib.import_module("zeline.cli")
    return cfg, checkpoints, tools, cli


class CheckpointBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir(parents=True)
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.checkpoints, self.tools, self.cli = fresh(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()

    def file(self, name: str, content: str) -> Path:
        target = self.work / name
        target.write_text(content, encoding="utf-8")
        return target


class SnapshotTests(CheckpointBase):
    def test_snapshot_captures_previous_bytes(self):
        target = self.file("a.py", "original\n")
        cid = self.checkpoints.snapshot(target)
        self.assertIsNotNone(cid)
        target.write_text("clobbered\n", encoding="utf-8")
        ok, message = self.checkpoints.restore(cid)
        self.assertTrue(ok, message)
        self.assertEqual(target.read_text(encoding="utf-8"), "original\n")

    def test_new_file_has_nothing_to_snapshot(self):
        """A file that does not exist yet has no previous content."""
        self.assertIsNone(self.checkpoints.snapshot(self.work / "missing.py"))

    def test_oversized_file_is_skipped(self):
        target = self.file("big.bin", "x")
        with mock.patch.object(self.checkpoints, "MAX_SNAPSHOT_BYTES", 0):
            self.assertIsNone(self.checkpoints.snapshot(target))

    def test_disabled_by_config(self):
        saved = self.config.config_copy()
        saved["tools"]["checkpoints"] = False
        self.config.save_config(saved)
        self.assertFalse(self.checkpoints.enabled())
        self.assertIsNone(self.checkpoints.snapshot(self.file("a.py", "x")))

    def test_a_broken_store_returns_none_and_does_not_raise(self):
        target = self.file("a.py", "x")
        with mock.patch.object(self.checkpoints.shutil, "copy2", side_effect=OSError("full disk")):
            self.assertIsNone(self.checkpoints.snapshot(target))

    def test_store_permissions_are_private(self):
        target = self.file("a.py", "secret\n")
        cid = self.checkpoints.snapshot(target)
        self.assertIsNotNone(cid)
        if os.name == "posix":
            root = self.config.DATA_DIR / "checkpoints"
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            blob = root / f"{cid}.blob"
            self.assertEqual(stat.S_IMODE(blob.stat().st_mode), 0o600)

    def test_pruning_keeps_the_cap_and_deletes_blobs(self):
        target = self.file("a.py", "x")
        with mock.patch.object(self.checkpoints, "MAX_SNAPSHOTS", 3):
            ids = [self.checkpoints.snapshot(target, reason=f"r{i}") for i in range(6)]
        entries = self.checkpoints.list_checkpoints()
        self.assertEqual(len(entries), 3)
        root = self.config.DATA_DIR / "checkpoints"
        self.assertFalse((root / f"{ids[0]}.blob").exists())
        self.assertTrue((root / f"{ids[-1]}.blob").exists())

    def test_corrupt_index_is_treated_as_empty_not_fatal(self):
        self.checkpoints.snapshot(self.file("a.py", "x"))
        (self.config.DATA_DIR / "checkpoints" / "index.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.checkpoints.list_checkpoints(), [])
        self.assertIsNotNone(self.checkpoints.snapshot(self.file("b.py", "y")))


class RestoreTests(CheckpointBase):
    def test_restore_is_itself_undoable(self):
        target = self.file("a.py", "v1\n")
        first = self.checkpoints.snapshot(target)
        target.write_text("v2\n", encoding="utf-8")
        self.checkpoints.restore(first)
        self.assertEqual(target.read_text(encoding="utf-8"), "v1\n")
        # The pre-restore snapshot holds v2, so the undo can be undone.
        pre = [e for e in self.checkpoints.list_checkpoints() if e["reason"] == "pre-restore"]
        self.assertTrue(pre)
        ok, _ = self.checkpoints.restore(str(pre[0]["id"]))
        self.assertTrue(ok)
        self.assertEqual(target.read_text(encoding="utf-8"), "v2\n")

    def test_unknown_id_reports_clearly(self):
        ok, message = self.checkpoints.restore("nope")
        self.assertFalse(ok)
        self.assertIn("nope", message)

    def test_missing_blob_reports_clearly(self):
        cid = self.checkpoints.snapshot(self.file("a.py", "x"))
        (self.config.DATA_DIR / "checkpoints" / f"{cid}.blob").unlink()
        ok, message = self.checkpoints.restore(cid)
        self.assertFalse(ok)
        self.assertIn("no stored content", message)

    def test_restore_recreates_a_deleted_file(self):
        target = self.file("a.py", "alive\n")
        cid = self.checkpoints.snapshot(target)
        target.unlink()
        ok, _ = self.checkpoints.restore(cid)
        self.assertTrue(ok)
        self.assertEqual(target.read_text(encoding="utf-8"), "alive\n")

    def test_clear_removes_everything(self):
        self.checkpoints.snapshot(self.file("a.py", "x"))
        self.assertGreater(self.checkpoints.clear(), 0)
        self.assertEqual(self.checkpoints.list_checkpoints(), [])


class ListingTests(CheckpointBase):
    def test_listing_is_newest_first(self):
        target = self.file("a.py", "x")
        old = self.checkpoints.snapshot(target, reason="old")
        time.sleep(0.01)
        new = self.checkpoints.snapshot(target, reason="new")
        entries = self.checkpoints.list_checkpoints()
        self.assertEqual(entries[0]["id"], new)
        self.assertEqual(entries[-1]["id"], old)

    def test_filter_by_file(self):
        a = self.file("a.py", "x")
        b = self.file("b.py", "y")
        self.checkpoints.snapshot(a)
        self.checkpoints.snapshot(b)
        entries = self.checkpoints.list_checkpoints(a)
        self.assertEqual(len(entries), 1)
        # The stored path is deliberately canonical, not whatever spelling the
        # caller happened to use, so compare against the canonical form. On
        # macOS str(a) is /var/... while the resolved path is /private/var/...,
        # and on Windows it is RUNNER~1 versus runneradmin.
        self.assertEqual(entries[0]["path"], str(self.checkpoints._normalize(a)))

    def test_diff_preview_shows_the_change(self):
        target = self.file("a.py", "before\n")
        cid = self.checkpoints.snapshot(target)
        target.write_text("after\n", encoding="utf-8")
        text = self.checkpoints.diff_preview(cid)
        self.assertIn("-before", text)
        self.assertIn("+after", text)

    def test_diff_preview_on_unchanged_file(self):
        cid = self.checkpoints.snapshot(self.file("a.py", "same\n"))
        self.assertIn("no differences", self.checkpoints.diff_preview(cid))

    def test_diff_preview_unknown_id(self):
        self.assertIn("no checkpoint", self.checkpoints.diff_preview("nope"))

    def test_a_file_has_one_identity_regardless_of_how_it_is_spelled(self):
        """Windows 8.3 short names and macOS /var -> /private/var both bite here.

        The tool layer resolves paths before writing while a caller may not, so
        storing the raw string made two spellings of one file look like two
        files -- a checkpoint taken by write_file could not then be found by
        path. Reproduced portably with an unresolved '..' segment.
        """
        target = self.file("a.py", "x\n")
        indirect = self.work / "sub" / ".." / "a.py"
        cid = self.checkpoints.snapshot(indirect)
        self.assertIsNotNone(cid)
        self.assertEqual(len(self.checkpoints.list_checkpoints(target)), 1)
        self.assertEqual(len(self.checkpoints.list_checkpoints(indirect)), 1)


class ToolIntegrationTests(CheckpointBase):
    def test_write_file_snapshots_the_previous_content(self):
        target = self.file("a.py", "old\n")
        result = self.tools._write_file("a.py", "new\n", self.work)
        self.assertTrue(result.startswith("OK"), result)
        self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
        entries = self.checkpoints.list_checkpoints(target)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["reason"], "write_file")
        self.checkpoints.restore(str(entries[0]["id"]))
        self.assertEqual(target.read_text(encoding="utf-8"), "old\n")

    def test_edit_file_snapshots_the_previous_content(self):
        target = self.file("a.py", "value = 1\n")
        result = self.tools._edit_file("a.py", "value = 1", "value = 2", self.work)
        self.assertTrue(result.startswith("OK"), result)
        entries = self.checkpoints.list_checkpoints(target)
        self.assertEqual(entries[0]["reason"], "edit_file")
        self.checkpoints.restore(str(entries[0]["id"]))
        self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")

    def test_a_rejected_edit_creates_no_checkpoint(self):
        """A non-unique old_text writes nothing, so there is nothing to snapshot."""
        target = self.file("a.py", "x = 1\nx = 1\n")
        result = self.tools._edit_file("a.py", "x = 1", "x = 2", self.work)
        self.assertTrue(result.startswith("ERROR"), result)
        self.assertEqual(self.checkpoints.list_checkpoints(target), [])

    def test_creating_a_new_file_creates_no_checkpoint(self):
        result = self.tools._write_file("brand_new.py", "hello\n", self.work)
        self.assertTrue(result.startswith("OK"), result)
        self.assertEqual(self.checkpoints.list_checkpoints(), [])

    def test_a_failing_snapshot_never_blocks_the_write(self):
        """The whole point: losing the net must not lose the work."""
        target = self.file("a.py", "old\n")
        with (
            mock.patch.object(self.tools.checkpoints, "snapshot", side_effect=OSError("boom")),
            self.assertRaises(OSError),
        ):
            self.tools.checkpoints.snapshot(target)
        with mock.patch.object(self.tools.checkpoints, "snapshot", return_value=None):
            result = self.tools._write_file("a.py", "new\n", self.work)
        self.assertTrue(result.startswith("OK"), result)
        self.assertEqual(target.read_text(encoding="utf-8"), "new\n")


class AgentUndoTests(CheckpointBase):
    def test_agent_can_list_preview_and_restore_its_workspace_checkpoint(self):
        target = self.file("a.py", "before\n")
        executor = self.tools.ToolExecutor("telegram:owner", profile="workspace", workspace=str(self.work))
        self.assertTrue(self.tools._write_file("a.py", "after\n", self.work).startswith("OK"))

        listed = executor.run("undo_file", {"action": "list"})
        self.assertIn("checkpoint(s)", listed)
        checkpoint_id = self.checkpoints.list_checkpoints(workspace=self.work)[0]["id"]
        preview = executor.run("undo_file", {"action": "diff", "checkpoint_id": checkpoint_id})
        self.assertIn("before", preview)
        restored = executor.run("undo_file", {"action": "restore", "checkpoint_id": checkpoint_id})
        self.assertIn("restored", restored)
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")

    def test_agent_undo_cannot_restore_a_checkpoint_outside_its_workspace(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        target = outside / "secret.py"
        target.write_text("secret\n", encoding="utf-8")
        checkpoint_id = self.checkpoints.snapshot(target)
        target.write_text("changed\n", encoding="utf-8")
        executor = self.tools.ToolExecutor("telegram:owner", profile="workspace", workspace=str(self.work))

        listed = executor.run("undo_file", {"action": "list"})
        self.assertIn("no checkpoints", listed)
        denied = executor.run("undo_file", {"action": "restore", "checkpoint_id": checkpoint_id})
        self.assertIn("no checkpoint", denied)
        self.assertEqual(target.read_text(encoding="utf-8"), "changed\n")

    def test_undo_file_is_not_exposed_to_the_public_safe_profile(self):
        executor = self.tools.ToolExecutor("telegram:public", profile="safe", workspace=str(self.work))
        denied = executor.run("undo_file", {"action": "list"})
        self.assertIn("not allowed for profile", denied)


class CliUndoTests(CheckpointBase):
    def test_undo_with_no_checkpoints_explains_itself(self):
        self.assertEqual(self.cli.cmd_undo(), 0)
        self.assertEqual(self.cli.cmd_undo(show_list=True), 0)

    def test_undo_without_an_id_restores_the_newest(self):
        target = self.file("a.py", "v1\n")
        self.tools._write_file("a.py", "v2\n", self.work)
        self.assertEqual(self.cli.cmd_undo(), 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "v1\n")

    def test_undo_with_an_id_restores_that_one(self):
        target = self.file("a.py", "v1\n")
        cid = self.checkpoints.snapshot(target)
        target.write_text("v9\n", encoding="utf-8")
        self.assertEqual(self.cli.cmd_undo(cid), 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "v1\n")

    def test_undo_with_a_bad_id_exits_nonzero(self):
        self.assertEqual(self.cli.cmd_undo("nope"), 1)

    def test_undo_list_and_diff_and_clear(self):
        target = self.file("a.py", "v1\n")
        cid = self.checkpoints.snapshot(target)
        target.write_text("v2\n", encoding="utf-8")
        self.assertEqual(self.cli.cmd_undo(show_list=True), 0)
        self.assertEqual(self.cli.cmd_undo(file_path=str(target), show_list=True), 0)
        self.assertEqual(self.cli.cmd_undo(diff=cid), 0)
        self.assertEqual(self.cli.cmd_undo(clear=True), 0)
        self.assertEqual(self.checkpoints.list_checkpoints(), [])

    def test_undo_honours_the_disabled_flag(self):
        saved = self.config.config_copy()
        saved["tools"]["checkpoints"] = False
        self.config.save_config(saved)
        self.assertEqual(self.cli.cmd_undo(), 0)

    def test_cli_exposes_undo_flags(self):
        parser = self.cli.build_parser()
        namespace = parser.parse_args(["undo", "--list"])
        self.assertEqual(namespace.command, "undo")
        self.assertTrue(namespace.show_list)
        self.assertEqual(parser.parse_args(["undo", "abc"]).checkpoint_id, "abc")
        self.assertEqual(parser.parse_args(["undo", "--diff", "abc"]).diff, "abc")
        self.assertTrue(parser.parse_args(["undo", "--clear"]).clear)
        self.assertEqual(parser.parse_args(["undo", "--file", "x.py"]).file_path, "x.py")

    def test_age_formatting_scales(self):
        now = time.time()
        self.assertIn("s ago", self.cli._format_checkpoint_age(now - 5))
        self.assertIn("m ago", self.cli._format_checkpoint_age(now - 300))
        self.assertIn("h ago", self.cli._format_checkpoint_age(now - 7200))
        self.assertIn("d ago", self.cli._format_checkpoint_age(now - 200000))


if __name__ == "__main__":
    unittest.main()
