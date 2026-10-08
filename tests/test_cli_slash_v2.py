"""Tests for the CLI upgrade v2 slash commands and @file wiring in the REPL.

Covers ``/stats``, ``/export``, ``/tools`` (``/compact`` is deliberately NOT
registered — there is no clean public compaction API), and the rule that
``@file`` mentions are expanded before a turn reaches the model with warnings
printed locally as dim ``[!]`` lines.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
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


class SlashStatsTests(unittest.TestCase):
    def test_stats_calls_cmd_stats_with_defaults(self):
        with mock.patch.object(cli, "cmd_stats") as stats:
            action, _output = run_slash("/stats")
        self.assertEqual(action, "handled")
        stats.assert_called_once_with()

    def test_stats_by_day_flag(self):
        with mock.patch.object(cli, "cmd_stats") as stats:
            action, _output = run_slash("/stats --by-day")
        self.assertEqual(action, "handled")
        stats.assert_called_once_with(by_day=True)

    def test_stats_reset_flag(self):
        with mock.patch.object(cli, "cmd_stats") as stats:
            action, _output = run_slash("/stats --reset")
        self.assertEqual(action, "handled")
        stats.assert_called_once_with(reset=True)

    def test_stats_error_is_friendly_not_traceback(self):
        with mock.patch.object(cli, "cmd_stats", side_effect=RuntimeError("disk gone")):
            action, output = run_slash("/stats")
        self.assertEqual(action, "handled")
        self.assertIn("Could not load usage stats", output)
        self.assertNotIn("Traceback", output)


class SlashExportTests(unittest.TestCase):
    def test_export_defaults_to_cli_local_identity(self):
        with mock.patch.object(cli, "cmd_session_export") as export:
            action, _output = run_slash("/export")
        self.assertEqual(action, "handled")
        export.assert_called_once_with("cli:local", None)

    def test_export_with_path(self):
        with mock.patch.object(cli, "cmd_session_export") as export:
            action, _output = run_slash("/export /tmp/session.json")
        self.assertEqual(action, "handled")
        export.assert_called_once_with("cli:local", "/tmp/session.json")

    def test_export_error_is_friendly_not_traceback(self):
        with mock.patch.object(cli, "cmd_session_export", side_effect=RuntimeError("disk gone")):
            action, output = run_slash("/export")
        self.assertEqual(action, "handled")
        self.assertIn("Could not export the session", output)
        self.assertNotIn("Traceback", output)


class SlashToolsTests(unittest.TestCase):
    def test_tools_lists_read_only(self):
        with mock.patch.object(cli, "cmd_tools") as tools:
            action, _output = run_slash("/tools")
        self.assertEqual(action, "handled")
        tools.assert_called_once_with("list")

    def test_tools_with_args_is_rejected_friendly(self):
        with mock.patch.object(cli, "cmd_tools") as tools:
            action, output = run_slash("/tools profile full")
        self.assertEqual(action, "handled")
        tools.assert_not_called()
        self.assertIn("/tools", output)


class CompactRegisteredTests(unittest.TestCase):
    def test_compact_is_in_registry(self):
        self.assertIsNotNone(tui.DEFAULT_REGISTRY.get("compact"))
        self.assertIn("/compact", tui.DEFAULT_REGISTRY.command_names())
        self.assertIsNotNone(tui.parse_command("/compact"))

    def test_compact_is_handled_not_sent_to_model(self):
        action, output = run_slash("/compact")
        self.assertEqual(action, "handled")
        self.assertNotIn("Unknown command", output)


class SlashCompletionTests(unittest.TestCase):
    def test_new_commands_are_tab_completable(self):
        names = cli._slash_completion_names()
        for name in ("/stats", "/export", "/tools"):
            self.assertIn(name, names)

    def test_help_lists_new_commands(self):
        action, output = run_slash("/help")
        self.assertEqual(action, "handled")
        for name in ("/stats", "/export", "/tools"):
            self.assertIn(name, output)


class ExpandMentionsForTurnTests(unittest.TestCase):
    def test_no_at_sign_returns_text_untouched(self):
        with mock.patch.object(tui, "expand_mentions") as expand:
            with contextlib.redirect_stdout(io.StringIO()) as buffer:
                result = cli._expand_mentions_for_turn("hello, how are you")
        expand.assert_not_called()
        self.assertEqual(result, "hello, how are you")
        self.assertEqual(buffer.getvalue(), "")

    def test_warnings_printed_dim_and_expanded_returned(self):
        with mock.patch.object(
            tui, "expand_mentions", return_value=("EXPANDED", ["@nope: file not found"])
        ):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                result = cli._expand_mentions_for_turn("read @nope please")
        self.assertEqual(result, "EXPANDED")
        self.assertIn("[!] @nope: file not found", buffer.getvalue())

    def test_all_failed_keeps_original_text_but_warns(self):
        with mock.patch.object(
            tui, "expand_mentions", return_value=("literal @foo", ["@foo: file not found"])
        ):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                result = cli._expand_mentions_for_turn("literal @foo")
        self.assertEqual(result, "literal @foo")
        self.assertIn("[!] @foo: file not found", buffer.getvalue())


class CmdChatExpansionIntegrationTests(unittest.TestCase):
    """End-to-end: a one-shot ``cmd_chat(query)`` expands @file before send()."""

    def test_expanded_text_is_what_send_receives(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "notes.md"
            target.write_text("SECRET-NOTE-CONTENT", encoding="utf-8")
            sent = {}

            class FakeSessions:
                def send(self, identity, text, **kwargs):
                    sent["identity"] = identity
                    sent["text"] = text
                    return "ok"

            with (
                mock.patch.object(cli.config, "GATEWAY_SETUP_COMPLETE", True),
                mock.patch.object(cli.config, "SETUP_COMPLETE", True),
                mock.patch.object(cli.config, "PROVIDER", {"model_verified": True}),
                mock.patch.object(cli.config, "API_KEY", "k"),
                mock.patch.object(cli, "SessionStore", lambda **kw: FakeSessions()),
                mock.patch.object(cli.interaction, "register_channel"),
                mock.patch.object(cli.delivery, "register_channel"),
                mock.patch.object(cli, "_print_banner"),
                mock.patch.object(cli, "_print_session_header"),
                mock.patch.object(os, "getcwd", return_value=tmp),
            ):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    rc = cli.cmd_chat(f"summarize @{target.name}")
            self.assertEqual(rc, 0)
            self.assertEqual(sent["identity"], "cli:local")
            self.assertIn("SECRET-NOTE-CONTENT", sent["text"])
            self.assertNotIn("SECRET-NOTE-CONTENT", buffer.getvalue().split("<file")[0])

    def test_missing_file_warns_but_still_sends_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent = {}

            class FakeSessions:
                def send(self, identity, text, **kwargs):
                    sent["text"] = text
                    return "ok"

            with (
                mock.patch.object(cli.config, "GATEWAY_SETUP_COMPLETE", True),
                mock.patch.object(cli.config, "SETUP_COMPLETE", True),
                mock.patch.object(cli.config, "PROVIDER", {"model_verified": True}),
                mock.patch.object(cli.config, "API_KEY", "k"),
                mock.patch.object(cli, "SessionStore", lambda **kw: FakeSessions()),
                mock.patch.object(cli.interaction, "register_channel"),
                mock.patch.object(cli.delivery, "register_channel"),
                mock.patch.object(cli, "_print_banner"),
                mock.patch.object(cli, "_print_session_header"),
                mock.patch.object(os, "getcwd", return_value=tmp),
            ):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    rc = cli.cmd_chat("read @missing-file.txt please")
            self.assertEqual(rc, 0)
            self.assertEqual(sent["text"], "read @missing-file.txt please")
            self.assertIn("[!] @missing-file.txt: file not found", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
