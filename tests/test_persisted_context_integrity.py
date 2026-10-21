"""Persisted windows retain the task anchor and valid tool/result groups."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


class PersistedContextIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {'ZELINE_HOME': self.tmp.name,
            'ZELINE_API_KEY': 'fixture-only', 'ZELINE_MODEL': 'fixture',
            'ZELINE_BASE_URL': 'http://127.0.0.1:1/v1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.module = importlib.import_module('zeline.session_store')
        self.compaction = importlib.import_module('zeline.compaction')
        self.persistence = self.module.SessionPersistence(Path(self.tmp.name) / 'sessions.db')

    def test_existing_digest_survives_save_and_reopen(self):
        digest = {'role': 'user', 'content': '[compacted-context] retained finding ROOT_EVIDENCE'}
        history = [{'role': 'system', 'content': 'system'}, digest,
                   {'role': 'user', 'content': 'continue'}, {'role': 'assistant', 'content': 'next action'}]
        self.persistence.save('fixture', history)
        restored, _ = self.module.SessionPersistence(self.persistence.path).load('fixture')
        self.assertTrue(any(self.compaction.is_digest(m) for m in restored))
        self.assertIn('ROOT_EVIDENCE', str(restored))

    def test_single_long_turn_keeps_original_user_and_recent_frontier(self):
        history = [{'role': 'system', 'content': 'system'},
                   {'role': 'user', 'content': 'Finish the ORIGINAL_TASK without repeating completed work'}]
        history += [{'role': 'assistant', 'content': f'Finding {i} already verified'} for i in range(65)]
        history.append({'role': 'assistant', 'content': 'NEXT_STEP: implement verified fix'})
        self.persistence.save_checkpoint('fixture', history)
        restored, _ = self.module.SessionPersistence(self.persistence.path).load('fixture')
        self.assertTrue(any(m.get('role') == 'user' and 'ORIGINAL_TASK' in m.get('content', '') for m in restored))
        self.assertIn('NEXT_STEP: implement verified fix', str(restored))
        stored = [m for m in restored if not str(m.get('content', '')).startswith('[Zeline recovery]')]
        self.assertLessEqual(len(stored), self.module.MAX_STORED_MESSAGES + 1)

    def test_oversize_tool_result_keeps_call_pair_goal_and_frontier(self):
        history = [
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'Investigate CURRENT_TASK'},
            {'role': 'assistant', 'content': 'Inspect evidence', 'tool_calls': [
                {'id': 'read-1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': json.dumps({'path': 'source.py'})}}]},
            {'role': 'tool', 'tool_call_id': 'read-1', 'name': 'read_file', 'content': 'CONFIRMED_FINDING\n' + 'x' * 10000 + '\nVERIFIED_TAIL'},
            {'role': 'assistant', 'content': 'NEXT_STEP: patch the confirmed cause'},
        ]
        with mock.patch.object(self.module, 'MAX_STORED_CHARS', 8000):
            self.persistence.save_checkpoint('fixture', history)
        restored, _ = self.module.SessionPersistence(self.persistence.path).load('fixture')
        self.assertIn('CURRENT_TASK', str(restored))
        self.assertIn('CONFIRMED_FINDING', str(restored))
        self.assertIn('NEXT_STEP: patch the confirmed cause', str(restored))
        stored = [m for m in restored[1:] if not str(m.get('content', '')).startswith('[Zeline recovery]')]
        self.assertLessEqual(sum(len(str(m.get('content', ''))) for m in stored), 8000)
        calls = {c['id'] for m in restored for c in m.get('tool_calls', [])}
        results = {m['tool_call_id'] for m in restored if m.get('role') == 'tool'}
        self.assertEqual(calls, results)
        self.assertEqual(calls, {'read-1'})

    def test_real_stop_compaction_reload_delivers_findings_to_provider_request(self):
        """Real session/tool path; provider response is a fixed offline fixture."""
        sessions = importlib.import_module('zeline.sessions')
        agent_module = importlib.import_module('zeline.agent')
        agent_module.config.STREAM_RESPONSES = False
        store = sessions.SessionStore(persistence=self.persistence)
        identity = 'fixture:stop-reload'
        session = store.get_or_create(identity, 'full', workspace=self.tmp.name)
        (Path(self.tmp.name) / 'evidence.txt').write_text('ROOT_FINDING: decoder drops Unicode')
        call = {'role': 'assistant', 'content': 'NEXT_STEP: implement the verified fix once.', 'tool_calls': [
            {'id': 'fixture-read', 'type': 'function', 'function': {
                'name': 'read_file', 'arguments': '{"path":"evidence.txt"}'}}]}
        with mock.patch.object(session.agent, '_call_llm', return_value=call):
            reply = store.send(identity, 'ORIGINAL_GOAL: repair decoder without public API changes', 'full',
                on_tool_result=lambda *_args: store.stop(identity))
        self.assertEqual(reply, 'Stopped.')
        for _ in range(40):
            session.agent.messages.extend([
                {'role': 'user', 'content': 'continue'},
                {'role': 'assistant', 'content': 'Previously checked. ' + '.' * 1700}])
        session.agent._trim_history()
        self.persistence.save(identity, session.agent.export_history())
        rebuilt = sessions.SessionStore(persistence=self.module.SessionPersistence(self.persistence.path))
        rebuilt.get_or_create(identity, 'full', workspace=self.tmp.name)
        response = mock.Mock(status_code=200, headers={})
        response.json.return_value = {'choices': [{'message': {'role': 'assistant', 'content': 'Fixture final response'}}]}
        response.text = json.dumps(response.json.return_value)
        with mock.patch.object(agent_module.requests, 'post', return_value=response) as post:
            rebuilt.send(identity, 'lanjut', 'full')
        body = json.dumps(post.call_args.kwargs['json']['messages'])
        for evidence in ('ORIGINAL_GOAL', 'ROOT_FINDING', 'NEXT_STEP', 'evidence.txt'):
            self.assertIn(evidence, body)
