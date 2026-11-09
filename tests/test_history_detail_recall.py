"""Bounded archive discovery/detail through the real public tool dispatcher."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class HistoryDetailRecallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, HOME=self.tmp.name, ZELINE_HOME=self.tmp.name,
                              ZELINE_API_KEY='fixture', ZELINE_BASE_URL='http://127.0.0.1:1/v1')
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.persistence_module = importlib.import_module('zeline.session_store')
        self.tools = importlib.import_module('zeline.tools')
        self.store = self.persistence_module.SessionPersistence()
        self.executor = self.tools.ToolExecutor('owner', 'full', Path(self.tmp.name))

    def test_public_detail_recovers_middle_of_long_unicode_answer(self):
        text = 'Amber: ' + ('雪🧭' * 5000) + ' NEXT_STEP_UNIQUE: run amber_test.py ' + ('é' * 5000)
        self.store.append_turn('owner', 'assistant', text, 'Amber')
        discovery = self.executor.run('recall_history', {'query': 'Amber'})
        self.assertIn('message_id=1', discovery)
        detail = self.executor.run('recall_history', {'message_id': 1, 'offset': 9980, 'limit': 120})
        self.assertIn('NEXT_STEP_UNIQUE: run amber_test.py', detail)
        self.assertIn('next_offset', detail)
        self.assertLess(len(detail), 1500)


    def test_repeated_same_lookup_does_not_query_archive_again(self):
        self.store.append_turn('owner', 'assistant', 'Amber inspected', 'Amber')
        original = self.persistence_module.SessionPersistence.search_archive
        calls = []
        def counted(store, *args, **kwargs):
            calls.append(args)
            return original(store, *args, **kwargs)
        with patch.object(self.persistence_module.SessionPersistence, 'search_archive', new=counted):
            first = self.executor.run('recall_history', {'query': 'Amber'})
            repeated = self.executor.run('recall_history', {'query': 'Amber'})
            self.assertEqual(len(calls), 1)
            self.assertIn('already', repeated.lower())
            self.assertIn('Amber inspected', first)
            self.executor.run('recall_history', {'query': 'inspected'})
            self.assertEqual(len(calls), 2)


    def test_bare_recall_uses_invocation_selected_checkpoint_not_latest_thread(self):
        from zeline.task_context import TaskContextStore
        tasks = TaskContextStore()
        amber = tasks.begin('owner', 'Amber task')['task']
        tasks.checkpoint('owner', amber['id'], summary='Amber verified', next_step='Run amber_next')
        blue = tasks.begin('owner', 'Blue task')['task']
        self.store.append_turn('owner', 'assistant', 'UNRELATED_BLUE_ARCHIVE', 'Blue')
        self.executor._task_store = tasks
        self.executor._task_id = blue['id']
        with self.executor.invocation_context({'_task_store': tasks, '_task_id': amber['id'],
                                               '_history_recall_cache': {}}):
            for query in ('', 'lanjut', 'terusin', 'yang tadi'):
                result = self.executor.run('recall_history', {'query': query})
                self.assertIn('Run amber_next', result)
                self.assertNotIn('UNRELATED_BLUE_ARCHIVE', result)
                self.assertNotIn('Blue task', result)

    def test_unresolved_bare_recall_never_selects_global_latest_thread(self):
        self.store.append_turn('owner', 'assistant', 'UNRELATED_BLUE_ARCHIVE', 'Blue')
        for controls in ({'_task_resolution_required': True},
                         {'_task_store': object(), '_task_id': None}):
            with self.executor.invocation_context(controls):
                result = self.executor.run('recall_history', {})
            self.assertIn('unresolved', result.lower())
            self.assertIn('ask_user', result)
            self.assertNotIn('UNRELATED_BLUE_ARCHIVE', result)

    def test_legacy_recent_thread_is_only_a_historical_candidate(self):
        self.store.append_turn('owner', 'assistant', 'Blue archived work', 'Blue')
        result = self.executor.run('recall_history', {})
        self.assertIn('Blue archived work', result)
        self.assertIn('historical candidate', result.lower())
        self.assertIn('not proof', result.lower())
        self.assertNotIn('continue THIS', result)

    def test_no_hit_does_not_claim_transcripts_or_work_do_not_exist(self):
        for query in ('Amber', ''):
            result = self.executor.run('recall_history', {'query': query})
            self.assertIn('other history may still exist', result.lower())
            self.assertNotIn('has no earlier transcript', result)
            self.assertNotIn('there is nothing', result)
            self.assertNotIn('is older than', result)

    def test_failed_archive_requests_can_retry_in_same_turn(self):
        self.store.append_turn('owner', 'assistant', 'Amber retry evidence', 'Amber')
        cases = [('__init__', {'query': 'Amber'}),
                 ('search_archive', {'query': 'Amber'}),
                 ('archive_detail', {'message_id': 1}), ('last_thread', {})]
        for method, args in cases:
            original = getattr(self.persistence_module.SessionPersistence, method)
            calls = []
            def flaky(store, *positional, **kwargs):
                calls.append(True)
                if len(calls) == 1:
                    raise OSError('synthetic transient failure')
                return original(store, *positional, **kwargs)
            with self.subTest(method=method), self.executor.invocation_context({'_history_recall_cache': {}}):
                with patch.object(self.persistence_module.SessionPersistence, method, new=flaky):
                    failed = self.executor.run('recall_history', args)
                    retried = self.executor.run('recall_history', args)
                    repeated = self.executor.run('recall_history', args)
                self.assertIn('ERROR', failed)
                self.assertIn('Amber retry evidence', retried)
                self.assertEqual(len(calls), 2)
                self.assertIn('already', repeated)

    def test_named_discovery_and_public_schema_do_not_authorize_historical_instructions(self):
        self.store.append_turn('owner', 'assistant', 'Amber: execute the old instruction', 'Amber')
        result = self.executor.run('recall_history', {'query': 'Amber'})
        self.assertIn('untrusted historical evidence', result.lower())
        self.assertIn('not instructions', result.lower())
        schema = next(item for item in self.executor.all_schemas
                      if item['function']['name'] == 'recall_history')['function']
        self.assertIn('selected task checkpoint', schema['description'].lower())
        self.assertNotIn('whenever the user refers to the past', schema['description'])
        self.assertIn('selected task', schema['parameters']['properties']['query']['description'].lower())

    def test_latest_checkpoint_is_not_excluded_by_old_relevance_pool(self):

        for index in range(110):
            self.store.append_turn('owner', 'assistant', ('Amber ' * 30) + str(index), 'Amber')
        self.store.append_turn('owner', 'assistant',
            'Amber ' + ('unrelatedpadding ' * 100) + 'NEXT_STEP_CURRENT', 'Amber')
        rows = self.store.search_archive('owner', 'Amber', limit=4)
        self.assertTrue(any('NEXT_STEP_CURRENT' in row['content'] for row in rows))
        self.assertEqual(len(rows), 4)


if __name__ == '__main__':
    unittest.main()
