"""Tests for the chat REPL slash commands and TUI wiring in zeline.cli.

Covers ``_handle_slash_command`` dispatch for every command registered in
``zeline.tui.DEFAULT_REGISTRY``, the module-level ``_cli_ask`` renderer
(arrow-key menu on a TTY via ``tui.select``, numbered list plus free text
with redirected stdin), and the rule that unknown ``/words`` never reach
the model.
"""
from __future__ import annotations

import contextlib
import io
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import cli  # noqa: E402
from zeline import tui  # noqa: E402


def run_slash(text):
    """Run the dispatcher, capturing printed output. Returns (action, output)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        action = cli._handle_slash_command(text)
    return action, buffer.getvalue()


class FakeUsageStore:
    def totals(self, since_day):
        return {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "calls": 3,
            "models": 1,
        }


class FakeSupervisor:
    def __init__(self, workers):
        self._workers = workers

    def list_workers(self):
        return self._workers


class FakeMemoryStore:
    def __init__(self, identity="cli:local"):
        self.identity = identity

    def records(self):
        return [{"text": "aes likes tea"}, {"text": "project deadline friday"}]

    def formatted(self):
        return "- aes likes tea\n- project deadline friday"

    def retrieve(self, query, k=5):
        if "tea" in query:
            return [{"text": "aes likes tea"}]
        return []


class SlashDispatchTests(unittest.TestCase):
    def test_passthrough_plain_text(self):
        action, output = run_slash("hello, how are you?")
        self.assertEqual(action, "passthrough")
        self.assertEqual(output, "")

    def test_passthrough_empty(self):
        action, output = run_slash("")
        self.assertEqual(action, "passthrough")
        self.assertEqual(output, "")

    def test_unknown_slash_command_stays_local(self):
        action, output = run_slash("/frobnicate now")
        self.assertEqual(action, "handled")
        self.assertIn("Unknown command", output)
        self.assertIn("'/frobnicate'", output)
        self.assertIn("/help", output)

    def test_bare_slash(self):
        action, output = run_slash("/")
        self.assertEqual(action, "handled")
        self.assertIn("Unknown command", output)

    def test_help(self):
        action, output = run_slash("/help")
        self.assertEqual(action, "handled")
        self.assertIn("Slash commands:", output)
        for name in ("help", "model", "status", "goals", "workers", "memory", "clear", "undo", "exit"):
            self.assertIn(f"/{name}", output)

    def test_help_matches_registry(self):
        # Every command the TUI registry advertises must have a CLI handler.
        for name in tui.DEFAULT_REGISTRY._order:
            self.assertIn(name, cli._SLASH_HANDLERS, f"/{name} has no handler")

    def test_exit_and_aliases(self):
        for text in ("/exit", "/quit", "/q", "  /exit  "):
            action, _ = run_slash(text)
            self.assertEqual(action, "exit", text)

    def test_undo_calls_cmd_undo(self):
        with mock.patch.object(cli, "cmd_undo") as undo:
            action, _ = run_slash("/undo")
        self.assertEqual(action, "handled")
        undo.assert_called_once_with(show_list=False)

    def test_undo_list_flag(self):
        with mock.patch.object(cli, "cmd_undo") as undo:
            action, _ = run_slash("/undo --list")
        self.assertEqual(action, "handled")
        undo.assert_called_once_with(show_list=True)

    def test_clear(self):
        with mock.patch.object(cli.os, "system") as system:
            action, _ = run_slash("/clear")
        self.assertEqual(action, "handled")
        system.assert_called_once()
        self.assertIn(system.call_args[0][0], ("clear", "cls"))

    def test_model_card(self):
        with (
            mock.patch.object(cli.config, "MODEL", "test-model-x"),
            mock.patch.object(cli.config, "PROVIDER", {"model_verified": True}),
            mock.patch.object(cli.config, "CLI_TOOL_PROFILE", "full"),
        ):
            action, output = run_slash("/model")
        self.assertEqual(action, "handled")
        self.assertIn("test-model-x", output)
        self.assertIn("Model", output)

    def test_model_with_args_points_at_zeline_model(self):
        with mock.patch.object(cli.config, "MODEL", "test-model-x"):
            action, output = run_slash("/model some-other-model")
        self.assertEqual(action, "handled")
        self.assertIn("zeline model", output)

    def test_model_help_description_matches_behavior(self):
        # #17: /model di chat HANYA menampilkan — deskripsi /help tidak boleh
        # mengklaim "Show or switch" seolah switch bisa dari dalam chat.
        spec = tui.DEFAULT_REGISTRY.get("model")
        self.assertIsNotNone(spec)
        self.assertNotIn("or switch", spec.description)
        self.assertIn("zeline model", spec.description)
        _, output = run_slash("/help")
        self.assertIn(spec.description, output)

    def test_status(self):
        workers = [
            {"id": "w1", "task": "do thing", "status": "running"},
            {"id": "w2", "task": "other", "status": "done"},
        ]
        with (
            mock.patch("zeline.usage_stats.UsageStore", FakeUsageStore),
            mock.patch("zeline.supervisor.get_supervisor", lambda identity: FakeSupervisor(workers)),
            mock.patch(
                "zeline.goals.list_goals",
                lambda identity, status=None: [
                    {"title": "Ship it", "status": "active"},
                    {"title": "Old", "status": "done"},
                ],
            ),
            mock.patch("zeline.memory.MemoryStore", FakeMemoryStore),
            mock.patch.object(cli.config, "MODEL", "test-model-x"),
        ):
            action, output = run_slash("/status")
        self.assertEqual(action, "handled")
        self.assertIn("Status", output)
        self.assertIn("test-model-x", output)
        self.assertIn("150", output)  # total tokens today
        self.assertIn("1 live / 2 total", output)
        self.assertIn("1 active / 2 total", output)
        self.assertIn("Memories", output)

    def test_status_degrades_gracefully(self):
        # A broken store must never break the REPL: the line shows "n/a".
        with (
            mock.patch("zeline.usage_stats.UsageStore", side_effect=RuntimeError("db locked")),
            mock.patch("zeline.supervisor.get_supervisor", side_effect=RuntimeError("nope")),
            mock.patch("zeline.goals.list_goals", side_effect=RuntimeError("nope")),
            mock.patch("zeline.memory.MemoryStore", side_effect=RuntimeError("nope")),
        ):
            action, output = run_slash("/status")
        self.assertEqual(action, "handled")
        self.assertIn("n/a", output)

    def test_goals_empty(self):
        with mock.patch("zeline.goals.list_goals", lambda identity, status=None: []):
            action, output = run_slash("/goals")
        self.assertEqual(action, "handled")
        self.assertIn("No goals yet.", output)

    def test_goals_list(self):
        goals = [
            {"title": "Ship it", "status": "active"},
            {"title": "Write docs", "status": "paused"},
        ]
        with mock.patch("zeline.goals.list_goals", lambda identity, status=None: goals):
            action, output = run_slash("/goals")
        self.assertEqual(action, "handled")
        self.assertIn("Goals (2):", output)
        self.assertIn("[active] Ship it", output)
        self.assertIn("[paused] Write docs", output)

    def test_workers_empty(self):
        with mock.patch(
            "zeline.supervisor.get_supervisor", lambda identity: FakeSupervisor([])
        ):
            action, output = run_slash("/workers")
        self.assertEqual(action, "handled")
        self.assertIn("No background workers.", output)

    def test_workers_list(self):
        workers = [{"id": "abc123", "task": "summarize inbox", "status": "running"}]
        with mock.patch(
            "zeline.supervisor.get_supervisor", lambda identity: FakeSupervisor(workers)
        ):
            action, output = run_slash("/workers")
        self.assertEqual(action, "handled")
        self.assertIn("Workers (1):", output)
        self.assertIn("abc123", output)
        self.assertIn("running", output)
        self.assertIn("summarize inbox", output)

    def test_workers_long_task_truncated(self):
        workers = [{"id": "abc123", "task": "x" * 200, "status": "running"}]
        with mock.patch(
            "zeline.supervisor.get_supervisor", lambda identity: FakeSupervisor(workers)
        ):
            _, output = run_slash("/workers")
        self.assertNotIn("x" * 200, output)
        self.assertIn("…", output)

    def test_memory_list(self):
        with mock.patch("zeline.memory.MemoryStore", FakeMemoryStore):
            action, output = run_slash("/memory")
        self.assertEqual(action, "handled")
        self.assertIn("aes likes tea", output)

    def test_memory_empty(self):
        class EmptyStore(FakeMemoryStore):
            def formatted(self):
                return "  \n"

        with mock.patch("zeline.memory.MemoryStore", EmptyStore):
            action, output = run_slash("/memory")
        self.assertEqual(action, "handled")
        self.assertIn("No memories stored yet.", output)

    def test_memory_search_hit(self):
        with mock.patch("zeline.memory.MemoryStore", FakeMemoryStore):
            action, output = run_slash("/memory tea")
        self.assertEqual(action, "handled")
        self.assertIn("aes likes tea", output)

    def test_memory_search_miss(self):
        with mock.patch("zeline.memory.MemoryStore", FakeMemoryStore):
            action, output = run_slash("/memory zebra")
        self.assertEqual(action, "handled")
        self.assertIn("No memories match", output)


def make_entry(question="Proceed?", options=None, full_text=""):
    return types.SimpleNamespace(
        question=question, options=options or [], full_text=full_text
    )


_RAISE_EOF = object()


class CliAskTests(unittest.TestCase):
    def run_ask(self, entry, tty, input_value=None):
        buffer = io.StringIO()
        with (
            mock.patch.object(cli, "_stdin_is_tty", return_value=tty),
            contextlib.redirect_stdout(buffer),
        ):
            if input_value is _RAISE_EOF:
                with mock.patch("builtins.input", side_effect=EOFError):
                    result = cli._cli_ask(entry)
            elif input_value is None:
                result = cli._cli_ask(entry)
            else:
                with mock.patch("builtins.input", return_value=input_value):
                    result = cli._cli_ask(entry)
        return result, buffer.getvalue()


class CliAskTtyTests(CliAskTests):
    def test_tty_uses_select_menu(self):
        entry = make_entry(options=["yes", "no"])
        with (
            mock.patch.object(cli, "_stdin_is_tty", return_value=True),
            mock.patch.object(cli.tui, "select", return_value=1) as select,
        ):
            result = cli._cli_ask(entry)
        self.assertEqual(result, "no")
        select.assert_called_once()
        args, _ = select.call_args
        self.assertEqual(list(args[1]), ["yes", "no"])

    def test_tty_cancel(self):
        entry = make_entry(options=["yes", "no"])
        with (
            mock.patch.object(cli, "_stdin_is_tty", return_value=True),
            mock.patch.object(cli.tui, "select", return_value=-1),
        ):
            result = cli._cli_ask(entry)
        self.assertTrue(result.startswith("CANCELLED"))

    def test_tty_no_options_skips_select(self):
        entry = make_entry(options=[])
        with (
            mock.patch.object(cli, "_stdin_is_tty", return_value=True),
            mock.patch.object(cli.tui, "select") as select,
            mock.patch("builtins.input", return_value="typed answer"),
        ):
            result = cli._cli_ask(entry)
        self.assertEqual(result, "typed answer")
        select.assert_not_called()


class CliAskNonTtyTests(CliAskTests):
    def test_number_picks_option(self):
        result, _ = self.run_ask(make_entry(options=["yes", "no"]), False, "2")
        self.assertEqual(result, "no")

    def test_free_text_answer_preserved(self):
        result, _ = self.run_ask(make_entry(options=["yes", "no"]), False, "maybe later")
        self.assertEqual(result, "maybe later")

    def test_empty_answer(self):
        result, _ = self.run_ask(make_entry(options=["yes", "no"]), False, "")
        self.assertEqual(result, "(empty answer)")

    def test_eof_cancels(self):
        result, _ = self.run_ask(make_entry(options=["yes", "no"]), False, _RAISE_EOF)
        self.assertTrue(result.startswith("CANCELLED"))

    def test_out_of_range_number_falls_through(self):
        result, _ = self.run_ask(make_entry(options=["yes", "no"]), False, "9")
        self.assertEqual(result, "9")

    def test_full_detail_block_printed(self):
        _, output = self.run_ask(
            make_entry(options=["a"], full_text="line1\nline2"), False, "1"
        )
        self.assertIn("full detail:", output)
        self.assertIn("line2", output)


if __name__ == "__main__":
    unittest.main()
