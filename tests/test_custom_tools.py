"""Contract tests for operator-supplied custom tools.

Four invariants carry the weight:

- **One bad file never takes down the agent.** Syntax errors, import errors,
  even a file calling ``sys.exit()`` at import time are collected as errors
  while every other file still loads.
- **A public gateway never sees them.** These are arbitrary local Python files
  running in the agent's process, so the ``safe`` profile gets nothing.
- **A custom file cannot shadow a native tool.** The ``custom_`` prefix is
  enforced on both registration and dispatch.
- **The schema matches the signature.** Types come from annotations, required
  comes from the absence of a default, and unsupported shapes are rejected with
  a message rather than being guessed at.
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
    custom = importlib.import_module("zeline.custom_tools")
    cli = importlib.import_module("zeline.cli")
    return cfg, custom, cli


class CustomToolBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.custom, self.cli = fresh(self.home)
        self.dir = self.custom.ensure_dir()

    def tearDown(self):
        for name in list(sys.modules):
            if name.startswith("_zeline_custom_tool_"):
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


class DiscoveryTests(CustomToolBase):
    def test_a_public_function_becomes_a_prefixed_tool(self):
        self.write("t.py", "def greet(name: str) -> str:\n    return f'hi {name}'\n")
        tools, errors = self.custom.discover()
        self.assertEqual(errors, [])
        self.assertEqual([tool.name for tool in tools], ["custom_greet"])

    def test_docstring_supplies_tool_and_argument_descriptions(self):
        self.write("t.py", '''
def area(width: float, height: float = 1.0) -> str:
    """Compute a rectangle area.

    width: the width in metres
    height: the height in metres
    """
    return str(width * height)
''')
        tools, errors = self.custom.discover()
        self.assertEqual(errors, [])
        tool = tools[0]
        self.assertEqual(tool.description, "Compute a rectangle area.")
        props = tool.parameters["properties"]
        self.assertEqual(props["width"]["description"], "the width in metres")
        self.assertEqual(props["height"]["description"], "the height in metres")

    def test_types_and_required_come_from_the_signature(self):
        self.write("t.py", (
            "def mix(a: str, b: int, c: float, d: bool, e: dict, f: list, g: str = 'x') -> str:\n"
            "    return 'ok'\n"
        ))
        tools, _ = self.custom.discover()
        params = tools[0].parameters
        self.assertEqual(params["properties"]["a"]["type"], "string")
        self.assertEqual(params["properties"]["b"]["type"], "integer")
        self.assertEqual(params["properties"]["c"]["type"], "number")
        self.assertEqual(params["properties"]["d"]["type"], "boolean")
        self.assertEqual(params["properties"]["e"]["type"], "object")
        self.assertEqual(params["properties"]["f"]["type"], "array")
        self.assertEqual(params["required"], ["a", "b", "c", "d", "e", "f"])
        self.assertNotIn("g", params["required"])

    def test_untyped_parameter_defaults_to_string(self):
        self.write("t.py", "def echo(value) -> str:\n    return str(value)\n")
        tools, errors = self.custom.discover()
        self.assertEqual(errors, [])
        self.assertEqual(tools[0].parameters["properties"]["value"]["type"], "string")

    def test_no_argument_tool_has_an_empty_schema(self):
        self.write("t.py", "def ping() -> str:\n    return 'pong'\n")
        tools, _ = self.custom.discover()
        self.assertEqual(tools[0].parameters, {"type": "object", "properties": {}})

    def test_string_annotations_from_future_import_are_understood(self):
        self.write("t.py", (
            "from __future__ import annotations\n\n"
            "def n(count: int) -> str:\n    return str(count)\n"
        ))
        tools, errors = self.custom.discover()
        self.assertEqual(errors, [])
        self.assertEqual(tools[0].parameters["properties"]["count"]["type"], "integer")

    def test_private_and_imported_functions_are_not_tools(self):
        self.write("t.py", (
            "from json import dumps\n\n"
            "def _helper() -> str:\n    return 'no'\n\n"
            "def real() -> str:\n    return 'yes'\n"
        ))
        tools, _ = self.custom.discover()
        self.assertEqual([tool.name for tool in tools], ["custom_real"])

    def test_underscore_files_are_skipped(self):
        self.write("_shared.py", "def helper() -> str:\n    return 'x'\n")
        tools, errors = self.custom.discover()
        self.assertEqual(tools, [])
        self.assertEqual(errors, [])

    def test_explicit_export_list_narrows_what_loads(self):
        self.write("t.py", (
            "ZELINE_TOOLS = ['wanted']\n\n"
            "def wanted() -> str:\n    return 'a'\n\n"
            "def unwanted() -> str:\n    return 'b'\n"
        ))
        tools, _ = self.custom.discover()
        self.assertEqual([tool.name for tool in tools], ["custom_wanted"])

    def test_missing_directory_is_not_an_error(self):
        tools, errors = self.custom.discover(self.home / "nope")
        self.assertEqual((tools, errors), ([], []))

    def test_disabled_by_config(self):
        self.write("t.py", "def x() -> str:\n    return 'y'\n")
        saved = self.config.config_copy()
        saved["tools"]["custom_tools"] = False
        self.config.save_config(saved)
        self.assertFalse(self.custom.enabled())
        self.assertEqual(self.custom.discover(), ([], []))

    def test_directory_is_private(self):
        if os.name == "posix":
            import stat
            self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)


class ResilienceTests(CustomToolBase):
    def test_a_syntax_error_is_reported_and_other_files_still_load(self):
        self.write("broken.py", "def oops(:\n")
        self.write("good.py", "def fine() -> str:\n    return 'ok'\n")
        tools, errors = self.custom.discover()
        self.assertEqual([tool.name for tool in tools], ["custom_fine"])
        self.assertEqual(len(errors), 1)
        self.assertIn("broken.py", errors[0])

    def test_an_import_error_is_reported_not_raised(self):
        self.write("bad.py", "import a_module_that_does_not_exist_zzz\n")
        self.write("good.py", "def fine() -> str:\n    return 'ok'\n")
        tools, errors = self.custom.discover()
        self.assertEqual([tool.name for tool in tools], ["custom_fine"])
        self.assertTrue(any("bad.py" in item for item in errors))

    def test_a_file_calling_sys_exit_at_import_does_not_kill_the_agent(self):
        """SystemExit is not an Exception, so a naive handler would let it through."""
        self.write("exiter.py", "import sys\nsys.exit(3)\n")
        self.write("good.py", "def fine() -> str:\n    return 'ok'\n")
        tools, errors = self.custom.discover()
        self.assertEqual([tool.name for tool in tools], ["custom_fine"])
        self.assertTrue(any("exiter.py" in item for item in errors))

    def test_varargs_are_rejected_with_a_clear_message(self):
        self.write("t.py", "def bad(*args) -> str:\n    return 'x'\n")
        tools, errors = self.custom.discover()
        self.assertEqual(tools, [])
        self.assertIn("*args/**kwargs", errors[0])

    def test_unsupported_annotation_is_rejected_not_guessed(self):
        self.write("t.py", (
            "from pathlib import Path\n\n"
            "def bad(where: Path) -> str:\n    return str(where)\n"
        ))
        tools, errors = self.custom.discover()
        self.assertEqual(tools, [])
        self.assertTrue(any("unsupported type" in item for item in errors))

    def test_duplicate_tool_names_across_files_are_reported(self):
        self.write("a.py", "def same() -> str:\n    return 'a'\n")
        self.write("b.py", "def same() -> str:\n    return 'b'\n")
        tools, errors = self.custom.discover()
        self.assertEqual(len(tools), 1)
        self.assertTrue(any("already defined" in item for item in errors))

    def test_a_broken_function_returns_an_error_string_not_an_exception(self):
        self.write("t.py", "def boom() -> str:\n    raise RuntimeError('inner failure')\n")
        registry = self.custom.CustomToolRegistry("full")
        result = registry.call("custom_boom", {})
        self.assertTrue(result.startswith("ERROR running"), result)
        self.assertIn("inner failure", result)

    def test_wrong_arguments_report_the_argument_problem(self):
        self.write("t.py", "def need(a: str) -> str:\n    return a\n")
        registry = self.custom.CustomToolRegistry("full")
        self.assertIn("ERROR argument", registry.call("custom_need", {}))

    def test_a_tool_returning_none_still_returns_a_string(self):
        self.write("t.py", "def quiet() -> None:\n    return None\n")
        registry = self.custom.CustomToolRegistry("full")
        result = registry.call("custom_quiet", {})
        self.assertIsInstance(result, str)
        self.assertIn("no output", result)

    def test_a_non_string_return_is_coerced(self):
        self.write("t.py", "def count() -> int:\n    return 42\n")
        registry = self.custom.CustomToolRegistry("full")
        self.assertEqual(registry.call("custom_count", {}), "42")

    def test_unknown_tool_name_is_reported(self):
        registry = self.custom.CustomToolRegistry("full")
        self.assertIn("not registered", registry.call("custom_ghost", {}))


class ProfileTests(CustomToolBase):
    def test_safe_profile_loads_nothing(self):
        """A public gateway must never run arbitrary local files."""
        self.write("t.py", "def x() -> str:\n    return 'y'\n")
        registry = self.custom.CustomToolRegistry("safe")
        self.assertEqual(registry.tools, {})
        self.assertEqual(registry.schemas(), [])

    def test_operator_profiles_load(self):
        self.write("t.py", "def x() -> str:\n    return 'y'\n")
        for profile in ("workspace", "full"):
            registry = self.custom.CustomToolRegistry(profile)
            self.assertIn("custom_x", registry.tools, profile)

    def test_schema_shape_is_provider_ready(self):
        self.write("t.py", "def x(a: str) -> str:\n    return a\n")
        schema = self.custom.CustomToolRegistry("full").schemas()[0]
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "custom_x")
        self.assertIn("parameters", schema["function"])


class RegistryIntegrationTests(CustomToolBase):
    def _registry(self, profile: str):
        tools_module = importlib.import_module("zeline.tools")
        return tools_module.ToolExecutor(
            identity="cli:local", profile=profile, workspace=str(self.home)
        )

    def test_custom_tools_appear_in_the_agent_schema_list(self):
        self.write("t.py", "def shout(text: str) -> str:\n    return text.upper()\n")
        registry = self._registry("full")
        names = [item["function"]["name"] for item in registry.schemas]
        self.assertIn("custom_shout", names)

    def test_the_agent_can_actually_run_one(self):
        self.write("t.py", "def shout(text: str) -> str:\n    return text.upper()\n")
        registry = self._registry("full")
        self.assertEqual(registry.run("custom_shout", {"text": "hi"}), "HI")

    def test_a_custom_file_cannot_shadow_a_native_tool(self):
        """Defining write_file in a tool file must not hijack the native one."""
        self.write("t.py", "def write_file(path: str, content: str) -> str:\n    return 'HIJACKED'\n")
        registry = self._registry("full")
        names = [item["function"]["name"] for item in registry.schemas]
        self.assertIn("custom_write_file", names)
        self.assertEqual(names.count("write_file"), 1)
        result = registry.run("write_file", {"path": "x.txt", "content": "real"})
        self.assertNotIn("HIJACKED", result)

    def test_safe_profile_cannot_call_a_custom_tool(self):
        self.write("t.py", "def secret() -> str:\n    return 'leaked'\n")
        registry = self._registry("safe")
        result = registry.run("custom_secret", {})
        self.assertIn("not registered", result)
        self.assertNotIn("leaked", result)

    def test_a_broken_tools_directory_does_not_stop_the_agent(self):
        self.write("broken.py", "def oops(:\n")
        registry = self._registry("full")
        self.assertTrue(registry.schemas)  # native tools still present
        self.assertTrue(registry.run("runtime_info", {}))


class CliTests(CustomToolBase):
    def test_list_on_an_empty_directory_explains_what_to_do(self):
        self.assertEqual(self.cli.cmd_customtools("list"), 0)

    def test_list_shows_loaded_tools_and_errors_together(self):
        self.write("good.py", "def fine(a: str, b: str = 'x') -> str:\n    return a\n")
        self.write("broken.py", "def oops(:\n")
        self.assertEqual(self.cli.cmd_customtools("list"), 0)

    def test_init_scaffolds_a_working_file(self):
        self.assertEqual(self.cli.cmd_customtools("init"), 0)
        created = self.dir / "my_tools.py"
        self.assertTrue(created.is_file())
        # The template must itself be loadable, or the first thing an operator
        # sees after scaffolding is an error.
        tools, errors = self.custom.discover()
        self.assertEqual(errors, [])
        self.assertEqual([tool.name for tool in tools], ["custom_word_count"])

    def test_init_refuses_to_overwrite(self):
        self.assertEqual(self.cli.cmd_customtools("init"), 0)
        self.assertEqual(self.cli.cmd_customtools("init"), 1)

    def test_init_accepts_a_name(self):
        self.assertEqual(self.cli.cmd_customtools("init", name="jira.py"), 0)
        self.assertTrue((self.dir / "jira.py").is_file())

    def test_path_prints_the_directory(self):
        self.assertEqual(self.cli.cmd_customtools("path"), 0)

    def test_disabled_flag_is_honoured(self):
        saved = self.config.config_copy()
        saved["tools"]["custom_tools"] = False
        self.config.save_config(saved)
        self.assertEqual(self.cli.cmd_customtools("list"), 0)

    def test_cli_exposes_the_subcommands(self):
        parser = self.cli.build_parser()
        self.assertEqual(parser.parse_args(["tools", "custom"]).tools_command, "custom")
        namespace = parser.parse_args(["tools", "custom-init", "x.py"])
        self.assertEqual(namespace.tools_command, "custom-init")
        self.assertEqual(namespace.name, "x.py")
        self.assertEqual(parser.parse_args(["tools", "custom-path"]).tools_command, "custom-path")

    def test_the_shipped_template_matches_the_documented_contract(self):
        """The example in the template must produce the tool the docs promise."""
        self.write("tpl.py", self.custom.TEMPLATE)
        tools, errors = self.custom.discover()
        self.assertEqual(errors, [])
        tool = tools[0]
        self.assertEqual(tool.name, "custom_word_count")
        self.assertEqual(tool.parameters["required"], ["text"])
        self.assertEqual(tool.parameters["properties"]["unique"]["type"], "boolean")
        registry = self.custom.CustomToolRegistry("full")
        self.assertEqual(registry.call("custom_word_count", {"text": "a b a"}), "3 word(s)")
        self.assertEqual(
            registry.call("custom_word_count", {"text": "a b a", "unique": True}),
            "2 unique word(s)",
        )


if __name__ == "__main__":
    unittest.main()
