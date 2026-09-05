"""Contract tests for the persistent task board.

``update_task`` used to validate its arguments and return them as JSON. Nothing was
stored: the Telegram feed rendered a tidy ``📋 Updating tasks`` line for data that was
discarded the moment the tool returned, so the agent's plan lived only in the message
history — the one place guaranteed to be truncated on long work.

These tests pin the three ways a board is lost (compaction, restart, ``/new``), the
matching rule that decides whether an update progresses a task or duplicates it, and
the isolation a multi-user gateway depends on.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


class TaskBoardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="zl-tasks-"))
        self._old = os.environ.get("ZELINE_HOME")
        os.environ["ZELINE_HOME"] = str(self.home)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)
        self.tasks = importlib.import_module("zeline.tasks")
        self.tools = importlib.import_module("zeline.tools")
        self.workspace = self.home / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.executor = self.tools.ToolExecutor(
            "telegram:4242", profile="full", workspace=str(self.workspace)
        )

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)
        for name in [n for n in list(sys.modules) if n == "zeline" or n.startswith("zeline.")]:
            sys.modules.pop(name, None)

    def call(self, task: str, status: str) -> str:
        return self.executor.run("update_task", {"task": task, "status": status})

    # -- the regression this module exists for
    def test_a_task_is_actually_stored(self):
        """The old implementation returned JSON and wrote nothing at all."""
        self.call("Run the tests", "in_progress")
        self.assertEqual(len(self.tasks.load("telegram:4242")), 1)
        self.assertTrue(self.tasks._path("telegram:4242").is_file())

    def test_the_reply_is_the_board_not_an_echo_of_the_arguments(self):
        """Echoing the input teaches the model nothing it did not already know."""
        self.call("First step", "completed")
        result = self.call("Second step", "in_progress")
        self.assertIn("[x] First step", result)
        self.assertIn("[>] Second step", result)
        self.assertIn("(1/2 completed)", result)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(result)

    # -- matching: progress a task, or duplicate it
    def test_rewording_updates_the_same_task(self):
        """A model rarely repeats a title byte-for-byte, so exact matching grows the board."""
        self.call("Write contract tests", "pending")
        self.call("write the contract tests", "in_progress")
        board = self.tasks.load("telegram:4242")
        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]["status"], "in_progress")
        # The newer wording is kept: it is what the model will use next time, so
        # storing the old title guarantees a mismatch on the following update.
        self.assertEqual(board[0]["task"], "write the contract tests")

    def test_word_order_does_not_create_a_duplicate(self):
        self.call("tests for the scheduler", "pending")
        self.call("scheduler tests", "completed")
        self.assertEqual(len(self.tasks.load("telegram:4242")), 1)

    def test_genuinely_different_tasks_stay_separate(self):
        """Fuzzy matching would silently merge these and lose one task.

        This is the reason matching is a normalised word SET rather than a
        similarity score: two task titles can differ by a single word and still be
        different work.
        """
        self.call("Write contract tests", "pending")
        self.call("Write installer tests", "pending")
        self.assertEqual(len(self.tasks.load("telegram:4242")), 2)

    def test_case_and_punctuation_are_ignored(self):
        self.call("Open the PR.", "pending")
        self.call("open the pr", "completed")
        board = self.tasks.load("telegram:4242")
        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]["status"], "completed")

    # -- survival
    def test_open_items_are_carried_into_a_rebuilt_session(self):
        """This is what survives a gateway restart, which happens constantly here."""
        self.call("Done thing", "completed")
        self.call("Half-done thing", "in_progress")
        self.call("Not started", "pending")
        block = self.tasks.prompt_block("telegram:4242")
        self.assertIn("[>] Half-done thing", block)
        self.assertIn("[ ] Not started", block)
        # A completed task in the prompt is noise that invites redoing finished work.
        self.assertNotIn("Done thing", block)
        self.assertIn("NOT yet done", block)

    def test_an_empty_board_injects_nothing(self):
        """An empty section in every system prompt is pure token cost."""
        self.assertEqual(self.tasks.prompt_block("telegram:4242"), "")
        self.call("All finished", "completed")
        self.assertEqual(self.tasks.prompt_block("telegram:4242"), "")

    def test_in_progress_sorts_before_pending(self):
        self.call("Later", "pending")
        self.call("Now", "in_progress")
        self.assertEqual(
            [item["task"] for item in self.tasks.open_items("telegram:4242")],
            ["Now", "Later"],
        )

    def test_a_compaction_digest_carries_the_open_plan(self):
        """Compaction fires during long work — exactly when the plan is needed."""
        compaction = importlib.import_module("zeline.compaction")
        self.call("Ship the tool", "in_progress")
        digest = compaction.compact(
            [{"role": "user", "content": "add the cron tool"}], "telegram:4242"
        )
        self.assertIsNotNone(digest)
        self.assertIn("Tasks still open", digest["content"])
        self.assertIn("[>] Ship the tool", digest["content"])

    def test_a_digest_without_open_tasks_is_unchanged(self):
        compaction = importlib.import_module("zeline.compaction")
        digest = compaction.compact(
            [{"role": "user", "content": "something"}], "telegram:4242"
        )
        self.assertNotIn("Tasks still open", digest["content"])

    def test_reset_clears_the_board_but_archives_unfinished_work(self):
        """`/new` is a deliberate reset; losing a half-done plan with no trace is not."""
        sessions = importlib.import_module("zeline.sessions")
        self.call("Finished", "completed")
        self.call("Still open", "in_progress")
        sessions.SessionStore().reset("telegram:4242")
        self.assertEqual(self.tasks.load("telegram:4242"), [])
        archived = self.tasks.archive_path().read_text(encoding="utf-8")
        self.assertIn("[in_progress] Still open", archived)
        # A completed task needs no rescue.
        self.assertNotIn("Finished", archived)

    # -- isolation and limits
    def test_two_identities_never_share_a_board(self):
        self.call("chat A work", "pending")
        self.tools.ToolExecutor(
            "telegram:999", profile="full", workspace=str(self.workspace)
        ).run("update_task", {"task": "chat B work", "status": "pending"})
        self.assertEqual(len(self.tasks.load("telegram:4242")), 1)
        self.assertEqual(len(self.tasks.load("telegram:999")), 1)
        self.assertEqual(self.tasks.load("telegram:999")[0]["task"], "chat B work")

    def test_the_filename_never_contains_the_chat_id(self):
        """A directory listing must not enumerate who talks to a public bot."""
        self.call("x", "pending")
        names = [path.name for path in self.tasks.tasks_dir().glob("*.json")]
        self.assertTrue(names)
        for name in names:
            self.assertNotIn("4242", name)
            self.assertNotIn("telegram", name)

    def test_a_full_board_drops_the_oldest_finished_item(self):
        """Refusing the write would freeze the board and make it silently wrong."""
        for index in range(self.tasks.MAX_ITEMS):
            self.call(f"task number {index}", "completed")
        result = self.call("one more thing", "in_progress")
        self.assertIn("board was full", result)
        board = self.tasks.load("telegram:4242")
        self.assertEqual(len(board), self.tasks.MAX_ITEMS)
        self.assertNotIn("task number 0", [item["task"] for item in board])
        self.assertIn("one more thing", [item["task"] for item in board])

    def test_a_board_full_of_unfinished_work_refuses_and_says_why(self):
        for index in range(self.tasks.MAX_ITEMS):
            self.call(f"task number {index}", "pending")
        result = self.call("one more thing", "pending")
        self.assertIn("ERROR task", result)
        self.assertIn("complete or cancel some", result)

    def test_rejections_name_the_valid_statuses(self):
        self.assertIn("empty description", self.call("", "pending"))
        rejected = self.call("x", "banana")
        self.assertIn("ERROR task", rejected)
        for status in self.tasks.STATUSES:
            self.assertIn(status, rejected)

    def test_a_corrupt_board_file_is_treated_as_empty_not_fatal(self):
        """A truncated write must not make every later turn fail."""
        self.call("x", "pending")
        self.tasks._path("telegram:4242").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.tasks.load("telegram:4242"), [])
        self.assertIn("[ ] fresh start", self.call("fresh start", "pending"))

    def test_unknown_and_malformed_entries_are_skipped_on_read(self):
        """A file written by a newer version must not brick an older one."""
        self.tasks._path("telegram:4242").parent.mkdir(parents=True, exist_ok=True)
        self.tasks._path("telegram:4242").write_text(
            json.dumps(
                [
                    {"task": "good", "status": "pending", "future_field": 1},
                    {"task": "", "status": "pending"},
                    {"task": "bad status", "status": "sideways"},
                    "not a dict",
                ]
            ),
            encoding="utf-8",
        )
        board = self.tasks.load("telegram:4242")
        self.assertEqual([item["task"] for item in board], ["good"])

    def test_the_board_file_is_owner_only(self):
        if os.name == "nt":
            self.skipTest("POSIX permissions")
        self.call("x", "pending")
        mode = self.tasks._path("telegram:4242").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_a_public_gateway_cannot_touch_the_board(self):
        for profile in ("safe", "workspace"):
            with self.subTest(profile=profile):
                denied = self.tools.ToolExecutor(
                    "telegram:public", profile=profile, workspace=str(self.workspace)
                ).run("update_task", {"task": "x", "status": "pending"})
                self.assertIn("not allowed for profile", denied.lower())


if __name__ == "__main__":
    unittest.main()
