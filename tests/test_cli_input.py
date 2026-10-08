"""Unit tests for the chat REPL input layer in zeline.cli.

Covers ``_read_user_input`` (trailing-backslash multiline joining, the
``\\\\`` literal case, the continuation cap), the readline tab completer
(driven by ``tui.DEFAULT_REGISTRY`` so new commands are picked up
automatically), readline history setup (temporary histfile only — never the
real ``~/.zeline``), and the ``/editor`` slash command.
"""

from __future__ import annotations

import builtins
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import cli  # noqa: E402
from zeline import tui  # noqa: E402


def make_input(lines):
    """A fake ``input()`` that replays lines and records the prompts used."""
    prompts = []
    it = iter(lines)

    def fake(prompt=""):
        prompts.append(prompt)
        return next(it)

    fake.prompts = prompts
    return fake


class FakeReadline:
    """Minimal readline double recording the calls the REPL makes."""

    def __init__(self):
        self.loaded = []
        self.saved = []
        self.history_length = None
        self.completer = None
        self.delims = None
        self.items = []

    # history file
    def read_history_file(self, path):
        self.loaded.append(path)
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            self.items.append(line)

    def write_history_file(self, path):
        self.saved.append(path)
        Path(path).write_text("\n".join(self.items) + "\n", encoding="utf-8")

    def set_history_length(self, n):
        self.history_length = n

    # completion
    def set_completer(self, fn):
        self.completer = fn

    def set_completer_delims(self, delims):
        self.delims = delims

    def get_begidx(self):
        return 0

    # blank-line filtering
    def get_current_history_length(self):
        return len(self.items)

    def get_history_item(self, n):
        return self.items[n - 1]

    def remove_history_item(self, n):
        del self.items[n]


class MultilineTests(unittest.TestCase):
    def setUp(self):
        # No readline side effects while testing joining logic.
        self._patcher = mock.patch.object(cli, "_readline_module", return_value=None)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def read(self, lines, **kwargs):
        fake = make_input(lines)
        with mock.patch.object(builtins, "input", fake):
            result = cli._read_user_input("You> ", **kwargs)
        return result, fake.prompts

    def test_single_line(self):
        result, prompts = self.read(["hello world"])
        self.assertEqual(result, "hello world")
        self.assertEqual(prompts, ["You> "])

    def test_trailing_backslash_continues(self):
        result, prompts = self.read(["first \\", "second"])
        # Only the continuation backslash is dropped; the space stays.
        self.assertEqual(result, "first \nsecond")
        self.assertEqual(prompts, ["You> ", "... "])

    def test_custom_continuation_prompt(self):
        result, prompts = self.read(["a\\", "b"], continuation_prompt=">> ")
        self.assertEqual(result, "a\nb")
        self.assertEqual(prompts, ["You> ", ">> "])

    def test_double_backslash_is_literal(self):
        result, _ = self.read(["C:\\\\path\\\\"])
        self.assertEqual(result, "C:\\\\path\\\\")

    def test_mixed_runs(self):
        # odd run -> continue; even run -> literal, ends input
        result, _ = self.read(["one \\", "two \\\\", "three"])
        self.assertEqual(result, "one \ntwo \\\\")
        # "three" is never read: the even run ended the turn

    def test_three_backslashes_continue_with_two_literal(self):
        result, _ = self.read(["x\\\\\\", "y"])
        self.assertEqual(result, "x\\\\\ny")

    def test_whitespace_before_backslash_kept(self):
        result, _ = self.read(["padded   \\", "next"])
        self.assertEqual(result, "padded   \nnext")

    def test_continuation_cap(self):
        lines = ["l%d\\" % i for i in range(10)]
        result, _ = self.read(lines, max_lines=3)
        # Two continuations joined, then the third line is taken literally
        # (backslash kept) and the turn ends.
        self.assertEqual(result, "l0\nl1\nl2\\")

    def test_single_line_limit_one(self):
        result, _ = self.read(["a\\", "b"], max_lines=1)
        self.assertEqual(result, "a\\")

    def test_result_stripped(self):
        result, _ = self.read(["  hello  "])
        self.assertEqual(result, "hello")

    def test_eof_propagates(self):
        with mock.patch.object(builtins, "input", mock.Mock(side_effect=EOFError)):
            with self.assertRaises(EOFError):
                cli._read_user_input("You> ")

    def test_keyboard_interrupt_propagates(self):
        with mock.patch.object(builtins, "input", mock.Mock(side_effect=KeyboardInterrupt)):
            with self.assertRaises(KeyboardInterrupt):
                cli._read_user_input("You> ")


