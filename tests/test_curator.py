"""SkillForge Curator: pin/unpin, archive/restore, usage summary, prune.

These tests cover the curator lifecycle without touching the agent loop,
gateway, or tool dispatch — purely ForgeStore + skills data operations.
"""
import json
import multiprocessing
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


def _pin_worker(home, name, gate, queue):
    os.environ["ZELINE_HOME"] = home
    from zeline.skillforge import ForgeStore
    gate.wait()
    try:
        ForgeStore().pin(name)
        queue.put(None)
    except Exception as exc:
        queue.put(str(exc))

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


class CuratorTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.env = mock.patch.dict(os.environ, {
            "ZELINE_HOME": str(self.home / "state"),
            "ZELINE_API_KEY": "fixture-only",
            "ZELINE_MODEL": "fixture",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        # Force reimport with clean state
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                del sys.modules[name]
        self.skillforge = __import__("zeline.skillforge", fromlist=["ForgeStore"])
        self.skills = __import__("zeline.skills", fromlist=["manage_skill"])
        self.skills._ensure_dirs()
        self.store = self.skillforge.ForgeStore()

    def _create_private_skill(self, name, content="# Test\n> test skill"):
        result = self.skills.manage_skill("create", name=name, content=content)
        self.assertIn("OK", result)
        return result


class PinTests(CuratorTestBase):
    def test_pin_and_unpin_skill(self):
        self._create_private_skill("alpha")
        result = self.store.pin("alpha")
        self.assertIn("Pinned", result)
        self.assertTrue(self.store.is_pinned("alpha"))
        self.assertIn("alpha", self.store.pinned())

        result = self.store.unpin("alpha")
        self.assertIn("Unpinned", result)
        self.assertFalse(self.store.is_pinned("alpha"))
        self.assertNotIn("alpha", self.store.pinned())

    def test_pin_nonexistent_skill_raises(self):
        with self.assertRaises(ValueError):
            self.store.pin("nonexistent")

    def test_unpin_nonexistent_is_safe(self):
        # Unpin a skill that was never pinned — should not raise
        result = self.store.unpin("never-pinned")
        self.assertIn("Unpinned", result)

    def test_pinned_skill_refuses_delete(self):
        self._create_private_skill("beta")
        self.store.pin("beta")
        result = self.skills.manage_skill("delete", name="beta")
        self.assertIn("pinned", result.lower())
        # Skill still exists
        self.assertIsNotNone(self.skills._locate_unit("beta"))

    def test_pin_cannot_succeed_during_inflight_delete(self):
        self._create_private_skill("racing-delete")
        deleting = threading.Event()
        release = threading.Event()
        pin_done = threading.Event()
        outcomes = []
        def checkpoint(*_args):
            deleting.set()
            release.wait(5)
        def pin():
            try:
                outcomes.append(self.store.pin("racing-delete"))
            except ValueError as exc:
                outcomes.append(str(exc))
            finally:
                pin_done.set()
        with mock.patch.object(self.skills, "_checkpoint_skill_tree", side_effect=checkpoint):
            remover = threading.Thread(target=lambda: self.skills.manage_skill("delete", name="racing-delete"))
            pinner = threading.Thread(target=pin)
            remover.start()
            try:
                self.assertTrue(deleting.wait(5))
                pinner.start()
                self.assertFalse(pin_done.wait(.2), "pin reported success while delete was still in progress")
            finally:
                release.set()
                remover.join(5)
                if pinner.ident is not None:
                    pinner.join(5)
        self.assertTrue(pin_done.is_set())
        self.assertIn("not found", outcomes[0])
        self.assertFalse(self.store.is_pinned("racing-delete"))

    def test_delete_does_not_report_success_on_filesystem_failure(self):
        self._create_private_skill("cannot-delete")
        def denied(_path, **kwargs):
            if not kwargs.get("ignore_errors"):
                raise PermissionError("blocked")
        with mock.patch.object(self.skills.shutil, "rmtree", side_effect=denied):
            result = self.skills.manage_skill("delete", name="cannot-delete")
        self.assertIn("ERROR", result)
        self.assertTrue((self.skills.PRIVATE_SKILLS_DIR / "cannot-delete").exists())

    def test_unpinned_skill_allows_delete(self):
        self._create_private_skill("gamma")
        self.store.pin("gamma")
        self.store.unpin("gamma")
        result = self.skills.manage_skill("delete", name="gamma")
        self.assertIn("Deleted", result)

    def test_pins_persist_across_store_instances(self):
        self._create_private_skill("delta")
        self.store.pin("delta")
        store2 = self.skillforge.ForgeStore()
        self.assertTrue(store2.is_pinned("delta"))

    def test_corrupt_registry_blocks_deletion_and_archive(self):
        self._create_private_skill("protected")
        pins = self.store._pins_path()
        pins.write_text('{broken', encoding="utf-8")
        self.assertIn("ERROR", self.skills.manage_skill("delete", name="protected"))
        with self.assertRaises(ValueError):
            self.store.archive("protected")
        with self.assertRaises(ValueError):
            self.store.unpin("protected")
        self.assertIsNotNone(self.skills._locate_unit("protected"))
        self.assertEqual(pins.read_text(encoding="utf-8"), '{broken')

    def test_missing_registry_is_empty_but_wrong_schema_fails_closed(self):
        self._create_private_skill("protected")
        self.assertFalse(self.store.is_pinned("protected"))
        for value in ('{}', '["protected", 42]', '["../outside"]'):
            with self.subTest(value=value):
                self.store._pins_path().write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.store.is_pinned("protected")

    def test_registry_read_error_blocks_deletion(self):
        self._create_private_skill("protected")
        self.store.pin("protected")
        path = self.store._pins_path()
        with mock.patch.object(Path, "read_text", autospec=True, side_effect=OSError("disk unavailable")):
            self.assertIn("ERROR", self.skills.manage_skill("delete", name="protected"))
        self.assertTrue(path.exists())
        self.assertIsNotNone(self.skills._locate_unit("protected"))

    def test_pin_write_failure_preserves_previous_registry(self):
        self._create_private_skill("alpha")
        self._create_private_skill("beta")
        self.store.pin("alpha")
        before = self.store._pins_path().read_bytes()
        with mock.patch.object(os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.pin("beta")
        self.assertEqual(self.store._pins_path().read_bytes(), before)

    def test_parallel_process_pins_do_not_lose_updates(self):
        names = [f"parallel-{index}" for index in range(8)]
        for name in names:
            self._create_private_skill(name)
        ctx = multiprocessing.get_context("fork") if "fork" in multiprocessing.get_all_start_methods() else multiprocessing.get_context()
        gate, queue = ctx.Event(), ctx.Queue()
        processes = [ctx.Process(target=_pin_worker, args=(str(self.home / "state"), name, gate, queue)) for name in names]
        for process in processes:
            process.start()
        gate.set()
        for process in processes:
            process.join(timeout=15)
            if process.is_alive():
                process.terminate()
                process.join()
            self.assertEqual(process.exitcode, 0)
        self.assertEqual([queue.get(timeout=2) for _ in processes], [None] * len(processes))
        self.assertEqual(set(self.store.pinned()), set(names))


class ArchiveTests(CuratorTestBase):
    def _race_restore_with_author(self, name, flat, action, **kwargs):
        if flat:
            (self.skills.PRIVATE_SKILLS_DIR / f"{name}.md").write_text("# Archived evidence\n")
        else:
            self._create_private_skill(name, "# Archived evidence\n")
        self.store.archive(name)
        moving = threading.Event()
        release = threading.Event()
        author_started = threading.Event()
        author_done = threading.Event()
        outcomes = []
        errors = []
        real_move = self.skillforge.shutil.move

        def move(*args, **kw):
            moving.set()
            if not release.wait(5):
                raise RuntimeError("restore race release timed out")
            return real_move(*args, **kw)

        def restore():
            try:
                outcomes.append(self.store.restore(name))
            except Exception as exc:
                errors.append(exc)

        def author():
            author_started.set()
            try:
                outcomes.append(self.skills.manage_skill(action, name=name, **kwargs))
            except Exception as exc:
                errors.append(exc)
            finally:
                author_done.set()

        with mock.patch.object(self.skillforge.shutil, "move", side_effect=move):
            restorer = threading.Thread(target=restore, daemon=True)
            writer = threading.Thread(target=author, daemon=True)
            restorer.start()
            try:
                self.assertTrue(moving.wait(5))
                writer.start()
                self.assertTrue(author_started.wait(5))
                self.assertFalse(author_done.wait(.2), "author bypassed in-flight restore lock")
            finally:
                release.set()
                restorer.join(5)
                if writer.ident is not None:
                    writer.join(5)
        self.assertFalse(restorer.is_alive(), "restore deadlocked")
        self.assertFalse(writer.is_alive(), "author deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 2)
        folder = self.skills.PRIVATE_SKILLS_DIR / name
        self.assertFalse((folder / name).exists(), "restore nested the archived directory")
        self.assertFalse((self.skills.PRIVATE_SKILLS_DIR / f"{name}.md").exists())
        return folder

    def test_patch_and_apply_wait_for_shared_mutation_lock(self):
        self._create_private_skill("locked-edit", "# Original evidence")
        failures = []
        for operation in (
            lambda: self.skills.manage_skill("patch", name="locked-edit", old_text="Original", new_text="Patched"),
            lambda: self.store.apply("missing-proposal"),
        ):
            started = threading.Event()
            done = threading.Event()
            outcomes = []

            def writer():
                started.set()
                try:
                    outcomes.append(operation())
                except ValueError as exc:
                    outcomes.append(str(exc))
                finally:
                    done.set()

            with self.store._pins_lock():
                worker = threading.Thread(target=writer, daemon=True)
                worker.start()
                self.assertTrue(started.wait(5))
                blocked = not done.wait(.2)
            worker.join(5)
            self.assertFalse(worker.is_alive(), "writer deadlocked")
            self.assertEqual(len(outcomes), 1)
            if not blocked:
                failures.append(outcomes[0])
        self.assertEqual(failures, [], "writers validated or mutated outside shared lock")

    def test_directory_restore_serializes_with_create(self):
        folder = self._race_restore_with_author("restore-create", False, "create", content="# New evidence")
        self.assertIn("New evidence", (folder / "SKILL.md").read_text())

    def test_flat_restore_serializes_with_write_file(self):
        folder = self._race_restore_with_author("restore-write", True, "write_file",
                                                file_path="references/proof.md", content="companion evidence")
        self.assertIn("Archived evidence", (folder / "SKILL.md").read_text())
        self.assertEqual((folder / "references/proof.md").read_text(), "companion evidence")

    def test_pin_cannot_succeed_while_archive_move_is_in_progress(self):
        self._create_private_skill("racing-archive")
        moving = threading.Event()
        release = threading.Event()
        pin_done = threading.Event()
        outcomes = []
        original_move = self.store.__class__.__module__
        forge_module = sys.modules[original_move]
        real_move = forge_module.shutil.move
        def move(*args, **kwargs):
            moving.set()
            release.wait(5)
            return real_move(*args, **kwargs)
        def pin():
            try:
                outcomes.append(self.store.pin("racing-archive"))
            except ValueError as exc:
                outcomes.append(str(exc))
            finally:
                pin_done.set()
        with mock.patch.object(forge_module.shutil, "move", side_effect=move):
            archiver = threading.Thread(target=lambda: self.store.archive("racing-archive"))
            pinner = threading.Thread(target=pin)
            archiver.start()
            try:
                self.assertTrue(moving.wait(5))
                pinner.start()
                self.assertFalse(pin_done.wait(.2), "pin succeeded during archive")
            finally:
                release.set()
                archiver.join(5)
                if pinner.ident is not None:
                    pinner.join(5)
        self.assertIn("not found", outcomes[0])
        self.assertFalse(self.store.is_pinned("racing-archive"))

    def test_concurrent_restore_does_not_move_same_archive_twice(self):
        self._create_private_skill("restore-race")
        self.store.archive("restore-race")
        forge_module = sys.modules[self.store.__class__.__module__]
        real_move = forge_module.shutil.move
        first_move = threading.Event()
        second_move = threading.Event()
        release = threading.Event()
        outcomes = []
        def move(*args, **kwargs):
            if first_move.is_set():
                second_move.set()
            first_move.set()
            release.wait(5)
            return real_move(*args, **kwargs)
        def restore():
            try:
                outcomes.append(self.store.restore("restore-race"))
            except (ValueError, OSError) as exc:
                outcomes.append(str(exc))
        with mock.patch.object(forge_module.shutil, "move", side_effect=move):
            first = threading.Thread(target=restore)
            second = threading.Thread(target=restore)
            first.start()
            try:
                self.assertTrue(first_move.wait(5))
                second.start()
                self.assertFalse(second_move.wait(.2), "two restore operations passed the same destination check")
            finally:
                release.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
        self.assertEqual(sum(item.startswith("Restored") for item in outcomes), 1)
        self.assertTrue((self.skills.PRIVATE_SKILLS_DIR / "restore-race" / "SKILL.md").is_file())

    def test_restore_rejects_malformed_flat_wrapper_before_moving_file(self):
        self.skills._ensure_dirs()
        flat = self.skills.PRIVATE_SKILLS_DIR / "bad-flat.md"
        flat.write_text("# Flat skill", encoding="utf-8")
        self.store.archive("bad-flat")
        archived = self.store._archive_dir() / "bad-flat"
        (archived / "unexpected-directory").mkdir()
        with self.assertRaises((ValueError, OSError)):
            self.store.restore("bad-flat")
        self.assertFalse(flat.exists(), "failed restore must not already have moved the skill")
        self.assertTrue((archived / "bad-flat.md").is_file())

    def test_archive_and_restore(self):
        self._create_private_skill("archivable")
        result = self.store.archive("archivable")
        self.assertIn("Archived", result)
        self.assertIn("archivable", self.store.archived())
        # Skill no longer active
        self.assertIsNone(self.skills._locate_unit("archivable"))

        result = self.store.restore("archivable")
        self.assertIn("Restored", result)
        self.assertNotIn("archivable", self.store.archived())
        # Skill is active again
        self.assertIsNotNone(self.skills._locate_unit("archivable"))

    def test_archive_pinned_skill_raises(self):
        self._create_private_skill("pinned-one")
        self.store.pin("pinned-one")
        with self.assertRaises(ValueError) as ctx:
            self.store.archive("pinned-one")
        self.assertIn("pinned", str(ctx.exception).lower())

    def test_archive_public_skill_raises(self):
        # Public skills cannot be archived
        with self.assertRaises(ValueError) as ctx:
            self.store.archive("zeline-zenith-z0")
        # Either "not found" or "bundled/public"
        self.assertTrue("not found" in str(ctx.exception).lower()
                        or "public" in str(ctx.exception).lower())

    def test_archive_nonexistent_raises(self):
        with self.assertRaises(ValueError):
            self.store.archive("does-not-exist")

    def test_restore_nonexistent_raises(self):
        with self.assertRaises(ValueError):
            self.store.restore("never-archived")

    def test_double_archive_raises(self):
        self._create_private_skill("double")
        self.store.archive("double")
        with self.assertRaises(ValueError) as ctx:
            self.store.archive("double")
        # Second attempt fails — skill is no longer in active scope
        msg = str(ctx.exception).lower()
        self.assertTrue("not found" in msg or "already archived" in msg)

    def test_restore_when_active_raises(self):
        self._create_private_skill("conflict")
        self.store.archive("conflict")
        self._create_private_skill("conflict")
        with self.assertRaises(ValueError) as ctx:
            self.store.restore("conflict")
        self.assertIn("already exists", str(ctx.exception).lower())

    def test_restore_private_override_even_if_public_exists(self):
        self._create_private_skill("override")
        public = self.skills.PUBLIC_SKILLS_DIR / "override.md"
        public.write_text("# Public\n", encoding="utf-8")
        self.store.archive("override")
        self.assertEqual(self.skills._locate_unit("override")[0], "public")
        self.store.restore("override")
        self.assertEqual(self.skills._locate_unit("override")[0], "private")
        self.assertTrue(public.exists())

    def test_restore_rejects_empty_private_target(self):
        self._create_private_skill("collision")
        self.store.archive("collision")
        target = self.skills.PRIVATE_SKILLS_DIR / "collision"
        target.mkdir()
        with self.assertRaises(ValueError):
            self.store.restore("collision")
        self.assertTrue((self.store._archive_dir() / "collision" / "SKILL.md").is_file())
        self.assertFalse((target / "collision" / "SKILL.md").exists())

    def test_restore_flat_skill_rejects_existing_private_flat_target(self):
        flat = self.skills.PRIVATE_SKILLS_DIR / "flat.md"
        flat.write_text("# Original\n", encoding="utf-8")
        self.store.archive("flat")
        flat.write_text("# New\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.restore("flat")
        self.assertEqual(flat.read_text(encoding="utf-8"), "# New\n")
        self.assertIn("flat", self.store.archived())


class UsageSummaryTests(CuratorTestBase):
    def test_empty_usage(self):
        result = self.store.usage_summary()
        self.assertEqual(result, [])

    def test_usage_with_records(self):
        now = time.time()
        rev = "a" * 64
        self.store.record_usage("u1", "t1", "alpha", "private", rev, "success", now=now)
        self.store.record_usage("u1", "t2", "alpha", "private", rev, "failure", now=now - 100)
        self.store.record_usage("u1", "t3", "alpha", "private", rev, "success", now=now - 200)

        result = self.store.usage_summary("alpha", now=now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "alpha")
        self.assertEqual(result[0]["total"], 3)
        self.assertEqual(result[0]["success"], 2)
        self.assertEqual(result[0]["failure"], 1)
        self.assertEqual(result[0]["success_rate"], 66.7)

    def test_usage_all_skills(self):
        now = time.time()
        rev = "b" * 64
        self.store.record_usage("u1", "t1", "alpha", "private", rev, "success", now=now)
        self.store.record_usage("u1", "t2", "beta", "private", rev, "failure", now=now)

        result = self.store.usage_summary(now=now)
        self.assertEqual(len(result), 2)
        names = {r["name"] for r in result}
        self.assertEqual(names, {"alpha", "beta"})

    def test_usage_respects_days_window(self):
        now = time.time()
        rev = "c" * 64
        # Old record outside window
        self.store.record_usage("u1", "t1", "old-skill", "private", rev, "success",
                                now=now - 100 * 86400)
        result = self.store.usage_summary("old-skill", days=90, now=now)
        self.assertEqual(result, [])

    def test_usage_rejects_non_positive_window(self):
        for days in (0, -1, True, 36_501):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.store.usage_summary(days=days)


class PruneTests(CuratorTestBase):
    def test_prune_dry_run(self):
        self._create_private_skill("stale-skill")
        # Backdate mtime to simulate staleness
        skill_path = self.skills.PRIVATE_SKILLS_DIR / "stale-skill" / "SKILL.md"
        old_time = time.time() - 200 * 86400
        os.utime(skill_path, (old_time, old_time))

        result = self.store.prune(stale_days=180, dry_run=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "stale-skill")
        self.assertEqual(result[0]["action"], "would_archive")
        # Skill still exists (dry run)
        self.assertIsNotNone(self.skills._locate_unit("stale-skill"))

    def test_prune_execute(self):
        self._create_private_skill("dead-skill")
        skill_path = self.skills.PRIVATE_SKILLS_DIR / "dead-skill" / "SKILL.md"
        old_time = time.time() - 200 * 86400
        os.utime(skill_path, (old_time, old_time))

        result = self.store.prune(stale_days=180, dry_run=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["action"], "archived")
        # Skill moved to archive
        self.assertIsNone(self.skills._locate_unit("dead-skill"))
        self.assertIn("dead-skill", self.store.archived())

    def test_prune_skips_pinned(self):
        self._create_private_skill("pinned-old")
        self.store.pin("pinned-old")
        skill_path = self.skills.PRIVATE_SKILLS_DIR / "pinned-old" / "SKILL.md"
        old_time = time.time() - 200 * 86400
        os.utime(skill_path, (old_time, old_time))

        result = self.store.prune(stale_days=180, dry_run=False)
        self.assertEqual(result, [])
        # Skill still exists
        self.assertIsNotNone(self.skills._locate_unit("pinned-old"))

    def test_prune_skips_recently_used(self):
        self._create_private_skill("used-old")
        skill_path = self.skills.PRIVATE_SKILLS_DIR / "used-old" / "SKILL.md"
        now = time.time()
        old_time = now - 200 * 86400
        os.utime(skill_path, (old_time, old_time))
        # Record recent usage
        rev = "d" * 64
        self.store.record_usage("u1", "t1", "used-old", "private", rev, "success", now=now - 10)

        result = self.store.prune(stale_days=180, dry_run=True, now=now)
        self.assertEqual(result, [])

    def test_prune_skips_recent_skills(self):
        self._create_private_skill("fresh-skill")
        # mtime is now, so it's not stale
        result = self.store.prune(stale_days=180, dry_run=True)
        self.assertEqual(result, [])

    def test_prune_uses_newest_file_in_skill_tree(self):
        self._create_private_skill("maintained-skill")
        skill_dir = self.skills.PRIVATE_SKILLS_DIR / "maintained-skill"
        reference = skill_dir / "references" / "notes.md"
        reference.parent.mkdir()
        reference.write_text("current", encoding="utf-8")
        old_time = time.time() - 200 * 86400
        os.utime(skill_dir / "SKILL.md", (old_time, old_time))
        self.assertEqual(self.store.prune(stale_days=180, dry_run=True), [])

    def test_prune_rejects_unsafe_windows_and_minimums(self):
        for days in (0, -1, True, 36_501):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.store.prune(stale_days=days)
        for minimum in (-1, True):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                self.store.prune(min_uses=minimum)


class CLITests(CuratorTestBase):
    def _run(self, *args):
        import argparse
        parser = argparse.ArgumentParser()
        self.skillforge.add_arguments(parser)
        parsed = parser.parse_args(list(args))
        return self.skillforge.command(parsed)

    def test_pin_unpin_pinned_cli(self):
        self._create_private_skill("cli-pin")
        self.assertEqual(self._run("pin", "cli-pin"), 0)
        self.assertEqual(self._run("pinned"), 0)
        self.assertEqual(self._run("unpin", "cli-pin"), 0)

    def test_archive_restore_archived_cli(self):
        self._create_private_skill("cli-arch")
        self.assertEqual(self._run("archive", "cli-arch"), 0)
        self.assertEqual(self._run("archived"), 0)
        self.assertEqual(self._run("restore", "cli-arch"), 0)

    def test_usage_cli(self):
        self.assertEqual(self._run("usage"), 0)
        self.assertEqual(self._run("usage", "--days", "30"), 0)

    def test_prune_dry_run_cli(self):
        self.assertEqual(self._run("prune"), 0)


if __name__ == "__main__":
    unittest.main()
