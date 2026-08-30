"""Contract tests for lazy tool schemas and `tool_search`.

The feature trades one round trip for tokens, so the tests pin both halves: that
it actually saves tokens, and that it never costs the model a capability.

Four invariants:

- **Nothing becomes unreachable.** Every tool name stays visible in the
  catalogue, and calling a hidden tool *directly* works without a lookup first.
- **Revelation is sticky.** Once shown, a schema stays shown for the life of the
  executor, so an earlier plan cannot become silently unexecutable.
- **Off by default, and inert when off.** With the flag off the schema list is
  byte-identical to before the feature existed.
- **It only engages when it pays.** Below the tool-count floor, nothing is
  hidden, because the extra round trip would cost more than the tokens saved.
"""
from __future__ import annotations

import importlib
import json
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
    index = importlib.import_module("zeline.tool_index")
    tools = importlib.import_module("zeline.tools")
    return cfg, index, tools


def schema(name: str, description: str = "", **params) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or f"Does {name}.",
            "parameters": {"type": "object", "properties": params},
        },
    }


class IndexBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.index, self.tools = fresh(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()

    def switch(self, on: bool):
        saved = self.config.config_copy()
        saved["tools"]["tool_search"] = on
        self.config.save_config(saved)

    def many(self, count: int = 20) -> list[dict]:
        """Core tools plus enough extras to clear the floor."""
        built = [schema(name) for name in sorted(self.index.CORE_TOOLS)]
        built += [schema(f"extra_{i}", f"Extra tool number {i}.") for i in range(count)]
        return built


class FlagTests(IndexBase):
    def test_off_by_default(self):
        self.assertFalse(self.index.enabled())

    def test_when_off_every_schema_is_sent_unchanged(self):
        all_schemas = self.many()
        idx = self.index.LazySchemaIndex(all_schemas)
        self.assertFalse(idx.applicable)
        self.assertEqual(idx.visible(), all_schemas)

    def test_when_on_but_too_few_tools_nothing_is_hidden(self):
        """Below the floor the round trip costs more than the tokens saved."""
        self.switch(True)
        few = [schema(f"t{i}") for i in range(self.index.MIN_TOOLS_TO_BOTHER - 1)]
        idx = self.index.LazySchemaIndex(few)
        self.assertFalse(idx.applicable)
        self.assertEqual(idx.visible(), few)

    def test_when_on_with_enough_tools_it_engages(self):
        self.switch(True)
        idx = self.index.LazySchemaIndex(self.many())
        self.assertTrue(idx.applicable)
        self.assertLess(len(idx.visible()), len(idx.all))


class VisibilityTests(IndexBase):
    def setUp(self):
        super().setUp()
        self.switch(True)
        self.idx = self.index.LazySchemaIndex(self.many())

    def names(self) -> list[str]:
        return [s["function"]["name"] for s in self.idx.visible()]

    def test_core_tools_are_always_visible(self):
        visible = set(self.names())
        for name in self.index.CORE_TOOLS:
            self.assertIn(name, visible, name)

    def test_tool_search_is_offered_when_something_is_hidden(self):
        self.assertIn(self.index.TOOL_NAME, self.names())

    def test_every_hidden_tool_is_named_in_the_catalogue(self):
        """The model must never be blind to a capability, only to its parameters."""
        search = next(
            s for s in self.idx.visible()
            if s["function"]["name"] == self.index.TOOL_NAME
        )
        catalog = search["function"]["description"]
        hidden = [
            s["function"]["name"] for s in self.idx.all
            if s["function"]["name"] not in self.index.CORE_TOOLS
        ]
        for name in hidden:
            self.assertIn(name, catalog, name)

    def test_hiding_actually_saves_tokens(self):
        full = len(json.dumps(self.idx.all))
        lazy = len(json.dumps(self.idx.visible()))
        self.assertLess(lazy, full)

    def test_catalogue_is_cheaper_than_the_schemas_it_replaces(self):
        """The whole premise: names are cheap, schemas are expensive."""
        hidden = [
            s for s in self.idx.all
            if s["function"]["name"] not in self.index.CORE_TOOLS
        ]
        catalog_cost = len(json.dumps(self.index.search_schema(hidden)))
        schema_cost = len(json.dumps(hidden))
        self.assertLess(catalog_cost, schema_cost)

    def test_no_tool_search_entry_when_nothing_is_hidden(self):
        for name in [s["function"]["name"] for s in self.idx.all]:
            self.idx.reveal(name)
        self.assertNotIn(self.index.TOOL_NAME, self.names())

    def test_revealing_is_sticky(self):
        self.assertNotIn("extra_3", self.names())
        self.idx.reveal("extra_3")
        self.assertIn("extra_3", self.names())
        self.assertIn("extra_3", self.names())  # still there on a later round


class SearchTests(IndexBase):
    def setUp(self):
        super().setUp()
        self.switch(True)
        self.idx = self.index.LazySchemaIndex([
            *[schema(name) for name in sorted(self.index.CORE_TOOLS)],
            schema("http_request", "Call a REST API or webhook with any method."),
            schema("web_search", "Search the public web for information."),
            schema("generate_image", "Create an image from a text prompt."),
            *[schema(f"filler_{i}") for i in range(10)],
        ])

    def test_exact_name_match_wins(self):
        result = self.idx.search("http_request")
        self.assertIn("http_request", result)
        self.assertIn("http_request", [s["function"]["name"] for s in self.idx.visible()])

    def test_capability_words_find_the_tool(self):
        self.assertIn("web_search", self.idx.search("search the web"))

    def test_a_name_match_suppresses_weaker_description_matches(self):
        """Every stray match costs the tokens this feature exists to save."""
        result = self.idx.search("http_request")
        self.assertIn("http_request", result)
        self.assertNotIn('"name": "web_search"', result)

    def test_a_match_reveals_the_full_parameters(self):
        result = self.idx.search("generate_image")
        self.assertIn("parameters", result)

    def test_no_match_lists_what_is_available(self):
        result = self.idx.search("teleportation device")
        self.assertIn("No tool matched", result)
        self.assertIn("http_request", result)

    def test_empty_query_does_not_match_everything(self):
        self.assertIn("No tool matched", self.idx.search(""))

    def test_search_does_not_return_core_tools(self):
        """They are already loaded; re-sending them would waste the round trip."""
        result = self.idx.search("read_file")
        self.assertIn("No tool matched", result)

    def test_results_are_capped(self):
        before = len(self.idx.revealed)
        self.idx.search("tool")
        self.assertLessEqual(len(self.idx.revealed) - before, 4)


class ExecutorTests(IndexBase):
    def executor(self, profile: str = "full"):
        return self.tools.ToolExecutor(
            identity="cli:local", profile=profile, workspace=str(self.home)
        )

    def test_off_means_identical_behaviour_to_before_the_feature(self):
        executor = self.executor()
        self.assertEqual(executor.schemas, executor.all_schemas)

    def test_on_reduces_what_is_sent(self):
        full = len(json.dumps(self.executor().schemas))
        self.switch(True)
        lazy = len(json.dumps(self.executor().schemas))
        self.assertLess(lazy, full)

    def test_search_then_call_works_end_to_end(self):
        self.switch(True)
        executor = self.executor()
        names = [s["function"]["name"] for s in executor.schemas]
        self.assertNotIn("system_env", names)
        result = executor.run("tool_search", {"query": "system_env"})
        self.assertIn("system_env", result)
        self.assertIn("system_env", [s["function"]["name"] for s in executor.schemas])
        self.assertFalse(executor.run("system_env", {}).startswith("ERROR"))

    def test_calling_a_hidden_tool_directly_still_works(self):
        """There must be no dead end where a named tool cannot be reached."""
        self.switch(True)
        executor = self.executor()
        self.assertNotIn("system_env", [s["function"]["name"] for s in executor.schemas])
        result = executor.run("system_env", {})
        self.assertFalse(result.startswith("ERROR"), result)
        self.assertIn("system_env", [s["function"]["name"] for s in executor.schemas])

    def test_tool_search_when_disabled_says_so_instead_of_failing_silently(self):
        executor = self.executor()
        self.assertTrue(executor.schemas)  # also builds the index
        result = executor.run("tool_search", {"query": "anything"})
        self.assertIn("not needed", result)
        self.assertIn("directly", result)

    def test_a_disabled_tool_is_not_reachable_through_search(self):
        """Lazy loading must not become a way around the owner's disable list."""
        saved = self.config.config_copy()
        saved["tools"]["tool_search"] = True
        saved["tools"]["disabled"] = ["system_env"]
        self.config.save_config(saved)
        executor = self.executor()
        result = executor.run("tool_search", {"query": "system_env"})
        self.assertIn("No tool matched", result)
        self.assertIn("disabled", executor.run("system_env", {}))

    def test_a_safe_profile_tool_cannot_be_revealed_beyond_its_profile(self):
        """Hiding is presentation only; the profile boundary still decides."""
        self.switch(True)
        executor = self.executor("safe")
        result = executor.run("tool_search", {"query": "run_shell"})
        # A loose query may still surface some other tool, which is harmless --
        # what matters is that run_shell itself is neither offered nor callable.
        self.assertNotIn('"name": "run_shell"', result)
        self.assertIn("not allowed", executor.run("run_shell", {"command": "ls"}))

    def test_newly_added_custom_tools_appear_in_the_catalogue(self):
        """The index is rebuilt each round, so a late-loading tool is not lost."""
        self.switch(True)
        custom_dir = importlib.import_module("zeline.custom_tools").ensure_dir()
        (custom_dir / "late.py").write_text(
            "def arrived() -> str:\n    return 'here'\n", encoding="utf-8"
        )
        executor = self.executor()
        search = next(
            s for s in executor.schemas
            if s["function"]["name"] == self.index.TOOL_NAME
        )
        self.assertIn("custom_arrived", search["function"]["description"])
        self.assertFalse(executor.run("custom_arrived", {}).startswith("ERROR"))

    def test_summaries_are_short_enough_to_be_worth_it(self):
        self.switch(True)
        executor = self.executor()
        search = next(
            s for s in executor.schemas
            if s["function"]["name"] == self.index.TOOL_NAME
        )
        for line in search["function"]["description"].splitlines():
            if line.startswith("- "):
                self.assertLess(len(line), 120, line)


class SummaryTests(IndexBase):
    def test_first_sentence_only(self):
        text = self.index._summarize("Do the thing. Then a lot more detail follows here.")
        self.assertEqual(text, "Do the thing")

    def test_long_text_is_truncated(self):
        text = self.index._summarize("word " * 100)
        self.assertLessEqual(len(text), 70)

    def test_empty_description_is_safe(self):
        self.assertEqual(self.index._summarize(""), "")


if __name__ == "__main__":
    unittest.main()
