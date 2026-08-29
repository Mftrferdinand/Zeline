"""Contract tests for format-on-write.

The invariant that matters most: **a formatter must never cost the agent its
write.** Every failure mode (missing binary, non-zero exit, timeout, OSError)
is checked to confirm the file content survives and the tool still reports OK.

Also pinned: only already-installed binaries are used (no network side effects
from writing a file), and the operator can override or opt out per extension.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
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
    formatters = importlib.import_module("zeline.formatters")
    tools = importlib.import_module("zeline.tools")
    return cfg, formatters, tools


class FormatterBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.workspace = self.home / "ws"
        self.workspace.mkdir()
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.formatters, self.tools = fresh(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()


class CandidateSelectionTests(FormatterBase):
    def test_known_extensions_have_candidates(self):
        for name in ("a.py", "a.ts", "a.go", "a.rs", "a.json", "a.sh"):
            with self.subTest(file=name):
                self.assertTrue(self.formatters.candidates_for(Path(name)))

    def test_unknown_extension_has_no_candidates(self):
        self.assertEqual(self.formatters.candidates_for(Path("a.wat")), ())
        self.assertEqual(self.formatters.candidates_for(Path("noext")), ())

    def test_extension_match_is_case_insensitive(self):
        self.assertTrue(self.formatters.candidates_for(Path("A.PY")))

    def test_no_candidate_uses_a_package_runner(self):
        """`npx`/`pnpm dlx` would DOWNLOAD code as a side effect of writing a file."""
        banned = {"npx", "pnpm", "yarn", "bunx", "uvx", "pipx"}
        for candidates in self.formatters.DEFAULT_FORMATTERS.values():
            for command in candidates:
                with self.subTest(command=command):
                    self.assertNotIn(command[0], banned)

    def test_every_default_command_targets_the_written_file(self):
        for ext, candidates in self.formatters.DEFAULT_FORMATTERS.items():
            for command in candidates:
                with self.subTest(ext=ext, command=command):
                    self.assertIn("{file}", command)


class OverrideTests(FormatterBase):
    def _save_formatters(self, mapping):
        saved = self.config.config_copy()
        saved["tools"]["formatters"] = mapping
        self.config.save_config(saved)

    def test_operator_override_replaces_the_default(self):
        self._save_formatters({".py": "myfmt --fix {file}"})
        self.assertEqual(
            self.formatters.candidates_for(Path("a.py")),
            (("myfmt", "--fix", "{file}"),),
        )

    def test_override_accepts_a_list_and_normalizes_a_bare_extension(self):
        self._save_formatters({"py": ["myfmt", "{file}"]})
        self.assertEqual(self.formatters.candidates_for(Path("a.py")), (("myfmt", "{file}"),))

    def test_override_without_placeholder_still_targets_only_that_file(self):
        """Otherwise the formatter would reformat the whole project."""
        self._save_formatters({".py": "myfmt"})
        self.assertEqual(self.formatters.candidates_for(Path("a.py")), (("myfmt", "{file}"),))

    def test_empty_override_opts_one_extension_out(self):
        self._save_formatters({".py": ""})
        self.assertEqual(self.formatters.candidates_for(Path("a.py")), ())
        # Other languages keep working.
        self.assertTrue(self.formatters.candidates_for(Path("a.go")))

    def test_format_on_write_false_disables_everything(self):
        saved = self.config.config_copy()
        saved["tools"]["format_on_write"] = False
        self.config.save_config(saved)
        self.assertFalse(self.formatters.enabled())
        target = self.workspace / "a.py"
        target.write_text("x=1\n", encoding="utf-8")
        with mock.patch.object(self.formatters.subprocess, "run") as run:
            self.assertEqual(self.formatters.format_file(target), "")
        run.assert_not_called()


class ResilienceTests(FormatterBase):
    """A formatter must NEVER cost the agent its write."""

    def _py_file(self, content="x   =    1\n"):
        target = self.workspace / "sample.py"
        target.write_text(content, encoding="utf-8")
        return target

    def test_missing_binary_is_a_silent_no_op(self):
        target = self._py_file()
        with mock.patch.object(self.formatters.shutil, "which", return_value=None), \
             mock.patch.object(self.formatters.subprocess, "run") as run:
            note = self.formatters.format_file(target)
        self.assertEqual(note, "")
        run.assert_not_called()
        self.assertEqual(target.read_text(encoding="utf-8"), "x   =    1\n")

    def test_success_reports_which_formatter_ran(self):
        target = self._py_file()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch.object(self.formatters.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), \
             mock.patch.object(self.formatters.subprocess, "run", return_value=completed):
            note = self.formatters.format_file(target)
        self.assertIn("formatted with", note)

    def test_nonzero_exit_surfaces_the_reason_and_keeps_the_file(self):
        """A failing formatter usually means a real syntax error worth reporting."""
        target = self._py_file("def broken(:\n")
        completed = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="error: invalid syntax at line 1\nmore noise\n"
        )
        with mock.patch.object(self.formatters.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), \
             mock.patch.object(self.formatters.subprocess, "run", return_value=completed):
            note = self.formatters.format_file(target)
        self.assertIn("invalid syntax", note)
        self.assertEqual(target.read_text(encoding="utf-8"), "def broken(:\n")

    def test_timeout_is_reported_and_bounded(self):
        target = self._py_file()
        with mock.patch.object(self.formatters.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), \
             mock.patch.object(
                 self.formatters.subprocess, "run",
                 side_effect=subprocess.TimeoutExpired(cmd="ruff", timeout=20),
             ):
            note = self.formatters.format_file(target)
        self.assertIn("timed out", note)
        self.assertEqual(target.read_text(encoding="utf-8"), "x   =    1\n")

    def test_oserror_is_reported_not_raised(self):
        target = self._py_file()
        with mock.patch.object(self.formatters.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), \
             mock.patch.object(self.formatters.subprocess, "run", side_effect=OSError("exec format error")):
            note = self.formatters.format_file(target)
        self.assertIn("could not run", note)

    def test_second_candidate_is_used_when_the_first_is_absent(self):
        target = self._py_file()
        calls: list[str] = []

        def which(name):
            return "/usr/bin/black" if name == "black" else None

        def run(argv, **_kwargs):
            calls.append(argv[0])
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with mock.patch.object(self.formatters.shutil, "which", side_effect=which), \
             mock.patch.object(self.formatters.subprocess, "run", side_effect=run):
            note = self.formatters.format_file(target)
        self.assertEqual(calls, ["/usr/bin/black"])
        self.assertIn("black", note)

    def test_formatter_runs_detached_so_a_kill_cannot_reach_the_agent(self):
        target = self._py_file()
        captured: dict[str, object] = {}

        def run(argv, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with mock.patch.object(self.formatters.shutil, "which", side_effect=lambda n: f"/usr/bin/{n}"), \
             mock.patch.object(self.formatters.subprocess, "run", side_effect=run):
            self.formatters.format_file(target)
        self.assertEqual(captured.get("timeout"), self.formatters.FORMAT_TIMEOUT_SECONDS)
        if os.name != "nt":
            self.assertIs(captured.get("start_new_session"), True)

    def test_missing_file_is_a_no_op(self):
        self.assertEqual(self.formatters.format_file(self.workspace / "gone.py"), "")


class ToolIntegrationTests(FormatterBase):
    def test_write_file_formats_and_reports_it(self):
        with mock.patch.object(self.tools.formatters, "format_file", return_value=" (formatted with ruff)") as fmt:
            result = self.tools._write_file("app.py", "x=1\n", self.workspace)
        self.assertIn("OK, wrote", result)
        self.assertIn("formatted with ruff", result)
        fmt.assert_called_once()

    def test_edit_file_formats_and_reports_it(self):
        target = self.workspace / "app.py"
        target.write_text("old\n", encoding="utf-8")
        with mock.patch.object(self.tools.formatters, "format_file", return_value=" (formatted with ruff)"):
            result = self.tools._edit_file("app.py", "old", "new", self.workspace)
        self.assertIn("edited", result)
        self.assertIn("formatted with ruff", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

    def test_patch_file_inherits_formatting(self):
        target = self.workspace / "app.py"
        target.write_text("old\n", encoding="utf-8")
        with mock.patch.object(self.tools.formatters, "format_file", return_value=" (formatted with ruff)"):
            result = self.tools._patch_file("app.py", "old", "new", self.workspace)
        self.assertIn("patched", result)
        self.assertIn("formatted with ruff", result)

    def test_a_failing_formatter_does_not_turn_a_write_into_an_error(self):
        with mock.patch.object(
            self.tools.formatters, "format_file",
            return_value=" (formatter ruff reported: invalid syntax)",
        ):
            result = self.tools._write_file("app.py", "def broken(:\n", self.workspace)
        self.assertTrue(result.startswith("OK, wrote"))
        self.assertIn("invalid syntax", result)
        self.assertEqual(
            (self.workspace / "app.py").read_text(encoding="utf-8"), "def broken(:\n"
        )

    def test_write_still_succeeds_if_the_formatter_layer_explodes(self):
        """Defence in depth: a bug in formatting must not lose the write."""
        with mock.patch.object(self.tools.formatters, "format_file", side_effect=RuntimeError("boom")):
            result = self.tools._write_file("app.py", "x=1\n", self.workspace)
        # The write itself happened before formatting was attempted.
        self.assertEqual((self.workspace / "app.py").read_text(encoding="utf-8"), "x=1\n")
        self.assertIn("app.py", result)


class ReformatAwareEditErrorTests(FormatterBase):
    """Formatting changes bytes on disk, so a stale old_text must say WHY it failed.

    Real regression: write_file("name = 'old'") then patch old_text="'old'" fails,
    because ruff rewrote it to "name = \"old\"". Without a hint the model retries
    the identical failing edit forever.
    """

    def test_zero_match_on_a_formattable_file_explains_reformatting(self):
        target = self.workspace / "app.py"
        target.write_text('name = "old"\n', encoding="utf-8")
        result = self.tools._edit_file("app.py", "'old'", "'new'", self.workspace)
        self.assertIn("found 0", result)
        self.assertIn("reformatted", result.casefold())
        self.assertIn("read_file", result)

    def test_no_reformat_hint_for_a_non_formattable_extension(self):
        target = self.workspace / "notes.wat"
        target.write_text("hello\n", encoding="utf-8")
        result = self.tools._edit_file("notes.wat", "missing", "x", self.workspace)
        self.assertIn("found 0", result)
        self.assertNotIn("reformatted", result.casefold())

    def test_no_reformat_hint_when_format_on_write_is_disabled(self):
        saved = self.config.config_copy()
        saved["tools"]["format_on_write"] = False
        self.config.save_config(saved)
        target = self.workspace / "app.py"
        target.write_text('name = "old"\n', encoding="utf-8")
        result = self.tools._edit_file("app.py", "'old'", "'new'", self.workspace)
        self.assertIn("found 0", result)
        self.assertNotIn("reformatted", result.casefold())

    def test_ambiguous_match_does_not_blame_reformatting(self):
        target = self.workspace / "app.py"
        target.write_text("x = 1\nx = 1\n", encoding="utf-8")
        result = self.tools._edit_file("app.py", "x = 1", "y = 2", self.workspace)
        self.assertIn("found 2", result)
        self.assertNotIn("reformatted", result.casefold())


class RealFormatterTests(FormatterBase):
    @unittest.skipIf(__import__("shutil").which("ruff") is None, "ruff not installed")
    def test_ruff_actually_reformats_a_real_python_file(self):
        target = self.workspace / "messy.py"
        target.write_text("x   =    1\ny=2\n", encoding="utf-8")
        note = self.formatters.format_file(target)
        self.assertIn("formatted with ruff", note)
        self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\ny = 2\n")


if __name__ == "__main__":
    unittest.main()
