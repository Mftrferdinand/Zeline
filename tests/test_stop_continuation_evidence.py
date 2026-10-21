"""Continuation must use current tool evidence before consulting old archives."""
import importlib
import json
import unittest
from unittest import mock
from tests import test_agent as support


class StopContinuationEvidenceTests(unittest.TestCase):
    setUp = support.AgentLoopTests.setUp
    tearDown = support.AgentLoopTests.tearDown

    def test_continuation_prompt_prefers_current_evidence_over_archive_recall(self):
        prompt = self.agent_module.config.SYSTEM_PROMPT_TEMPLATE
        self.assertIn('Resume from the last unfinished step', prompt)
        self.assertIn('Do not re-read completed files or repeat searches', prompt)
        self.assertNotIn('call recall_history FIRST', prompt)

    def test_real_stop_and_continue_preserve_result_and_plan_across_reload(self):
        sessions = importlib.import_module('zeline.sessions')
        store = sessions.SessionStore()
        identity = 'telegram:stop-evidence'
        session = store.get_or_create(identity, 'safe')
        agent = session.agent
        plan = 'Evidence identified; next summarize the verified finding.'
        call = {'content': plan, 'tool_calls': [{'id': 'known-read', 'type': 'function',
                'function': {'name': 'list_memory', 'arguments': '{}'}}]}
        def completed(*args):
            store.stop(identity)
        with mock.patch.object(agent, '_call_llm', return_value=call), \
             mock.patch.object(agent.executor, 'run', return_value='VERIFIED_STOP_FIXTURE_EVIDENCE') as execute:
            self.assertEqual(store.send(identity, 'Analyze the fixture then summarize.', 'safe',
                                        on_tool_result=completed), 'Stopped.')
            self.assertEqual(execute.call_count, 1)
        rebuilt = sessions.SessionStore()
        resumed = rebuilt.get_or_create(identity, 'safe').agent
        observed = []
        def provider(**kwargs):
            observed.extend(resumed.export_history())
            return {'content': 'Summary from existing verified evidence.'}
        with mock.patch.object(resumed, '_call_llm', side_effect=provider), \
             mock.patch.object(resumed.executor, 'run') as reread:
            result = rebuilt.send(identity, 'lanjutin', 'safe')
        self.assertEqual(result, 'Summary from existing verified evidence.')
        self.assertIn('VERIFIED_STOP_FIXTURE_EVIDENCE', json.dumps(observed))
        self.assertIn(plan, json.dumps(observed))
        reread.assert_not_called()
