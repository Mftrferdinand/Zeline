"""Regression for repeated slow discovery hidden by successful helper calls."""
import json
import unittest
from unittest import mock
import test_agent as fixtures


class SlowSearchLoopTests(unittest.TestCase):
    setUp = fixtures.AgentLoopTests.setUp
    tearDown = fixtures.AgentLoopTests.tearDown

    def test_timed_out_search_cannot_repeat_after_helpers(self):
        agent = self.agent_module.Zeline(tool_profile='full')
        self.agent_module.config.MAX_TOOL_ROUNDS = 12
        requested = 0
        dispatched = []

        def llm(**kwargs):
            nonlocal requested
            if kwargs.get('use_tools') is False or requested >= 8:
                return {'role': 'assistant', 'content': 'Search blocked; existing evidence preserved.'}
            requested += 1
            name = 'run_shell' if requested % 2 else 'read_file'
            args = {'command': 'timeout 45 grep -rl fixture .config'} if name == 'run_shell' else {'path': 'fixture.txt'}
            return {'role':'assistant','content':'','tool_calls':[{'id':f'call-{requested}', 'type':'function',
                    'function':{'name':name,'arguments':json.dumps(args)}}]}

        def run(name, args):
            dispatched.append(name)
            return 'exit=124\n' if name == 'run_shell' else 'fixture detail'

        with mock.patch.object(agent, '_call_llm', side_effect=llm), mock.patch.object(agent.executor, 'run', side_effect=run):
            answer = agent.send('Find the fixture using a bounded search.')
        self.assertEqual(dispatched.count('run_shell'), 3)
        self.assertIn('preserved', answer)
        self.assertFalse(agent.last_turn_complete)
        self.assertTrue(any('not executed' in str(m.get('content','')) for m in agent.messages if m.get('role') == 'tool'))
