"""The static engine is a default native tool, not an MCP registration."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_public_core import fresh_zeline


class ReverseToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'ZELINE_HOME': str(self.root / 'state')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config, self.memory, self.tools = fresh_zeline(self.root / 'state')
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        (self.workspace / 'target.bin').write_bytes(b'\x00STATIC_FIXTURE\x00')

    def test_default_tool_is_profile_gated_and_needs_no_mcp(self):
        definitions = {d.name: d for d in self.tools.TOOL_DEFS}
        self.assertIn('reverse_engineer', definitions)
        self.assertEqual(definitions['reverse_engineer'].profiles, frozenset({'workspace', 'full'}))
        executor = self.tools.ToolExecutor('cli:static-test', 'workspace', self.workspace)
        self.assertEqual(self.config.MCP_SERVERS, {})
        result = json.loads(executor.run('reverse_engineer', {'action': 'inspect', 'path': 'target.bin'}))
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['evidence_id'].startswith('zev_'))
        public = self.tools.ToolExecutor('telegram:fixture', 'safe', self.workspace)
        self.assertTrue(public.run('reverse_engineer', {'action': 'inspect', 'path': 'target.bin'}).startswith('ERROR'))

    def test_outside_path_is_denied_by_real_dispatch(self):
        (self.root / 'private.bin').write_bytes(b'PRIVATE_FIXTURE')
        executor = self.tools.ToolExecutor('cli:static-test', 'workspace', self.workspace)
        result = executor.run('reverse_engineer', {'action': 'inspect', 'path': '../private.bin'})
        self.assertTrue(result.startswith('ERROR'), result)
        self.assertNotIn('PRIVATE_FIXTURE', result)

    def test_owner_can_disable_native_engine(self):
        with patch.object(self.config, 'DISABLED_TOOLS', ['reverse_engineer']):
            executor = self.tools.ToolExecutor('cli:static-test', 'workspace', self.workspace)
            self.assertTrue(executor.run('reverse_engineer', {'action': 'inspect', 'path': 'target.bin'}).startswith('ERROR'))

    def test_progress_and_compaction_describe_inspection_not_file_creation(self):
        from zeline.gateways.telegram import _tool_progress_text
        title = _tool_progress_text('reverse_engineer', {'action': 'strings', 'path': '<target>.bin'})
        self.assertIn('strings', title.lower())
        self.assertNotIn('<target>', title)
        from zeline import compaction
        self.assertIn('reverse_engineer', compaction._READ_TARGET_TOOLS)
        self.assertNotIn('reverse_engineer', compaction._ARTIFACT_TOOLS)
