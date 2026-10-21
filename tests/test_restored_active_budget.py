"""Interactive input must not consume the active-work deadline."""
import json
import unittest
from unittest import mock

from tests import test_agent as support


class RestoredHumanWaitBudgetTests(unittest.TestCase):
    setUp = support.AgentLoopTests.setUp
    tearDown = support.AgentLoopTests.tearDown

    def test_late_answer_keeps_next_action_and_explicit_stop_still_works(self):
        self.agent = self.agent_module.Zeline()
        clock = [100.0]
        cancellation = [False]
        observed = []

        messages = iter([
            {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'question', 'type': 'function', 'function': {
                    'name': 'ask_user', 'arguments': json.dumps({'question': 'Proceed?'})}}]},
            {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'next-action', 'type': 'function', 'function': {
                    'name': 'write_file', 'arguments': json.dumps({'path': 'result.txt', 'content': 'ok'})}}]},
            {'role': 'assistant', 'content': 'done'},
        ])

        def run(name, _args):
            observed.append(name)
            if name == 'ask_user':
                clock[0] += 20
                stopped = self.agent.executor._control('_should_stop')
                self.assertFalse(stopped(), 'human idle time exhausted active-work budget')
                cancellation[0] = True
                self.assertTrue(stopped(), 'explicit cancellation must remain effective while waiting')
                cancellation[0] = False
                return 'Proceed'
            clock[0] += 6
            self.assertTrue(self.agent.executor._control('_should_stop')(),
                            'active-work deadline must resume after the answer')
            clock[0] -= 6
            return 'Wrote result.txt'

        with mock.patch.object(self.agent_module.config, 'MAX_TURN_SECONDS', 5), \
             mock.patch.object(self.agent_module.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(self.agent, '_call_llm', side_effect=lambda **_k: next(messages)), \
             mock.patch.object(self.agent.executor, 'run', side_effect=run):
            reply = self.agent.send('Ask before writing the result.', should_stop=lambda: cancellation[0])
        self.assertEqual(reply, 'done')
        self.assertEqual(observed, ['ask_user', 'write_file'])
