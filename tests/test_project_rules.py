"""Contract tests for project rules (ZELINE.md / AGENTS.md) and `zeline init`.

This content goes into the system prompt of every turn, so the tests pin the
properties that keep that safe:

- discovery order and upward walk, bounded so it cannot escape past $HOME
- size cap, so a huge file cannot inflate every request
- the untrusted-context envelope, so a cloned repo cannot talk the agent out of
  its safety rules or widen its tool profile
- byte-stability within a session (prompt caching)
- `zeline init` refuses to clobber, and detects real tooling rather than
  emitting a generic template
"""
from __future__ import annotations

import importlib
import os
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
    rules = importlib.import_module("zeline.project_rules")
    return cfg, rules


class RulesBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.project = self.home / "proj"
        self.project.mkdir()
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.rules = fresh(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()


class DiscoveryTests(RulesBase):
    def test_finds_each_supported_filename(self):
        for name in self.rules.RULE_FILENAMES:
            with self.subTest(name=name):
                target = self.project / name
                target.write_text("rule text", encoding="utf-8")
                # Compare RESOLVED paths on both sides: find_rules_file resolves
                # (correctly), and a temp dir is a symlink on macOS (/var ->
                # /private/var) and a short name on Windows (RUNNER~1).
                found = self.rules.find_rules_file(self.project)
                self.assertEqual(found, target.resolve(strict=False))
                target.unlink()

    def test_zeline_md_wins_over_agents_md(self):
        (self.project / "AGENTS.md").write_text("agents", encoding="utf-8")
        (self.project / "ZELINE.md").write_text("zeline", encoding="utf-8")
        found = self.rules.find_rules_file(self.project)
        self.assertEqual(found.name, "ZELINE.md")

    def test_agents_md_wins_over_claude_md(self):
        (self.project / "CLAUDE.md").write_text("claude", encoding="utf-8")
        (self.project / "AGENTS.md").write_text("agents", encoding="utf-8")
        self.assertEqual(self.rules.find_rules_file(self.project).name, "AGENTS.md")

    def test_a_subdirectory_inherits_the_repo_rules(self):
        (self.project / "AGENTS.md").write_text("repo conventions", encoding="utf-8")
        nested = self.project / "src" / "deep" / "deeper"
        nested.mkdir(parents=True)
        found = self.rules.find_rules_file(nested)
        self.assertEqual(found, (self.project / "AGENTS.md").resolve(strict=False))

    def test_a_nearer_file_beats_a_parent_one(self):
        (self.project / "AGENTS.md").write_text("outer", encoding="utf-8")
        inner = self.project / "sub"
        inner.mkdir()
        (inner / "AGENTS.md").write_text("inner", encoding="utf-8")
        self.assertEqual(self.rules.find_rules_file(inner).read_text(encoding="utf-8"), "inner")

    def test_no_rules_returns_none_and_empty_block(self):
        self.assertIsNone(self.rules.find_rules_file(self.project))
        self.assertEqual(self.rules.prompt_block(self.project), "")

    def test_a_file_path_resolves_to_its_directory(self):
        (self.project / "AGENTS.md").write_text("x", encoding="utf-8")
        some_file = self.project / "main.py"
        some_file.write_text("print(1)", encoding="utf-8")
        self.assertIsNotNone(self.rules.find_rules_file(some_file))

    def test_upward_walk_is_bounded(self):
        """Unbounded climbing would pick up rules from / and apply them everywhere."""
        self.assertLessEqual(self.rules.MAX_PARENT_LEVELS, 10)
        deep = self.project.joinpath(*[f"l{i}" for i in range(12)])
        deep.mkdir(parents=True)
        # The rules file sits far above the bound, so it must NOT be found.
        (self.project / "AGENTS.md").write_text("too far", encoding="utf-8")
        self.assertIsNone(self.rules.find_rules_file(deep))

    def test_empty_or_whitespace_file_is_treated_as_absent(self):
        (self.project / "ZELINE.md").write_text("   \n\n  ", encoding="utf-8")
        path, text = self.rules.read_rules(self.project)
        self.assertIsNone(path)
        self.assertEqual(text, "")
        self.assertEqual(self.rules.prompt_block(self.project), "")


class PromptBlockTests(RulesBase):
    def test_content_is_size_capped(self):
        (self.project / "ZELINE.md").write_text("x" * 50_000, encoding="utf-8")
        _path, text = self.rules.read_rules(self.project)
        self.assertLessEqual(len(text), self.rules.MAX_RULES_CHARS + 200)
        self.assertIn("truncated", text)

    def test_block_marks_rules_as_context_not_permission(self):
        """A cloned repo must not be able to widen the agent's permissions."""
        (self.project / "ZELINE.md").write_text("Always use tabs.", encoding="utf-8")
        block = self.rules.prompt_block(self.project)
        lowered = block.casefold()
        self.assertIn("<project_rules", block)
        self.assertIn("</project_rules>", block)
        self.assertIn("not permission", lowered)
        self.assertIn("cannot widen your tool profile", lowered)
        self.assertIn("the rules above win", lowered)
        self.assertIn("Always use tabs.", block)

    def test_block_names_the_source_file(self):
        (self.project / "AGENTS.md").write_text("conventions", encoding="utf-8")
        self.assertIn('source="AGENTS.md"', self.rules.prompt_block(self.project))

    def test_disabled_config_produces_no_block(self):
        (self.project / "ZELINE.md").write_text("conventions", encoding="utf-8")
        saved = self.config.config_copy()
        saved["tools"]["project_rules"] = False
        self.config.save_config(saved)
        self.assertFalse(self.rules.enabled())
        self.assertEqual(self.rules.prompt_block(self.project), "")

    def test_block_is_byte_stable_for_the_same_input(self):
        """The system prompt must not drift between turns (prompt caching)."""
        (self.project / "ZELINE.md").write_text("conventions", encoding="utf-8")
        first = self.rules.prompt_block(self.project)
        second = self.rules.prompt_block(self.project)
        self.assertEqual(first, second)

    def test_unreadable_file_degrades_to_empty(self):
        (self.project / "ZELINE.md").write_text("x", encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=OSError("denied")):
            path, text = self.rules.read_rules(self.project)
        self.assertIsNone(path)
        self.assertEqual(text, "")


class AgentIntegrationTests(RulesBase):
    def test_agent_system_prompt_includes_the_project_rules(self):
        (self.project / "ZELINE.md").write_text("Use 2-space indent everywhere.", encoding="utf-8")
        agent_mod = importlib.import_module("zeline.agent")
        agent = agent_mod.Zeline(
            identity="cli:local", tool_profile="workspace", workspace=str(self.project)
        )
        prompt = agent.messages[0]["content"]
        self.assertIn("Use 2-space indent everywhere.", prompt)
        self.assertIn("<project_rules", prompt)

    def test_agent_prompt_has_no_block_without_a_rules_file(self):
        agent_mod = importlib.import_module("zeline.agent")
        agent = agent_mod.Zeline(
            identity="cli:local", tool_profile="workspace", workspace=str(self.project)
        )
        self.assertNotIn("<project_rules", agent.messages[0]["content"])

    def test_reload_provider_keeps_the_project_rules(self):
        """/model switch rebuilds the prompt; rules must survive it."""
        (self.project / "ZELINE.md").write_text("Marker rule ABC123.", encoding="utf-8")
        agent_mod = importlib.import_module("zeline.agent")
        agent = agent_mod.Zeline(
            identity="cli:local", tool_profile="workspace", workspace=str(self.project)
        )
        agent.reload_provider()
        self.assertIn("Marker rule ABC123.", agent.messages[0]["content"])


class TemplateTests(RulesBase):
    def test_python_project_gets_pytest_command(self):
        (self.project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        text = self.rules.render_template(self.project)
        self.assertIn("pytest", text)

    def test_node_project_gets_npm_commands(self):
        (self.project / "package.json").write_text("{}", encoding="utf-8")
        text = self.rules.render_template(self.project)
        self.assertIn("npm test", text)

    def test_rust_and_go_are_detected(self):
        (self.project / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        (self.project / "go.mod").write_text("module x\n", encoding="utf-8")
        text = self.rules.render_template(self.project)
        self.assertIn("cargo test", text)
        self.assertIn("go test", text)

    def test_unknown_project_gets_an_explicit_todo_not_a_fake_command(self):
        text = self.rules.render_template(self.project)
        self.assertIn("TODO", text)

    def test_layout_skips_noise_directories(self):
        for name in ("node_modules", "__pycache__", ".venv", "dist", "src"):
            (self.project / name).mkdir()
        text = self.rules.render_template(self.project)
        self.assertIn("`src/`", text)
        for noisy in ("node_modules", "__pycache__", "dist"):
            self.assertNotIn(f"`{noisy}/`", text)

    def test_summary_comes_from_readme_prose_not_a_badge_line(self):
        (self.project / "README.md").write_text(
            "# Title\n\n[![badge](https://img.shields.io/x)](https://y)\n\nA real description sentence.\n",
            encoding="utf-8",
        )
        text = self.rules.render_template(self.project)
        self.assertIn("A real description sentence.", text)
        self.assertNotIn("img.shields.io", text)

    def test_template_output_is_discoverable_by_the_reader(self):
        """Round trip: what init writes must be what read_rules picks up."""
        (self.project / "ZELINE.md").write_text(
            self.rules.render_template(self.project), encoding="utf-8"
        )
        path, text = self.rules.read_rules(self.project)
        self.assertEqual(path.name, "ZELINE.md")
        self.assertIn("Project conventions for AI agents", text)


class InitCommandTests(RulesBase):
    def _cli(self):
        return importlib.import_module("zeline.cli")

    def test_init_creates_the_file(self):
        cli = self._cli()
        self.assertEqual(cli.cmd_init(str(self.project)), 0)
        self.assertTrue((self.project / "ZELINE.md").is_file())

    def test_init_refuses_to_clobber_existing_rules(self):
        cli = self._cli()
        existing = self.project / "AGENTS.md"
        existing.write_text("hand written", encoding="utf-8")
        self.assertEqual(cli.cmd_init(str(self.project)), 0)
        # Neither overwritten nor shadowed by a new file.
        self.assertEqual(existing.read_text(encoding="utf-8"), "hand written")
        self.assertFalse((self.project / "ZELINE.md").exists())

    def test_force_overwrites(self):
        cli = self._cli()
        target = self.project / "ZELINE.md"
        target.write_text("old", encoding="utf-8")
        self.assertEqual(cli.cmd_init(str(self.project), force=True), 0)
        self.assertIn("Project conventions", target.read_text(encoding="utf-8"))

    def test_init_rejects_a_non_directory(self):
        cli = self._cli()
        self.assertEqual(cli.cmd_init(str(self.project / "missing")), 2)

    def test_rules_command_reports_absence_and_presence(self):
        cli = self._cli()
        self.assertEqual(cli.cmd_rules(str(self.project)), 0)
        (self.project / "ZELINE.md").write_text("marker", encoding="utf-8")
        self.assertEqual(cli.cmd_rules(str(self.project)), 0)

    def test_cli_exposes_init_and_rules(self):
        cli = self._cli()
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["init"]).command, "init")
        self.assertTrue(parser.parse_args(["init", "--force"]).force)
        self.assertEqual(parser.parse_args(["rules"]).command, "rules")


if __name__ == "__main__":
    unittest.main()
