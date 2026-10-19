"""Required execution policies are distinct from best-effort observer plugins."""
from pathlib import Path
import importlib
import json
import os
import sys
import tempfile
import warnings
import unittest
from unittest.mock import Mock, patch


class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"ZELINE_HOME": str(self.home)})
        self.env.start()
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self.tools = importlib.import_module("zeline.tools")
        (self.home / "policies").mkdir(mode=0o700)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def require(self, *names):
        (self.home / "policies.json").write_text(json.dumps({"required": list(names)}))

    def test_missing_required_policy_blocks_real_tool_side_effect(self):
        self.require("missing.py")
        executor = self.tools.ToolExecutor("fixture:policy", profile="full", workspace=self.home)
        target = self.home / "must-not-exist"
        result = executor.run("write_file", {"path": str(target), "content": "fixture"})
        self.assertTrue(result.startswith("ERROR: execution policy"), result)
        self.assertFalse(target.exists())

    def test_explicit_authorization_allows_real_tool_side_effect(self):
        self.require("allow.py")
        (self.home / "policies" / "allow.py").write_text(
            "def authorize(context):\n    return context['tool'] == 'write_file'\n"
        )
        executor = self.tools.ToolExecutor("fixture:policy", profile="full", workspace=self.home)
        target = self.home / "allowed"
        result = executor.run("write_file", {"path": str(target), "content": "fixture"})
        self.assertTrue(result.startswith("OK"), result)
        self.assertEqual(target.read_text(), "fixture")

    def test_output_observer_cannot_rewrite_policy_denial_to_success(self):
        self.require("missing.py")
        directory = self.home / "plugins"
        directory.mkdir()
        (directory / "rewrite.py").write_text(
            "def on_tool_after(name, args, result):\n    return 'OK fixture'\n"
        )
        executor = self.tools.ToolExecutor("fixture:policy", profile="full", workspace=self.home)
        result = executor.run("write_file", {"path": str(self.home / "blocked"), "content": "fixture"})
        self.assertTrue(result.startswith("ERROR: execution policy"), result)
        self.assertFalse((self.home / "blocked").exists())

    def test_policy_engine_failure_is_denial_not_exception_or_dispatch(self):
        executor = self.tools.ToolExecutor("fixture:policy", profile="full", workspace=self.home)
        executor.execution_policy.check = Mock(side_effect=RuntimeError("PRIVATE_FIXTURE"))
        with patch.object(executor, "_dispatch") as dispatch:
            try:
                result = executor.run("write_file", {"path": "fixture", "content": "fixture"})
            except RuntimeError:
                result = "unexpected exception escaped"
        self.assertTrue(result.startswith("ERROR: execution policy"), result)
        self.assertNotIn("PRIVATE_FIXTURE", result)
        dispatch.assert_not_called()

    def test_duplicate_registry_keys_cannot_disable_required_policies(self):
        (self.home / "policies.json").write_text('{"required":["missing.py"],"required":[]}')
        executor = self.tools.ToolExecutor("fixture:policy", profile="full", workspace=self.home)
        with patch.object(executor, "_dispatch", return_value="OK fixture") as dispatch:
            result = executor.run("read_file", {"path": "fixture"})
        self.assertTrue(result.startswith("ERROR: execution policy"), result)
        dispatch.assert_not_called()

    def test_missing_registry_is_optional_until_observed_then_fails_closed(self):
        engine = importlib.import_module("zeline.policies").PolicyEngine("fixture", "safe", self.home)
        self.assertIsNone(engine.check("read_file", {}))
        self.require()
        self.assertIsNone(engine.check("read_file", {}))
        (self.home / "policies.json").unlink()
        self.assertIsNotNone(engine.check("read_file", {}))

    def test_invalid_registries_are_denied(self):
        engine = importlib.import_module("zeline.policies").PolicyEngine("fixture", "safe", self.home)
        cases = ['{broken', '[]', '{}', '{"required":true}', '{"required":["../escape.py"]}',
                 '{"required":["/outside.py"]}', '{"required":["a.py","a.py"]}',
                 '{"required":[],"unknown":true}', 'x' * 16385]
        for raw in cases:
            with self.subTest(raw=raw[:80]):
                (self.home / "policies.json").write_text(raw)
                self.assertIsNotNone(engine.check("read_file", {}))

    def test_broken_hooks_and_non_boolean_decisions_never_dispatch(self):
        self.require("check.py")
        executor = self.tools.ToolExecutor("fixture:policy", profile="full", workspace=self.home)
        cases = ['raise SystemExit("PRIVATE_FIXTURE")', 'not valid python !!!',
                 'authorize = 123', 'def authorize():\n    return True',
                 'def authorize(context):\n    raise RuntimeError("PRIVATE_FIXTURE")']
        cases += [f'def authorize(context):\n    return {value}' for value in
                  ['None', 'False', '1', '"allow"', '{"allow":True}']]
        for body in cases:
            with self.subTest(body=body):
                (self.home / "policies" / "check.py").write_text(body)
                with patch.object(executor, "_dispatch") as dispatch:
                    result = executor.run("read_file", {"path": "fixture"})
                self.assertTrue(result.startswith("ERROR: execution policy"), result)
                self.assertNotIn("PRIVATE_FIXTURE", result)
                dispatch.assert_not_called()
        self.assertFalse(any(key.startswith("_zeline_required_policy_") for key in sys.modules))

    def test_required_policy_applies_to_safe_and_disabled_observer_profiles(self):
        self.require("missing.py")
        cfg = importlib.import_module("zeline.config")
        with patch.object(cfg, "PLUGINS", False):
            for profile in ["safe", "workspace", "full"]:
                executor = self.tools.ToolExecutor("fixture", profile=profile, workspace=self.home)
                with patch.object(executor, "_dispatch") as dispatch:
                    self.assertTrue(executor.run("read_file", {}).startswith("ERROR: execution policy"))
                dispatch.assert_not_called()

    def test_policy_checks_effective_arguments_after_observer_rewrite(self):
        self.require("check.py")
        (self.home / "policies" / "check.py").write_text(
            "def authorize(context):\n    return context['args'].get('path') != 'blocked'\n"
        )
        directory = self.home / "plugins"
        directory.mkdir()
        (directory / "rewrite.py").write_text(
            "def on_tool_before(name, args):\n    return {'path':'blocked'}\n"
        )
        executor = self.tools.ToolExecutor("fixture", profile="full", workspace=self.home)
        with patch.object(executor, "_dispatch") as dispatch:
            result = executor.run("read_file", {"path": "allowed"})
        self.assertTrue(result.startswith("ERROR: execution policy"))
        dispatch.assert_not_called()

    def test_policy_context_isolated_and_all_required_rules_must_allow(self):
        self.require("first.py", "second.py")
        (self.home / "policies" / "first.py").write_text(
            "def authorize(context):\n    context['args']['nested']['x'] = 99\n    return True\n"
        )
        (self.home / "policies" / "second.py").write_text(
            "def authorize(context):\n    return context['args']['nested']['x'] == 1 and context['profile'] == 'full'\n"
        )
        executor = self.tools.ToolExecutor("fixture", profile="full", workspace=self.home)
        original = {"nested": {"x": 1}}
        with patch.object(executor, "_dispatch", return_value="OK fixture") as dispatch:
            self.assertEqual(executor.run("read_file", original), "OK fixture")
        dispatch.assert_called_once_with("read_file", {"nested": {"x": 1}})
        self.assertEqual(original, {"nested": {"x": 1}})
        (self.home / "policies" / "second.py").write_text('def authorize(context):\n    return False\n')
        with patch.object(executor, "_dispatch") as dispatch:
            self.assertTrue(executor.run("read_file", original).startswith("ERROR: execution policy"))
        dispatch.assert_not_called()

    def test_policy_changes_recheck_source_and_do_not_reuse_timestamp_bytecode(self):
        self.require("check.py")
        source = self.home / "policies" / "check.py"
        source.write_text('def authorize(context):\n    return True \n')
        stamp = source.stat().st_mtime_ns
        executor = self.tools.ToolExecutor("fixture", profile="full", workspace=self.home)
        with patch.object(executor, "_dispatch", return_value="OK fixture"):
            self.assertEqual(executor.run("read_file", {}), "OK fixture")
        source.write_text('def authorize(context):\n    return False\n')
        os.utime(source, ns=(stamp, stamp))
        with patch.object(executor, "_dispatch") as dispatch:
            self.assertTrue(executor.run("read_file", {}).startswith("ERROR: execution policy"))
        dispatch.assert_not_called()

    def test_all_dispatch_routes_and_internal_reflection_share_policy_gate(self):
        self.require("missing.py")
        executor = self.tools.ToolExecutor("fixture", profile="full", workspace=self.home)
        executor._reflection_mode = True
        with patch.object(executor, "_dispatch") as dispatch:
            for name in ["run_shell", "custom_fixture", "mcp_fixture", "openapi_fixture", "manage_skill"]:
                self.assertTrue(executor.run(name, {}).startswith("ERROR: execution policy"))
        dispatch.assert_not_called()

    def test_async_policy_is_rejected_without_leaking_unawaited_coroutines(self):
        self.require("async_rule.py")
        engine = importlib.import_module("zeline.policies").PolicyEngine("fixture", "safe", self.home)
        for body in ['async def authorize(context):\n    return True\n',
                     'async def check():\n    return True\ndef authorize(context):\n    return check()\n']:
            (self.home / "policies" / "async_rule.py").write_text(body)
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                self.assertIsNotNone(engine.check("read_file", {}))
            self.assertFalse([item for item in captured if issubclass(item.category, RuntimeWarning)])

    @unittest.skipUnless(os.name != "nt" and hasattr(os, "symlink"), "requires POSIX symlinks")
    def test_registry_and_source_symlinks_cannot_escape_private_policy_root(self):
        self.require("link.py")
        outside = self.home / "outside.py"
        outside.write_text('def authorize(context):\n    return True\n')
        (self.home / "policies" / "link.py").symlink_to(outside)
        engine = importlib.import_module("zeline.policies").PolicyEngine("fixture", "safe", self.home)
        self.assertIsNotNone(engine.check("read_file", {}))
        registry = self.home / "policies.json"
        registry.unlink()
        registry.symlink_to(outside)
        self.assertIsNotNone(engine.check("read_file", {}))
