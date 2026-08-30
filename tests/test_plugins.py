"""Contract tests for the plugin hook bus.

A hook sits on the path of *every* tool call, so a careless one is far more
damaging than a careless custom tool. Five invariants carry the weight:

- **A broken hook never breaks the tool call.** Exceptions -- including
  SystemExit -- are captured, the hook is skipped, and the call proceeds.
- **Blocking is explicit.** Only a ``deny()`` sentinel stops a call. None, False,
  0, "" and wrong types all mean "no opinion", so nothing blocks by accident.
- **Rewrites are type-checked.** A before-hook must return a dict and an
  after-hook a string; anything else is discarded rather than corrupting a call.
- **A public gateway is never hooked.** Arbitrary local Python, so ``safe`` gets
  nothing -- and critically, a plugin cannot be used to *unblock* anything there.
- **Order is deterministic.** Sorted filename order, so an operator controls the
  pipeline and two hooks compose predictably.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    cfg = importlib.import_module("zeline.config")
    plugins = importlib.import_module("zeline.plugins")
    cli = importlib.import_module("zeline.cli")
    return cfg, plugins, cli


class PluginBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.plugins, self.cli = fresh(self.home)
        self.dir = self.plugins.ensure_dir()

    def tearDown(self):
        for name in list(sys.modules):
            if name.startswith("_zeline_plugin_"):
                sys.modules.pop(name, None)
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()

    def write(self, name: str, body: str) -> Path:
        target = self.dir / name
        target.write_text(body, encoding="utf-8")
        return target

    def bus(self, profile: str = "full"):
        return self.plugins.PluginBus(profile)


class DiscoveryTests(PluginBase):
    def test_a_file_with_both_hooks_loads(self):
        self.write("p.py", (
            "def on_tool_before(name, args):\n    return None\n\n"
            "def on_tool_after(name, args, result):\n    return None\n"
        ))
        loaded, errors = self.plugins.discover()
        self.assertEqual(errors, [])
        self.assertEqual(len(loaded), 1)
        self.assertIsNotNone(loaded[0].before)
        self.assertIsNotNone(loaded[0].after)

    def test_one_hook_alone_is_enough(self):
        self.write("p.py", "def on_tool_after(name, args, result):\n    return result\n")
        loaded, errors = self.plugins.discover()
        self.assertEqual(errors, [])
        self.assertIsNone(loaded[0].before)
        self.assertIsNotNone(loaded[0].after)

    def test_a_file_with_no_hooks_is_reported(self):
        self.write("p.py", "def something_else():\n    return 1\n")
        loaded, errors = self.plugins.discover()
        self.assertEqual(loaded, [])
        self.assertTrue(any("no hooks" in item for item in errors))

    def test_a_hook_with_the_wrong_arity_is_rejected_at_load_time(self):
        """Better to say so now than to raise TypeError on the first tool call."""
        self.write("p.py", "def on_tool_before(only_one):\n    return None\n")
        loaded, errors = self.plugins.discover()
        self.assertEqual(loaded, [])
        self.assertTrue(any("must accept 2 argument" in item for item in errors))

    def test_a_non_callable_hook_is_reported(self):
        self.write("p.py", "on_tool_before = 'not a function'\n")
        loaded, errors = self.plugins.discover()
        self.assertEqual(loaded, [])
        self.assertTrue(any("not callable" in item for item in errors))

    def test_a_syntax_error_does_not_stop_other_files(self):
        self.write("10-broken.py", "def on_tool_before(:\n")
        self.write("20-good.py", "def on_tool_before(name, args):\n    return None\n")
        loaded, errors = self.plugins.discover()
        self.assertEqual([p.source.name for p in loaded], ["20-good.py"])
        self.assertTrue(any("10-broken.py" in item for item in errors))

    def test_sys_exit_at_import_does_not_kill_the_agent(self):
        self.write("10-exit.py", "import sys\nsys.exit(4)\n")
        self.write("20-good.py", "def on_tool_before(name, args):\n    return None\n")
        loaded, errors = self.plugins.discover()
        self.assertEqual([p.source.name for p in loaded], ["20-good.py"])
        self.assertTrue(any("10-exit.py" in item for item in errors))

    def test_underscore_files_are_skipped(self):
        self.write("_helper.py", "def on_tool_before(name, args):\n    return None\n")
        loaded, errors = self.plugins.discover()
        self.assertEqual((loaded, errors), ([], []))

    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(self.plugins.discover(self.home / "nope"), ([], []))

    def test_disabled_by_config(self):
        self.write("p.py", "def on_tool_before(name, args):\n    return None\n")
        saved = self.config.config_copy()
        saved["tools"]["plugins"] = False
        self.config.save_config(saved)
        self.assertFalse(self.plugins.enabled())
        self.assertEqual(self.plugins.discover(), ([], []))

    def test_directory_is_private(self):
        if os.name == "posix":
            import stat
            self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)


class BeforeHookTests(PluginBase):
    def test_deny_blocks_the_call_and_names_the_file(self):
        self.write("guard.py", (
            "from zeline.plugins import deny\n\n"
            "def on_tool_before(name, args):\n"
            "    if name == 'run_shell':\n"
            "        return deny('no shell here')\n"
            "    return None\n"
        ))
        outcome = self.bus().before("run_shell", {"command": "ls"})
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.denied_by, "guard.py")
        self.assertEqual(outcome.reason, "no shell here")

    def test_a_dict_return_rewrites_the_arguments(self):
        self.write("p.py", (
            "def on_tool_before(name, args):\n"
            "    args['command'] = 'echo safe'\n"
            "    return args\n"
        ))
        outcome = self.bus().before("run_shell", {"command": "rm -rf /"})
        self.assertFalse(outcome.blocked)
        self.assertEqual(outcome.args, {"command": "echo safe"})

    def test_falsy_returns_do_not_block(self):
        """None/False/0/'' must all mean 'no opinion', never a silent block."""
        for value in ("None", "False", "0", "''", "[]", "{}"):
            with self.subTest(value=value):
                self.write("p.py", f"def on_tool_before(name, args):\n    return {value}\n")
                outcome = self.bus().before("read_file", {"path": "a"})
                self.assertFalse(outcome.blocked, value)

    def test_an_empty_dict_return_is_an_intentional_rewrite(self):
        self.write("p.py", "def on_tool_before(name, args):\n    return {}\n")
        outcome = self.bus().before("system_env", {"x": 1})
        self.assertEqual(outcome.args, {})

    def test_a_wrong_type_return_is_ignored(self):
        self.write("p.py", "def on_tool_before(name, args):\n    return 'nonsense'\n")
        outcome = self.bus().before("read_file", {"path": "a"})
        self.assertFalse(outcome.blocked)
        self.assertEqual(outcome.args, {"path": "a"})

    def test_a_raising_hook_is_skipped_and_the_call_proceeds(self):
        self.write("p.py", "def on_tool_before(name, args):\n    raise RuntimeError('hook bug')\n")
        bus = self.bus()
        outcome = bus.before("read_file", {"path": "a"})
        self.assertFalse(outcome.blocked)
        self.assertEqual(outcome.args, {"path": "a"})
        self.assertTrue(any("hook bug" in item for item in bus.runtime_errors))

    def test_a_hook_calling_sys_exit_at_runtime_is_contained(self):
        self.write("p.py", "import sys\n\ndef on_tool_before(name, args):\n    sys.exit(1)\n")
        outcome = self.bus().before("read_file", {"path": "a"})
        self.assertFalse(outcome.blocked)

    def test_hooks_run_in_filename_order_and_compose(self):
        self.write("10-first.py", (
            "def on_tool_before(name, args):\n"
            "    args['trace'] = 'first'\n    return args\n"
        ))
        self.write("20-second.py", (
            "def on_tool_before(name, args):\n"
            "    args['trace'] = args.get('trace', '') + '+second'\n    return args\n"
        ))
        outcome = self.bus().before("system_env", {})
        self.assertEqual(outcome.args["trace"], "first+second")

    def test_an_earlier_deny_stops_later_hooks(self):
        self.write("10-deny.py", (
            "from zeline.plugins import deny\n\n"
            "def on_tool_before(name, args):\n    return deny('stop here')\n"
        ))
        self.write("20-never.py", (
            "def on_tool_before(name, args):\n"
            "    args['ran'] = True\n    return args\n"
        ))
        outcome = self.bus().before("system_env", {})
        self.assertTrue(outcome.blocked)
        self.assertNotIn("ran", outcome.args)

    def test_mutating_the_dict_in_place_cannot_leak_without_a_return(self):
        """Each hook gets a copy, so a mutation without a return is not honoured."""
        self.write("p.py", (
            "def on_tool_before(name, args):\n"
            "    args['sneaky'] = True\n"
            "    return None\n"
        ))
        outcome = self.bus().before("read_file", {"path": "a"})
        self.assertNotIn("sneaky", outcome.args)


class AfterHookTests(PluginBase):
    def test_a_string_return_rewrites_the_output(self):
        self.write("p.py", (
            "def on_tool_after(name, args, result):\n"
            "    return result.replace('secret-token', '[redacted]')\n"
        ))
        out = self.bus().after("read_file", {}, "value: secret-token")
        self.assertEqual(out, "value: [redacted]")

    def test_none_leaves_the_output_untouched(self):
        self.write("p.py", "def on_tool_after(name, args, result):\n    return None\n")
        self.assertEqual(self.bus().after("read_file", {}, "original"), "original")

    def test_a_wrong_type_return_is_ignored(self):
        self.write("p.py", "def on_tool_after(name, args, result):\n    return 12345\n")
        self.assertEqual(self.bus().after("read_file", {}, "original"), "original")

    def test_a_raising_hook_leaves_the_output_intact(self):
        self.write("p.py", "def on_tool_after(name, args, result):\n    raise ValueError('boom')\n")
        bus = self.bus()
        self.assertEqual(bus.after("read_file", {}, "original"), "original")
        self.assertTrue(bus.runtime_errors)

    def test_after_hooks_chain_in_order(self):
        self.write("10-a.py", "def on_tool_after(name, args, result):\n    return result + '|a'\n")
        self.write("20-b.py", "def on_tool_after(name, args, result):\n    return result + '|b'\n")
        self.assertEqual(self.bus().after("x", {}, "start"), "start|a|b")

    def test_runtime_errors_are_bounded(self):
        self.write("p.py", "def on_tool_after(name, args, result):\n    raise ValueError('x')\n")
        bus = self.bus()
        for _ in range(120):
            bus.after("x", {}, "y")
        self.assertLessEqual(len(bus.runtime_errors), 50)


class ProfileTests(PluginBase):
    def test_safe_profile_loads_no_hooks(self):
        self.write("p.py", (
            "from zeline.plugins import deny\n\n"
            "def on_tool_before(name, args):\n    return deny('x')\n"
        ))
        bus = self.bus("safe")
        self.assertFalse(bus.active)
        self.assertFalse(bus.before("run_shell", {}).blocked)

    def test_operator_profiles_load(self):
        self.write("p.py", "def on_tool_before(name, args):\n    return None\n")
        for profile in ("workspace", "full"):
            self.assertTrue(self.bus(profile).active, profile)

    def test_denial_message_tells_the_model_not_to_retry(self):
        outcome = self.plugins.HookOutcome(args={}, denied_by="p.py", reason="policy")
        message = self.plugins.denial_message("run_shell", outcome)
        self.assertIn("run_shell", message)
        self.assertIn("p.py", message)
        self.assertIn("policy", message)
        self.assertIn("do not retry", message)


class ExecutorIntegrationTests(PluginBase):
    def _executor(self, profile: str = "full"):
        tools_module = importlib.import_module("zeline.tools")
        return tools_module.ToolExecutor(
            identity="cli:local", profile=profile, workspace=str(self.home)
        )

    def test_a_plugin_blocks_a_real_tool_call(self):
        self.write("guard.py", (
            "from zeline.plugins import deny\n\n"
            "def on_tool_before(name, args):\n"
            "    if name == 'write_file':\n"
            "        return deny('writes are frozen')\n"
            "    return None\n"
        ))
        executor = self._executor()
        result = executor.run("write_file", {"path": "blocked.txt", "content": "nope"})
        self.assertIn("blocked by plugin", result)
        self.assertIn("writes are frozen", result)
        self.assertFalse((self.home / "blocked.txt").exists())

    def test_a_plugin_rewrites_real_arguments(self):
        self.write("p.py", (
            "def on_tool_before(name, args):\n"
            "    if name == 'write_file':\n"
            "        args['content'] = 'rewritten by plugin'\n"
            "        return args\n"
            "    return None\n"
        ))
        executor = self._executor()
        executor.run("write_file", {"path": "note.txt", "content": "original"})
        self.assertEqual(
            (self.home / "note.txt").read_text(encoding="utf-8"),
            "rewritten by plugin",
        )

    def test_a_plugin_redacts_real_tool_output(self):
        (self.home / "creds.txt").write_text("password=hunter2\n", encoding="utf-8")
        self.write("p.py", (
            "def on_tool_after(name, args, result):\n"
            "    return result.replace('hunter2', '[redacted]')\n"
        ))
        executor = self._executor()
        result = executor.run("read_file", {"path": "creds.txt"})
        self.assertNotIn("hunter2", result)
        self.assertIn("[redacted]", result)

    def test_hooks_also_wrap_custom_tools(self):
        """Governance must cover every kind of tool, not only native ones."""
        custom_dir = importlib.import_module("zeline.custom_tools").ensure_dir()
        (custom_dir / "t.py").write_text(
            "def spill() -> str:\n    return 'TOP SECRET'\n", encoding="utf-8"
        )
        self.write("p.py", (
            "def on_tool_after(name, args, result):\n"
            "    return result.replace('TOP SECRET', '[hidden]')\n"
        ))
        executor = self._executor()
        self.assertEqual(executor.run("custom_spill", {}), "[hidden]")

    def test_a_broken_plugin_leaves_tools_working(self):
        self.write("broken.py", "def on_tool_before(:\n")
        executor = self._executor()
        result = executor.run("write_file", {"path": "still.txt", "content": "works"})
        self.assertTrue(result.startswith("OK"), result)
        self.assertEqual((self.home / "still.txt").read_text(encoding="utf-8"), "works")

    def test_safe_profile_tool_calls_are_unhooked(self):
        self.write("p.py", (
            "from zeline.plugins import deny\n\n"
            "def on_tool_before(name, args):\n    return deny('blocked')\n"
        ))
        executor = self._executor("safe")
        self.assertNotIn("blocked by plugin", executor.run("system_env", {}))


class CliTests(PluginBase):
    def test_list_on_empty_directory_explains_what_to_do(self):
        self.assertEqual(self.cli.cmd_plugins("list"), 0)

    def test_list_shows_hooks_and_errors_together(self):
        self.write("10-good.py", (
            "def on_tool_before(name, args):\n    return None\n\n"
            "def on_tool_after(name, args, result):\n    return None\n"
        ))
        self.write("20-broken.py", "def on_tool_before(:\n")
        self.assertEqual(self.cli.cmd_plugins("list"), 0)

    def test_init_scaffolds_a_loadable_file(self):
        self.assertEqual(self.cli.cmd_plugins("init"), 0)
        created = self.dir / "10-policy.py"
        self.assertTrue(created.is_file())
        loaded, errors = self.plugins.discover()
        self.assertEqual(errors, [])
        self.assertEqual(len(loaded), 1)

    def test_init_refuses_to_overwrite(self):
        self.assertEqual(self.cli.cmd_plugins("init"), 0)
        self.assertEqual(self.cli.cmd_plugins("init"), 1)

    def test_init_accepts_a_name(self):
        self.assertEqual(self.cli.cmd_plugins("init", name="99-late.py"), 0)
        self.assertTrue((self.dir / "99-late.py").is_file())

    def test_path_prints_the_directory(self):
        self.assertEqual(self.cli.cmd_plugins("path"), 0)

    def test_disabled_flag_is_honoured(self):
        saved = self.config.config_copy()
        saved["tools"]["plugins"] = False
        self.config.save_config(saved)
        self.assertEqual(self.cli.cmd_plugins("list"), 0)

    def test_cli_exposes_the_subcommands(self):
        parser = self.cli.build_parser()
        self.assertEqual(parser.parse_args(["plugins"]).command, "plugins")
        namespace = parser.parse_args(["plugins", "init", "x.py"])
        self.assertEqual(namespace.plugins_command, "init")
        self.assertEqual(namespace.name, "x.py")
        self.assertEqual(parser.parse_args(["plugins", "path"]).plugins_command, "path")

    def test_the_shipped_template_actually_governs_something(self):
        """The scaffold must work on first use, or it teaches the wrong lesson."""
        self.write("10-policy.py", self.plugins.TEMPLATE)
        bus = self.bus()
        blocked = bus.before("run_shell", {"command": "sudo rm -rf / --no-preserve-root"})
        self.assertTrue(blocked.blocked)
        allowed = bus.before("run_shell", {"command": "ls -la"})
        self.assertFalse(allowed.blocked)
        long_output = "x" * 25_000
        trimmed = bus.after("run_shell", {}, long_output)
        self.assertLess(len(trimmed), len(long_output))
        self.assertIn("truncated", trimmed)


if __name__ == "__main__":
    unittest.main()
