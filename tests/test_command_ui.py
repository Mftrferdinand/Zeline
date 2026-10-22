"""Shared terminal presentation never changes command semantics or raw output."""
import contextlib
import io
import json
import os
import re
import threading
import unittest
from unittest.mock import MagicMock, PropertyMock, patch
from zeline import cli, command_ui, config

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

class Terminal(io.StringIO):
    def isatty(self):
        return True


class CommandPresentationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"TERM": "xterm-256color", "COLUMNS": "80"}))

    def test_terminal_commands_share_panels_and_return_codes(self):
        stream = Terminal()
        @command_ui.present_command
        def cmd_demo():
            command_ui.emit("one")
            command_ui.emit("two")
            return 7
        with contextlib.redirect_stdout(stream):
            self.assertEqual(cmd_demo(), 7)
        plain = ANSI.sub("", stream.getvalue())
        self.assertEqual(plain.count("╭"), 1)
        self.assertEqual(plain.count("one"), 1)
        self.assertIsNone(command_ui.current_screen())

    def test_gateway_list_has_no_logo_but_run_and_status_do(self):
        cases = (("cmd_gateway_list", False), ("cmd_gateway_run", True), ("cmd_doctor", True))
        for name, expected in cases:
            stream = Terminal()
            def action():
                command_ui.emit("example content")
                return 0
            action.__name__ = name
            with patch.object(command_ui.ChatUI, "identity") as identity, contextlib.redirect_stdout(stream):
                command_ui.present_command(action)()
            self.assertEqual(identity.call_count, int(expected), name)

    def test_gateway_indicator_has_honest_states_and_cleans_up(self):
        stream = Terminal()
        screen = command_ui.CommandScreen("Gateway", stream=stream, live=True)
        screen.ui.color = False
        self.assertTrue(callable(getattr(screen, "set_state", None)), "gateway lifecycle indicator missing")
        screen.set_state("STARTING", "waiting for adapters")
        screen.set_state("RUNNING", "telegram thread alive; connection not inferred")
        screen.finish()
        text = stream.getvalue()
        self.assertIn("[STARTING]", text)
        self.assertIn("[RUNNING]", text)
        self.assertIn("[STOPPED]", text)
        self.assertNotIn("\x1b", text)
        self.assertIsNone(screen._status_thread)

    def test_animated_footer_stops_after_exception_without_leaking_context(self):
        stream = Terminal()
        tracked = []
        @command_ui.present_command
        def cmd_gateway_run():
            screen = command_ui.current_screen()
            screen.ui.color = True
            tracked.append(screen)
            screen.set_state("STARTING", "preview only")
            self.assertTrue(screen._status_thread.is_alive())
            raise RuntimeError("preview failure")
        with contextlib.redirect_stdout(stream), self.assertRaises(RuntimeError):
            cmd_gateway_run()
        self.assertFalse(tracked[0]._status_thread.is_alive())
        self.assertIn("[FAILED]", stream.getvalue())
        self.assertIsNone(command_ui.current_screen())
        self.assertIsNone(command_ui._live_screen)

    def test_logo_policy_matches_status_alias_only_and_help_has_no_logo(self):
        with patch.object(cli, "cmd_doctor", return_value=0) as doctor:
            cli.main(["status"])
            doctor.assert_called_once_with(show_logo=True)
            doctor.reset_mock()
            cli.main(["doctor"])
            doctor.assert_called_once_with(show_logo=False)
        stream = Terminal()
        with contextlib.redirect_stdout(stream):
            cli.build_parser().print_help()
        self.assertNotIn("████", stream.getvalue())

    def test_spinner_ticks_overwrite_without_blank_erase_frames(self):
        stream = Terminal()
        screen = command_ui.CommandScreen("Gateway", stream=stream, live=True)
        screen.ui.color = True
        screen._state = ("RUNNING", "adapter threads alive")
        ticks = iter((False, False, True))
        with patch.object(screen._status_stop, "wait", side_effect=lambda _: next(ticks)):
            screen._animate()
        self.assertNotIn("\x1b[2K", stream.getvalue())
        self.assertEqual(stream.getvalue().count("RUNNING"), 2)

    def test_arrow_menu_owns_its_panel_and_uses_raw_safe_line_endings(self):
        raw_chunks, active = [], [False]
        class Capture(Terminal):
            def write(self, value):
                if active[0]:
                    raw_chunks.append(value)
                return super().write(value)
        stream = Capture()
        @contextlib.contextmanager
        def raw_context():
            self.assertFalse(command_ui.current_screen().open_panel)
            active[0] = True
            try:
                yield
            finally:
                active[0] = False
        @command_ui.present_command
        def cmd_preview():
            command_ui.emit("Previous information")
            return cli._arrow_menu("Provider & model", ["Add provider", "Back"])
        with patch.object(cli.sys.stdin, "isatty", return_value=True), \
             patch.object(cli, "raw_mode", raw_context), \
             patch.object(cli, "_read_menu_key", side_effect=["down", "enter"]), \
             contextlib.redirect_stdout(stream):
            self.assertEqual(cmd_preview(), 1)
        raw = "".join(raw_chunks)
        self.assertIsNone(re.search(r"(?<!\r)\n", raw), "bare LF breaks raw-mode panel alignment")
        self.assertIn("Back", raw)
        self.assertNotIn("[•]", raw)

    def test_model_summary_and_cancel_do_not_change_configuration(self):
        cfg = {"provider": {"name": "Test provider", "model": "example-model",
               "base_url": "http://user:NEVER_SHOW@localhost:20128/v1?token=NEVER_SHOW",
               "api_key": "NEVER_SHOW"}, "providers": {"test": {}}}
        stream = Terminal()
        with patch.object(config, "GATEWAY_SETUP_COMPLETE", True), \
             patch.object(config, "stored_config_copy", return_value=cfg), \
             patch.object(config, "save_config") as save, \
             patch.object(cli, "_arrow_menu", return_value=3), \
             contextlib.redirect_stdout(stream):
            self.assertEqual(cli.cmd_model(), 0)
        save.assert_not_called()
        text = ANSI.sub("", stream.getvalue())
        self.assertIn("Active configuration", text)
        self.assertIn("example-model", text)
        self.assertIn("localhost:20128", text)
        self.assertNotIn("NEVER_SHOW", text)
        self.assertNotIn("Done.", text)

    def test_menu_viewport_and_borders_fit_narrow_terminals(self):
        from wcwidth import wcswidth
        for width in (30, 40, 48, 80, 120):
            rows = command_ui.menu_frame("Models", ["模型 " + "x" * 200] * 30,
                                         20, width=width, limit=5)
            self.assertLessEqual(len(rows), 11)
            self.assertIn("21/30", ANSI.sub("", "".join(rows)))
            for row in rows:
                self.assertEqual(wcswidth(ANSI.sub("", row)), width)

    def test_non_tty_remains_byte_compatible(self):
        stream = io.StringIO()
        @command_ui.present_command
        def cmd_demo():
            command_ui.emit("RAW: value")
            return 4
        with contextlib.redirect_stdout(stream):
            self.assertEqual(cmd_demo(), 4)
        self.assertEqual(stream.getvalue(), "RAW: value\n")

    def test_config_json_and_paths_stay_raw_even_in_a_terminal(self):
        stream = Terminal()
        with patch.object(config, "config_copy", return_value={"provider": {"api_key": "secret"}}):
            with contextlib.redirect_stdout(stream):
                self.assertEqual(cli.cmd_config("show"), 0)
        result = json.loads(stream.getvalue())
        self.assertNotEqual(result["provider"]["api_key"], "secret")
        self.assertNotIn("╭", stream.getvalue())
        stream = Terminal()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(cli.cmd_config("path"), 0)
        self.assertEqual(stream.getvalue(), str(config.CONFIG_FILE) + "\n")

    def test_nested_commands_do_not_duplicate_the_identity(self):
        stream = Terminal()
        @command_ui.present_command
        def cmd_inner():
            command_ui.emit("nested")
        @command_ui.present_command
        def cmd_outer():
            cmd_inner()
        with contextlib.redirect_stdout(stream):
            cmd_outer()
        plain = ANSI.sub("", stream.getvalue())
        self.assertEqual(plain.count("╭"), 1)

    def test_exception_restores_context_and_closes_panel(self):
        stream = Terminal()
        @command_ui.present_command
        def cmd_bad():
            command_ui.emit("before exception")
            raise ValueError("expected")
        with contextlib.redirect_stdout(stream), self.assertRaises(ValueError):
            cmd_bad()
        self.assertIsNone(command_ui.current_screen())
        self.assertEqual(stream.getvalue().count("╭"), stream.getvalue().count("╰"))

    def test_no_color_has_no_ansi(self):
        stream = Terminal()
        @command_ui.present_command
        def cmd_demo():
            command_ui.emit("plain")
        with patch.dict(os.environ, {"NO_COLOR": "1"}), contextlib.redirect_stdout(stream):
            cmd_demo()
        self.assertNotIn("\x1b", stream.getvalue())

    def test_cursor_menus_are_not_sanitized_or_reflowed(self):
        stream = Terminal()
        @command_ui.present_command
        def cmd_picker():
            command_ui.emit("Select")
            command_ui.raw_print("\r\x1b[K > choice")
            command_ui.raw_print("\x1b[4A", end="", flush=True)
        with contextlib.redirect_stdout(stream):
            cmd_picker()
        self.assertIn("\r\x1b[K > choice\n", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("\x1b[4A"))

    def test_untrusted_cursor_codes_are_removed_from_messages(self):
        stream = Terminal()
        @command_ui.present_command
        def cmd_untrusted():
            command_ui.emit("model\x1b[2J\x1b[Hname")
        with contextlib.redirect_stdout(stream):
            cmd_untrusted()
        self.assertNotIn("\x1b[2J", stream.getvalue())
        self.assertIn("modelname", stream.getvalue())

    def test_command_panels_fit_every_terminal_width(self):
        for width in (30, 40, 48, 56, 80, 100, 120):
            stream = Terminal()
            screen = command_ui.CommandScreen("Width sweep", stream=stream, width=width)
            screen.begin()
            screen.facts("Details", [("Long field", "a long model or workspace value " * 8)])
            screen.text("gateway ready " * 20)
            screen.close()
            for line in ANSI.sub("", stream.getvalue()).splitlines():
                self.assertLessEqual(len(line), width, (width, line))

    def test_wide_unicode_does_not_break_command_borders(self):
        from wcwidth import wcswidth
        for width in (30, 48, 80):
            stream = Terminal()
            screen = command_ui.CommandScreen("Unicode", stream=stream, width=width)
            screen.begin()
            screen.facts("Details", [("工作目录", "✅ 文件路径/" * 12)])
            screen.text("✅ ready 中文日志 " * 20)
            screen.close()
            for line in ANSI.sub("", stream.getvalue()).splitlines():
                self.assertLessEqual(wcswidth(line), width, (width, line))

    def test_provider_model_labels_cannot_inject_terminal_controls(self):
        stream = Terminal()
        options = ["normal", "bad\x1b[2J\x1b]52;c;injected\x07\nmodel\tname"]
        original = options.copy()
        with patch.object(cli.sys.stdin, "isatty", return_value=True), \
             patch.object(cli, "raw_mode", return_value=contextlib.nullcontext()), \
             patch.object(cli, "_read_menu_key", side_effect=["down", "enter"]), \
             contextlib.redirect_stdout(stream):
            selected = cli._arrow_menu("Pick a model", options)
        self.assertEqual(selected, 1)
        self.assertEqual(options, original)
        self.assertNotIn("\x1b[2J", stream.getvalue())
        self.assertNotIn("\x1b]52", stream.getvalue())
        self.assertNotIn("injected", stream.getvalue())
        self.assertIn("bad model name", stream.getvalue())

    def test_native_input_closes_panel_before_prompt(self):
        stream = Terminal()
        @command_ui.present_command
        def cmd_prompt():
            command_ui.emit("Question")
            answer = command_ui.read_input("Choice: ")
            self.assertFalse(command_ui.current_screen().open_panel)
            return answer
        with patch("builtins.input", return_value="selected"), contextlib.redirect_stdout(stream):
            self.assertEqual(cmd_prompt(), "selected")

    def test_status_is_structured_and_never_exposes_the_key(self):
        stream = Terminal()
        with patch.object(config, "API_KEY", "NEVER_PRINT_THIS_SECRET"), \
             patch.object(cli.gateway_service, "status", return_value=(False, "Gateway is not running.", None)), \
             patch.object(cli, "gateway_status", return_value=[]), contextlib.redirect_stdout(stream):
            self.assertEqual(cli.cmd_doctor(), 0)
        text = ANSI.sub("", stream.getvalue())
        for title in ("Runtime", "Workspace & configuration", "Gateways", "Diagnostics"):
            self.assertIn(title, text)
        self.assertNotIn("NEVER_PRINT_THIS_SECRET", text)
        self.assertNotIn("connected & running", text)

    def test_command_help_has_a_terminal_theme(self):
        stream = Terminal()
        with contextlib.redirect_stdout(stream):
            cli.build_parser().print_help()
        self.assertIn("Command reference", stream.getvalue())
        self.assertIn("gateway", stream.getvalue())

    def test_all_command_entrypoints_are_covered(self):
        for name, function in vars(cli).items():
            if name.startswith("cmd_") and callable(function):
                self.assertTrue(hasattr(function, "__wrapped__"), name)

    def test_foreground_gateway_ui_does_not_change_lifecycle(self):
        stream = Terminal()
        runtime = MagicMock(threads=[("demo", object())], alive=[])
        lock = MagicMock()
        lock.acquire.return_value = True
        def start_adapters(*args, **kwargs):
            worker = threading.Thread(target=command_ui.emit, args=("[demo] connected via polling",))
            worker.start()
            worker.join(timeout=2)
            return runtime
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(config, "API_KEY", "test-only"))
            stack.enter_context(patch.object(config, "GATEWAYS", {"demo": {"enabled": True}}))
            stack.enter_context(patch.object(cli.gateway_service, "status", return_value=(False, "stopped", None)))
            stack.enter_context(patch.object(cli.gateway_service, "GatewayLock", return_value=lock))
            stack.enter_context(patch.object(cli.gateway_service, "is_termux", return_value=False))
            stack.enter_context(patch.object(cli.gateway_service, "ensure_termux_wake_lock", return_value=(True, "Termux wake-lock active")))
            stack.enter_context(patch.object(type(runtime), "alive", create=True, new_callable=PropertyMock, side_effect=[["demo"], []]))
            stack.enter_context(patch.object(cli.time, "sleep"))
            router = stack.enter_context(patch.object(cli, "_router_diagnostics", create=True, return_value=[("9Router", "detected locally")]))
            stack.enter_context(patch.object(cli, "SessionStore"))
            stack.enter_context(patch.object(cli, "run_all", side_effect=start_adapters))
            stack.enter_context(patch("zeline.scheduler.Scheduler"))
            stack.enter_context(patch("zeline.scheduler.list_jobs", return_value=[]))
            stack.enter_context(patch("zeline.durable_jobs.Worker"))
            stack.enter_context(patch.object(cli.signal, "signal", return_value=None))
            stack.enter_context(contextlib.redirect_stdout(stream))
            self.assertEqual(cli.cmd_gateway_run(), 0)
        text = ANSI.sub("", stream.getvalue())
        self.assertNotIn("✅", text)
        self.assertIn("Termux wake-lock active", text)
        self.assertNotIn("9Router detection", text)
        self.assertLess(text.index("Starting gateway"), text.index("9Router:"))
        self.assertLess(text.index("9Router:"), text.index("Termux wake-lock active"))
        router.assert_called_once_with()
        self.assertIn("RUNNING", text)
        self.assertIn("STOPPED", text)
        self.assertIn("Gateway runtime", text)
        self.assertIn("connected via polling", text)
        self.assertRegex(text, r"[0-9]{2}:[0-9]{2}:[0-9]{2}")
        runtime.stop.assert_called_once_with(timeout=1)
        lock.release.assert_called_once()
        self.assertIsNone(command_ui._live_screen)
