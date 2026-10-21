"""Keep the optional Sem recipe aligned with Zeline's configuration parser."""
import json
from pathlib import Path
import re
import unittest

from zeline.mcp import MCPRegistry


class SemMCPDocsTests(unittest.TestCase):
    def test_documented_config_parses_without_connecting(self):
        guide = (Path(__file__).resolve().parents[1] / "docs/extending.md").read_text()
        section = guide.split("### Local code lookup with Sem", 1)[1].split("\n###", 1)[0]
        match = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
        self.assertIsNotNone(match)
        config = json.loads(match.group(1))
        server = config["mcp"]["servers"]["sem"]
        self.assertEqual(server["transport"], "stdio")
        self.assertEqual(server["command"], "sem mcp")
        self.assertEqual(server["env"]["SEM_REPO"], "/absolute/path/to/checkout")
        registry = MCPRegistry.from_config(config)
        try:
            self.assertEqual(set(registry.servers), {"sem"})
        finally:
            registry.close()
