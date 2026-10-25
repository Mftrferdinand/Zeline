"""Hard-cap rejection must leave admitted facts and historical revisions intact."""
from contextlib import closing
import importlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


class AdmissionHardLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.memory = importlib.import_module('zeline.memory')
        self.addCleanup(patch.stopall)
        patch.object(self.memory, 'MEMORY_DIR', root / 'memory').start()
        patch.object(self.memory, 'LEGACY_FILE', root / 'legacy.json').start()
        self.store = self.memory.MemoryStore('hard-cap:fixture')
        self.assertEqual(self.store.add_receipt('useralpha')['state'], 'created')
        self.assertEqual(self.store.add_receipt('userbravo')['state'], 'created')
        self.before = self.snapshot()
        self.store.default_source = 'reflection'
        patch.object(self.memory, 'CONSOLIDATE_AT_CHARACTERS', 5).start()

    def snapshot(self):
        records = self.store.records(include_expired=True, include_inactive=True)
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            history = connection.execute('SELECT owner,id,revision,payload FROM memory_revisions ORDER BY owner,id,revision').fetchall()
        return records, history

    def check_rejected(self, *, batch, capacity):
        if capacity == 'characters':
            patch.object(self.memory, 'MAX_CHARACTERS_PER_IDENTITY', 20).start()
        else:
            patch.object(self.memory, 'MAX_FACTS_PER_IDENTITY', 2).start()
        if batch:
            result = self.store.apply([{'action':'add', 'text':'cobalt'}, {'action':'add', 'text':'azure'}])
            self.assertFalse(result['success'])
            self.assertIn('limit', result['error'])
        else:
            result = self.store.add_receipt('overflow-entry')
            self.assertEqual(result['state'], 'failed')
            self.assertIsNone(result['record_id'])
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual({r['source'] for r in self.store.records()}, {'user'})

    def test_single_character_cap_rolls_back(self):
        self.check_rejected(batch=False, capacity='characters')

    def test_batch_character_cap_rolls_back(self):
        self.check_rejected(batch=True, capacity='characters')

    def test_single_fact_cap_rolls_back(self):
        self.check_rejected(batch=False, capacity='facts')

    def test_batch_fact_cap_rolls_back(self):
        self.check_rejected(batch=True, capacity='facts')


if __name__ == '__main__':
    unittest.main()
