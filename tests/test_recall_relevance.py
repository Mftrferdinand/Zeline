"""Real SQLite recall relevance boundaries, isolated from owner state."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class RecallRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, HOME=self.tmp.name, ZELINE_HOME=self.tmp.name,
                         ZELINE_API_KEY='fixture', ZELINE_BASE_URL='http://127.0.0.1:1/v1')
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        module = importlib.import_module('zeline.session_store')
        self.store = module.SessionPersistence(Path(self.tmp.name) / 'sessions.db')

    def test_domain_query_does_not_retrieve_unrelated_email_topics(self):
        self.store.append_turn('owner', 'user', 'Use generator.email for the Amber fixture', 'Amber')
        self.store.append_turn('owner', 'assistant', 'CapCut email file is capcut_mail.txt', 'Blue')
        rows = self.store.search_archive('owner', 'generator.email')
        self.assertEqual([row['title'] for row in rows], ['Amber'])

    def test_explicit_or_can_broaden_search_without_implicit_topic_mixing(self):
        self.store.append_turn('owner', 'user', 'Amber checkpoint', 'Amber')
        self.store.append_turn('owner', 'user', 'Blue checkpoint', 'Blue')
        self.assertEqual({r['title'] for r in self.store.search_archive('owner', 'Amber OR Blue')}, {'Amber', 'Blue'})
        self.assertEqual(self.store.search_archive('owner', 'Amber Blue'), [])


if __name__ == '__main__':
    unittest.main()
