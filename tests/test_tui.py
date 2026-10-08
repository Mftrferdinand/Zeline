"""Tests for zeline.tui (stdlib-first terminal UI layer, optional rich).

The whole suite must pass with and without ``rich`` installed, on a TTY and
off one. Environment mutations are restored after each test.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import tui  # noqa: E402


class FakeTty(io.StringIO):
    def isatty(self):  # noqa: D102
        return True


class CapabilityTests(unittest.TestCase):
    def test_paint_plain_when_no_color(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            os.environ.pop("FORCE_COLOR", None)
            self.assertEqual(tui.paint("hello", tui.COLOR_RED), "hello")

    def test_paint_wraps_with_force_color(self):
        env = {"FORCE_COLOR": "1", "TERM": "xterm"}
        env.pop("NO_COLOR", None)
        with mock.patch.dict(os.environ, env, clear=True):
            painted = tui.paint("hello", tui.COLOR_RED)
        self.assertTrue(painted.startswith(tui.COLOR_RED))
        self.assertTrue(painted.endswith(tui.COLOR_RESET))
        self.assertIn("hello", painted)

    def test_terminal_width_is_sane(self):
        width = tui.terminal_width()
        self.assertIsInstance(width, int)
        self.assertGreaterEqual(width, 20)

    def test_capability_helpers_return_bool(self):
        self.assertIsInstance(tui.colors_enabled(), bool)
        self.assertIsInstance(tui.unicode_supported(), bool)


class MarkdownTests(unittest.TestCase):
    def test_bold_italic_strikethrough_and_code(self):
        rendered = tui.render_markdown("**bold** *it* ~~gone~~ `code`")
        self.assertEqual(rendered, "bold it gone code")

    def test_headings_uppercase_top_levels(self):
        self.assertEqual(tui.render_markdown("# Title"), "TITLE")
        self.assertEqual(tui.render_markdown("## Sub"), "SUB")
        self.assertEqual(tui.render_markdown("### Small"), "Small")

    def test_heading_uppercase_does_not_corrupt_ansi(self):
        # Regression: .upper() applied AFTER _inline() corrupted ANSI escapes
        # (e.g. \x1b[38;5;39m became \x1b[38;5;39M = Delete Lines).
        # Uppercase must happen on plain text before escapes are planted.
        rendered = tui.render_markdown("# Hello **world** `code`")
        self.assertNotIn("\x1bM", rendered)  # no Delete Lines sequence
        for match in re.finditer(r"\x1b\[[0-9;]*([A-Za-z])", rendered):
            self.assertEqual(match.group(1), "m", f"corrupt escape in {rendered!r}")
        self.assertIn("HELLO", rendered)

    def test_bullets(self):
        rendered = tui.render_markdown("- one\n* two\n+ three")
        bullet = "•" if tui.unicode_supported() else "*"
        self.assertEqual(rendered, f"{bullet} one\n{bullet} two\n{bullet} three")

    def test_numbered_list(self):
        self.assertEqual(
            tui.render_markdown("1. first\n2) second"), "1. first\n2. second"
        )

    def test_links_and_images(self):
        rendered = tui.render_markdown("[docs](https://example.com)")
        self.assertEqual(rendered, "docs (https://example.com)")
        rendered = tui.render_markdown("![alt](https://example.com/i.png)")
        self.assertEqual(rendered, "alt (https://example.com/i.png)")

    def test_fenced_code_block_is_verbatim_and_indented(self):
        rendered = tui.render_markdown("```python\nx = **not bold**\n```")
        self.assertEqual(rendered, "    x = **not bold**")

    def test_blockquote(self):
        bar = "│" if tui.unicode_supported() else "|"
        self.assertEqual(tui.render_markdown("> hello"), f"{bar} hello")

    def test_horizontal_rule(self):
        # A rule line of dashes or box-drawing, nothing else.
        rendered = tui.render_markdown("---")
        self.assertTrue(rendered)
        self.assertTrue(all(ch in "-─ " for ch in rendered))

    def test_code_span_protects_markers_inside(self):
        rendered = tui.render_markdown("run `a * b` now")
        self.assertEqual(rendered, "run a * b now")

    def test_blank_lines_collapse(self):
        rendered = tui.render_markdown("a\n\n\n\nb")
        self.assertEqual(rendered, "a\n\nb")

    def test_empty_input(self):
        self.assertEqual(tui.render_markdown(""), "")
        self.assertEqual(tui.render_markdown(None), "")

    def test_print_markdown_without_rich(self):
        with mock.patch.object(tui, "RICH_AVAILABLE", False):
            out = io.StringIO()
            tui.print_markdown("# Hi\n\n**bold**", file=out)
            self.assertEqual(out.getvalue().strip(), "HI\n\nbold")

    def test_print_markdown_rich_path(self):
        if not tui.RICH_AVAILABLE:
            self.skipTest("rich not installed")
        out = io.StringIO()
        tui.print_markdown("# Hi", file=out)
        self.assertIn("Hi", out.getvalue())


class SpinnerTests(unittest.TestCase):
    def test_noop_off_tty(self):
        with tui.spinner("Working", file=io.StringIO()) as spin:
            self.assertIsNotNone(spin)
        # No crash, no thread started.

    def test_animates_on_tty_and_cleans_up(self):
        out = FakeTty()
        with tui.spinner("Working", file=out):
            import time

            time.sleep(0.25)
        text = out.getvalue()
        self.assertIn("Working", text)
        # Exit clears the line with carriage returns and spaces.
        self.assertTrue(text.rstrip(" ").endswith("\r") or "\r" in text)

    def test_update_message(self):
        spin = tui.Spinner("one", file=io.StringIO())
        spin.update("two")
        self.assertEqual(spin.message, "two")

    def test_exception_inside_context_propagates(self):
        with self.assertRaises(RuntimeError):
            with tui.spinner("Working", file=io.StringIO()):
                raise RuntimeError("boom")


class SelectTests(unittest.TestCase):
    def _non_tty_stdin(self, script: str) -> io.StringIO:
        return io.StringIO(script)

    def test_empty_options(self):
        self.assertEqual(tui.select("T:", [], _stdin=io.StringIO()), -1)

    def test_numeric_fallback_pick(self):
        stdin = self._non_tty_stdin("")
        answers = iter(["2"])
        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a", "b", "c"],
                _stdin=stdin,
                input_func=lambda _prompt: next(answers),
            )
        self.assertEqual(choice, 1)

    def test_numeric_fallback_empty_cancels(self):
        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a", "b"],
                _stdin=io.StringIO(),
                input_func=lambda _prompt: "",
            )
        self.assertEqual(choice, -1)

    def test_numeric_fallback_retries_invalid(self):
        answers = iter(["x", "0", "9", "1"])
        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a", "b"],
                _stdin=io.StringIO(),
                input_func=lambda _prompt: next(answers),
            )
        self.assertEqual(choice, 0)

    def test_numeric_fallback_eof_cancels(self):
        def _boom(_prompt):
            raise EOFError

        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select("Pick:", ["a"], _stdin=io.StringIO(), input_func=_boom)
        self.assertEqual(choice, -1)

    def test_arrow_path_moves_and_selects(self):
        keys = iter(["down", "down", "enter"])
        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a", "b", "c"],
                _stdin=FakeTty(),
                _key_reader=lambda: next(keys),
                _raw_mode=lambda: contextlib.nullcontext(),
            )
        self.assertEqual(choice, 2)

    def test_arrow_path_wraps_up(self):
        keys = iter(["up", "enter"])
        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a", "b"],
                _stdin=FakeTty(),
                _key_reader=lambda: next(keys),
                _raw_mode=lambda: contextlib.nullcontext(),
            )
        self.assertEqual(choice, 1)

    def test_arrow_path_cancel(self):
        keys = iter(["cancel"])
        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a"],
                _stdin=FakeTty(),
                _key_reader=lambda: next(keys),
                _raw_mode=lambda: contextlib.nullcontext(),
            )
        self.assertEqual(choice, -1)

    def test_arrow_path_start_index_clamped(self):
        keys = iter(["enter"])
        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a", "b"],
                start=99,
                _stdin=FakeTty(),
                _key_reader=lambda: next(keys),
                _raw_mode=lambda: contextlib.nullcontext(),
            )
        self.assertEqual(choice, 1)

    def test_arrow_path_keyboard_interrupt_cancels(self):
        def _raise():
            raise KeyboardInterrupt

        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a"],
                _stdin=FakeTty(),
                _key_reader=_raise,
                _raw_mode=lambda: contextlib.nullcontext(),
            )
        self.assertEqual(choice, -1)

    def _broken_raw_mode(self):
        def _boom():
            raise OSError("terminal does not support raw mode")

        return _boom

    def test_raw_mode_unsupported_falls_back_to_numeric(self):
        answers = iter(["2"])
        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a", "b", "c"],
                _stdin=FakeTty(),
                input_func=lambda _prompt: next(answers),
                _raw_mode=self._broken_raw_mode(),
            )
        self.assertEqual(choice, 1)

    def test_raw_mode_unsupported_empty_enter_cancels_not_first(self):
        # Enter kosong pada fallback numerik = cancel (-1), bukan pilihan pertama.
        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a", "b"],
                _stdin=FakeTty(),
                input_func=lambda _prompt: "",
                _raw_mode=self._broken_raw_mode(),
            )
        self.assertEqual(choice, -1)

    def test_raw_mode_unsupported_eof_cancels(self):
        def _eof(_prompt):
            raise EOFError

        with contextlib.redirect_stdout(io.StringIO()):
            choice = tui.select(
                "Pick:", ["a", "b"],
                _stdin=FakeTty(),
                input_func=_eof,
                _raw_mode=self._broken_raw_mode(),
            )
        self.assertEqual(choice, -1)

    def test_raw_mode_unsupported_invalid_then_empty_cancels(self):
        # Fallback numerik tetap menolak input tak valid, lalu "" = cancel.
        answers = iter(["x", ""])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            choice = tui.select(
                "Pick:", ["a", "b"],
                _stdin=FakeTty(),
                input_func=lambda _prompt: next(answers),
                _raw_mode=self._broken_raw_mode(),
            )
        self.assertEqual(choice, -1)
        self.assertIn("Invalid choice.", out.getvalue())

    def test_arrow_failure_renders_title_once(self):
        # read_key raise di TENGAH arrow-picker -> fallback numerik; judul
        # TEPAT 1x (dulu: judul arrow-picker + judul numerik = 2x).
        calls = {"n": 0}

        def flaky_key():
            calls["n"] += 1
            if calls["n"] == 1:
                return "down"
            raise RuntimeError("terminal rusak")

        answers = iter(["1"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            choice = tui.select(
                "Pick:", ["a", "b"],
                _stdin=FakeTty(),
                _key_reader=flaky_key,
                _raw_mode=lambda: contextlib.nullcontext(),
                input_func=lambda _prompt: next(answers),
            )
        self.assertEqual(choice, 0)
        self.assertEqual(out.getvalue().count("Pick:"), 1,
                         "judul ter-render lebih dari sekali")

    def test_raw_mode_failure_before_title_still_shows_title_once(self):
        # raw() raise SEBELUM judul tampil -> fallback numerik tetap
        # menampilkan judul, tepat 1x.
        answers = iter(["2"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            choice = tui.select(
                "Pick:", ["a", "b"],
                _stdin=FakeTty(),
                _key_reader=lambda: "enter",
                _raw_mode=self._broken_raw_mode(),
                input_func=lambda _prompt: next(answers),
            )
        self.assertEqual(choice, 1)
        self.assertEqual(out.getvalue().count("Pick:"), 1)


class CardAndPromptTests(unittest.TestCase):
    def test_card_aligns_values(self):
        text = tui.card([("Agent", "Zeline"), ("Model", "demo-1")])
        lines = text.splitlines()
        value_columns = {
            line.index("Zeline") for line in lines if "Zeline" in line
        } | {line.index("demo-1") for line in lines if "demo-1" in line}
        self.assertEqual(len(value_columns), 1)

    def test_card_with_title_and_dict(self):
        text = tui.card({"A": "1"}, title="Session")
        self.assertIn("Session", text)
        self.assertIn("A", text)
        self.assertIn("1", text)

    def test_card_empty(self):
        self.assertEqual(tui.card([]), "")

    def test_card_plain_without_color(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            os.environ.pop("FORCE_COLOR", None)
            text = tui.card([("Agent", "Zeline")])
        self.assertNotIn("\033[", text)

    def test_print_card(self):
        out = io.StringIO()
        tui.print_card([("K", "V")], file=out)
        self.assertIn("K", out.getvalue())

    def test_status_bar_joins_parts(self):
        bar = tui.status_bar(["a", "b"])
        self.assertIn("a", bar)
        self.assertIn("b", bar)
        sep = "•" if tui.unicode_supported() else "-"
        self.assertIn(sep, bar)

    def test_render_prompt(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            os.environ.pop("FORCE_COLOR", None)
            prompt = tui.render_prompt("You")
        self.assertTrue(prompt.startswith("You "))
        self.assertTrue(prompt.rstrip().endswith(("❯", ">")))


class CommandTests(unittest.TestCase):
    def test_parse_simple(self):
        spec, args = tui.parse_command("/help")
        self.assertEqual(spec.name, "help")
        self.assertEqual(args, [])

    def test_parse_with_args(self):
        spec, args = tui.parse_command("/model gpt-4o mini")
        self.assertEqual(spec.name, "model")
        self.assertEqual(args, ["gpt-4o", "mini"])

    def test_parse_case_insensitive(self):
        spec, _ = tui.parse_command("/HELP")
        self.assertEqual(spec.name, "help")

    def test_parse_alias(self):
        spec, _ = tui.parse_command("/quit")
        self.assertEqual(spec.name, "exit")
        spec, _ = tui.parse_command("/q")
        self.assertEqual(spec.name, "exit")

    def test_parse_rejects_non_commands(self):
        self.assertIsNone(tui.parse_command("hello"))
        self.assertIsNone(tui.parse_command(""))
        self.assertIsNone(tui.parse_command("/"))
        self.assertIsNone(tui.parse_command("/ unknown"))

    def test_parse_tolerates_space_after_slash(self):
        spec, _ = tui.parse_command("/ clear")
        self.assertEqual(spec.name, "clear")

    def test_extended_command_set_present(self):
        for name in ("help", "model", "status", "goals", "workers", "memory", "clear"):
            spec = tui.DEFAULT_REGISTRY.get(name)
            self.assertIsNotNone(spec, name)
            self.assertTrue(spec.description, name)
            self.assertTrue(spec.usage, name)

    def test_help_text_lists_everything(self):
        text = tui.command_help()
        for name in ("help", "model", "status", "goals", "workers", "memory", "clear"):
            self.assertIn(f"/{name}", text)

    def test_custom_registry(self):
        registry = tui.CommandRegistry()
        registry.register(tui.CommandSpec("ping", "Reply pong.", usage="/ping"))
        spec, args = registry.parse("/ping now")
        self.assertEqual(spec.name, "ping")
        self.assertEqual(args, ["now"])
        self.assertFalse(registry.is_command("/nope"))
        self.assertTrue(registry.is_command("/ping"))


if __name__ == "__main__":
    unittest.main()
