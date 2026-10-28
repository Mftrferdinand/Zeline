"""Keep the optional REA recipe compatible with the real MCP config parser."""
import json
from pathlib import Path
import re
import unittest

from zeline.mcp import MCPRegistry, split_command

ROOT = Path(__file__).resolve().parents[1]


class REAMCPDocsTests(unittest.TestCase):
    def test_bundled_workflow_is_available_without_an_enabled_server(self):
        skill = ROOT / "zeline/skills/rea-reverse-engineering.md"
        self.assertTrue(skill.is_file(), "Ship the REA investigation workflow")
        text = skill.read_text(encoding="utf-8")
        for requirement in ["tool_search", "mcp__rea__", "static", "evidence", "limitations"]:
            self.assertIn(requirement, text)
        self.assertNotIn("npx rea-agents setup", text)

    def test_documented_config_parses_without_starting_a_process(self):
        guide = (ROOT / "docs/extending.md").read_text(encoding="utf-8")
        heading = "### Reverse engineering with REA"
        self.assertIn(heading, guide)
        section = guide.split(heading, 1)[1].split("\n###", 1)[0]
        example = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
        self.assertIsNotNone(example)
        cfg = json.loads(example.group(1))
        registry = MCPRegistry.from_config(cfg)
        try:
            self.assertEqual(set(registry.servers), {"rea"})
            server = registry.servers["rea"]
            self.assertEqual(server.transport, "stdio")
            self.assertEqual(split_command(server.command),
                             ["npx", "-y", "rea-agents@6.7.0", "mcp"])
            self.assertEqual(server.env["REA_MCP_INPUT_SCHEMA_PROFILE"], "compact")
            self.assertIsNone(server._process)
        finally:
            registry.close()


if __name__ == "__main__":
    unittest.main()
