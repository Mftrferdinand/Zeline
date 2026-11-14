"""Native replies and restart continuity must survive provider context assembly."""
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


class ReplyContextContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, HOME=self.tmp.name, ZELINE_HOME=self.tmp.name,
                         ZELINE_API_KEY='fixture', ZELINE_MODEL='fixture',
                         ZELINE_BASE_URL='http://127.0.0.1:1/v1')
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.module = importlib.import_module('zeline.agent')
        self.module.config.STREAM_RESPONSES = False
        self.module.config.FALLBACK_MODELS = ()
        self.module.config.FALLBACK_MODEL = ''

    def agent(self):
        return self.module.Zeline(identity='telegram:7', workspace=self.tmp.name, tool_profile='safe')

    def send_payload(self, agent, text, reference=None):
        response = Mock(ok=True, status_code=200, encoding='utf-8')
        response.text = json.dumps({'choices': [{'message': {'content': 'fixture answer'}}]})
        with patch.object(self.module.requests, 'post', return_value=response) as post:
            agent.send(text, task_reference=reference)
        self.assertEqual(post.call_count, 1, 'Reference should resolve without a history tool loop')
        return post.call_args.kwargs['json']['messages']

    def test_known_reply_text_reaches_model_and_overrides_unrelated_focus(self):
        agent = self.agent()
        store = agent._task_store
        task_a = store.begin(agent.identity, 'Inspect Amber source', message_id=101)['task']
        store.begin(agent.identity, 'Inspect unrelated Blue source', message_id=201)
        payload = self.send_payload(agent, 'lanjut', {
            'message_id': 301, 'reply_to_message_id': 101,
            'quoted_text': 'AMBER_NATIVE_REPLY_EVIDENCE: continue from reviewed amber.py'})
        self.assertEqual(agent.last_task_id, task_a['id'])
        self.assertIn('AMBER_NATIVE_REPLY_EVIDENCE', json.dumps(payload))
        self.assertNotIn('unrelated Blue', str(payload[-1]['content']))
        self.assertEqual(agent.export_history()[-2]['content'], 'lanjut')

    def test_restart_preserves_restored_focus_history_in_provider_payload(self):
        first = self.agent()
        first._task_store.begin(first.identity, 'Inspect Amber source', message_id=101)
        restored = self.agent()
        restored.load_history([
            {'role': 'user', 'content': 'Inspect Amber source'},
            {'role': 'assistant', 'content': 'AMBER_RESTORED_EVIDENCE: reviewed amber.py; next is its regression test.'},
        ])
        payload = self.send_payload(restored, 'lanjut')
        self.assertTrue(any('AMBER_RESTORED_EVIDENCE' in str(m.get('content', '')) for m in payload),
                        'Restart incorrectly hid the restored focused conversation')

    def test_native_reply_recall_does_not_search_unrelated_archive(self):
        agent = self.agent()
        task = agent._task_store.begin(agent.identity, 'Inspect Amber source', message_id=101)['task']
        observed = []
        def turn(**kwargs):
            captured = agent.executor.capture_context()
            observed.append(agent.executor.run('recall_history', {'query': 'unrelated ancient task'}, _context=captured))
            agent.messages.append({'role': 'assistant', 'content': 'fixture answer'})
            return 'fixture answer'
        persistence = importlib.import_module('zeline.session_store').SessionPersistence
        with patch.object(agent, '_run_turn', side_effect=turn), \
                patch.object(persistence, 'search_archive', side_effect=AssertionError('Broad archive lookup')) as archive:
            agent.send('cek history ini', task_reference={
                'message_id': 301, 'reply_to_message_id': 101,
                'quoted_text': 'AMBER_ANCHORED_EVIDENCE: reviewed amber.py'})
        archive.assert_not_called()
        self.assertIn('AMBER_ANCHORED_EVIDENCE', observed[0])
        self.assertEqual(agent.last_task_id, task['id'])

    def test_unknown_reply_anchor_uses_supplied_text_not_unrelated_old_focus(self):
        agent = self.agent()
        agent._task_store.begin(agent.identity, 'Unrelated Blue task', message_id=201)
        payload = self.send_payload(agent, 'lanjut', {
            'message_id': 301, 'reply_to_message_id': 999,
            'quoted_text': 'AMBER_UNMAPPED_REPLY: reviewed amber.py, tests passed.'})
        self.assertIn('AMBER_UNMAPPED_REPLY', json.dumps(payload))
        self.assertNotIn('Unrelated Blue', str(payload[-1]['content']))


if __name__ == '__main__':
    unittest.main()