class BlankHistoryTests(unittest.TestCase):
    def test_blank_line_removed_from_history(self):
        rl = FakeReadline()
        rl.items = ["real command", "   "]
        cli._drop_blank_from_history(rl)
        self.assertEqual(rl.items, ["real command"])

    def test_non_blank_line_kept(self):
        rl = FakeReadline()
        rl.items = ["real command"]
        cli._drop_blank_from_history(rl)
        self.assertEqual(rl.items, ["real command"])

    def test_broken_readline_never_raises(self):
        broken = mock.Mock()
        broken.get_current_history_length.side_effect = RuntimeError("nope")
        cli._drop_blank_from_history(broken)  # must not raise


class HistoryInitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _tty(self, value):
        ctx = mock.patch.object(sys.stdin, "isatty", return_value=value)
        ctx.start()
        self.addCleanup(ctx.stop)
        ctx2 = mock.patch.object(sys.stdout, "isatty", return_value=value)
        ctx2.start()
        self.addCleanup(ctx2.stop)

    def init(self, histfile, readline_mod):
        registered = []
        with (
            mock.patch.object(cli, "_readline_module", return_value=readline_mod),
            mock.patch.object(cli.atexit, "register", side_effect=lambda fn: registered.append(fn)),
        ):
            ok = cli._init_readline_history(histfile=histfile)
        return ok, registered

    def test_loads_existing_history_and_registers_save(self):
        self._tty(True)
        hist = self.tmp / "hist"
        hist.write_text("oldcmd\n", encoding="utf-8")
        rl = FakeReadline()
        ok, registered = self.init(hist, rl)
        self.assertTrue(ok)
        self.assertEqual(rl.loaded, [str(hist)])
        self.assertEqual(rl.items, ["oldcmd"])
        self.assertEqual(rl.history_length, 500)
        self.assertIs(rl.completer, cli._slash_completer)
        # "/" must not be a word-break character for completion.
        self.assertNotIn("/", rl.delims)
        # The atexit save writes back to the same temp file.
        self.assertEqual(len(registered), 1)
        rl.items.append("newcmd")
        registered[0]()
        self.assertEqual(rl.saved, [str(hist)])
        self.assertIn("newcmd", hist.read_text(encoding="utf-8"))

    def test_missing_histfile_is_fine(self):
        self._tty(True)
        rl = FakeReadline()
        ok, _ = self.init(self.tmp / "does-not-exist", rl)
        self.assertTrue(ok)
        self.assertEqual(rl.loaded, [])

    def test_not_a_tty_touches_nothing(self):
        self._tty(False)
        rl = FakeReadline()
        hist = self.tmp / "hist"
        ok, registered = self.init(hist, rl)
        self.assertFalse(ok)
        self.assertEqual(registered, [])
        self.assertFalse(hist.exists())

    def test_readline_missing_falls_back_quietly(self):
        self._tty(True)
        ok, registered = self.init(self.tmp / "hist", None)
        self.assertFalse(ok)
        self.assertEqual(registered, [])

    def test_save_creates_parent_dir(self):
        self._tty(True)
        hist = self.tmp / "sub" / "dir" / "hist"
        rl = FakeReadline()
        ok, registered = self.init(hist, rl)
        self.assertTrue(ok)
        registered[0]()
        self.assertTrue(hist.exists())


