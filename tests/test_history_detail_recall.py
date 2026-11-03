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
