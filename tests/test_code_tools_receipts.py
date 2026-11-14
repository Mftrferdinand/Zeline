"""Snippet helper receipts must not label a failed native command successful."""
import json
import unittest
from pathlib import Path
import test_agent as fixtures

class CodeReceiptTests(unittest.TestCase):
    setUp=fixtures.AgentLoopTests.setUp
    tearDown=fixtures.AgentLoopTests.tearDown
    def test_nonzero_terminal_exit_is_a_failed_helper_receipt(self):
        executor=self.agent_module.ToolExecutor('cli:local','full',workspace=Path(self.temp.name)/'workspace')
        result=executor.run('execute_code',{'code':'from zeline_tools import terminal\nimport json\nprint(json.dumps(terminal("exit 7")))'})
        self.assertTrue(result.startswith('exit=0\n'),result)
        receipt=json.loads(result.split('\n',1)[1])
        self.assertFalse(receipt['success'],receipt)
        self.assertIn('exit=7',receipt['output'])