class CompleterTests(unittest.TestCase):
    def completer(self, text, state, begidx=0):
        rl = types.SimpleNamespace(get_begidx=lambda: begidx)
        with mock.patch.object(cli, "_readline_module", return_value=rl):
            return cli._slash_completer(text, state)

    def test_completes_prefix(self):
        self.assertEqual(self.completer("/h", 0), "/help")
        self.assertIsNone(self.completer("/h", 1))

    def test_multiple_matches_state_iterates(self):
        self.assertEqual(self.completer("/m", 0), "/model")
        self.assertEqual(self.completer("/m", 1), "/memory")
        self.assertIsNone(self.completer("/m", 2))

    def test_no_match(self):
        self.assertIsNone(self.completer("/zzz", 0))

    def test_bare_slash_lists_all(self):
        first = self.completer("/", 0)
        self.assertEqual(first, "/help")
        names = set()
        state = 0
        while True:
            name = self.completer("/", state)
            if name is None:
                break
            names.add(name)
            state += 1
        self.assertEqual(names, set(cli._slash_completion_names()))

    def test_only_at_line_start(self):
        self.assertIsNone(self.completer("/h", 0, begidx=3))
        self.assertIsNone(self.completer("say /h", 0, begidx=4))

    def test_readline_missing_returns_none(self):
        with mock.patch.object(cli, "_readline_module", return_value=None):
            self.assertIsNone(cli._slash_completer("/h", 0))

    def test_names_come_from_registry(self):
        self.assertEqual(cli._slash_completion_names(), tui.DEFAULT_REGISTRY.command_names())
        for expected in (
            "/help",
            "/model",
            "/status",
            "/goals",
            "/workers",
            "/memory",
            "/clear",
            "/undo",
            "/editor",
            "/exit",
        ):
            self.assertIn(expected, cli._slash_completion_names())

    def test_follows_registry_growth(self):
        registry = tui.default_registry()
        registry.register(tui.CommandSpec("frobnicate", "A test command."))
        with mock.patch.object(tui, "DEFAULT_REGISTRY", registry):
            self.assertIn("/frobnicate", cli._slash_completion_names())
            self.assertEqual(self.completer("/f", 0), "/frobnicate")


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._drafts = list(cli._editor_draft)
        self.addCleanup(cli._editor_draft.__setitem__, slice(None), self._drafts)

    def write_script(self, name, body):
        script = self.tmp / name
        script.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        script.chmod(0o755)
        return str(script)

    def run_editor(self, editor):
        created = []
        real_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            fd, path = real_mkstemp(*args, **kwargs)
            created.append(path)
            return fd, path

        with (
            mock.patch.dict(os.environ, {"EDITOR": editor}),
            mock.patch("tempfile.mkstemp", spy_mkstemp),
        ):
            with mock.patch("sys.stdout"):  # silence local messages
                result = cli._slash_editor([])
        return result, created

    def test_draft_returned_as_turn(self):
        script = self.write_script("ed.sh", 'echo "hello from editor" > "$1"')
        result, created = self.run_editor(script)
        self.assertEqual(result, "handled")
        self.assertEqual(cli._editor_draft, ["hello from editor"])
        # Temp file is cleaned up.
        for path in created:
            self.assertFalse(Path(path).exists())

    def test_empty_draft_sends_nothing(self):
        result, _ = self.run_editor("true")
        self.assertEqual(result, "handled")
        self.assertEqual(cli._editor_draft, [])

    def test_editor_failure_sends_nothing(self):
        result, _ = self.run_editor("false")
        self.assertEqual(result, "handled")
        self.assertEqual(cli._editor_draft, [])

    def test_missing_editor_is_friendly(self):
        result, _ = self.run_editor("/nonexistent/editor-xyz-123")
        self.assertEqual(result, "handled")
        self.assertEqual(cli._editor_draft, [])

    def test_default_editor_falls_back_to_vi(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EDITOR", None)
            with mock.patch("subprocess.run", side_effect=FileNotFoundError) as run_mock:
                with mock.patch("sys.stdout"):
                    result = cli._slash_editor([])
        self.assertEqual(result, "handled")
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.args[0][0], "vi")

    def test_dispatched_through_slash_handler(self):
        script = self.write_script("ed2.sh", 'echo "via dispatch" > "$1"')
        with mock.patch.dict(os.environ, {"EDITOR": script}):
            with mock.patch("sys.stdout"):
                action = cli._handle_slash_command("/editor")
        self.assertEqual(action, "handled")
        self.assertEqual(cli._editor_draft, ["via dispatch"])

    def test_editor_listed_in_help(self):
        self.assertIn("/editor", tui.command_help())


if __name__ == "__main__":
    unittest.main()
