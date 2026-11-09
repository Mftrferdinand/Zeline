"""Offline candidate-only SQLite read/write isolation contracts."""
import atexit
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import time
import unittest

_HOME = tempfile.TemporaryDirectory(prefix='zeline-memory-lock-')
atexit.register(_HOME.cleanup)
os.environ['HOME'] = _HOME.name
os.environ['ZELINE_HOME'] = str(Path(_HOME.name) / '.zeline')
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
def _no_network(*args, **kwargs):
    raise AssertionError('Actual network forbidden')
socket.socket.connect = _no_network
socket.create_connection = _no_network
from zeline import memory
assert Path(memory.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[2] / 'zeline')
assert memory.config.DATA_DIR.is_relative_to(Path(_HOME.name))


class MemoryReadLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=_HOME.name)
        self.addCleanup(self.tmp.cleanup)
        memory.MEMORY_DIR = Path(self.tmp.name) / 'memory'
        memory.LEGACY_FILE = Path(self.tmp.name) / 'memory.json'
        self.store = memory.MemoryStore('test:reader')

    def populate(self):
        report = self.store.apply([{'action': 'add', 'text': 'committed sapphire memory'}])
        self.assertTrue(report['success'], report)
        return self.store.records()[0]

    def test_automatic_retrieval_is_bounded_best_effort_under_contention(self):
        record = self.populate()
        with sqlite3.connect(self.store.db_path) as held:
            held.execute('BEGIN IMMEDIATE')
            start = time.monotonic()
            self.store.record_retrieval('busy-turn', [record])
            self.assertLess(time.monotonic() - start, 0.6)
        self.assertEqual(self.store.retrievals('busy-turn'), [])
        self.store.record_retrieval('free-turn', [record])
        self.assertEqual(self.store.retrievals('free-turn')[0]['record_id'], record['id'])

    def test_committed_records_and_recall_ignore_writer_reservation(self):
        record = self.populate()
        with sqlite3.connect(self.store.db_path) as held:
            held.execute('BEGIN IMMEDIATE')
            held.execute('DELETE FROM memory_items WHERE owner=?', (self.store.key,))
            start = time.monotonic()
            rows = self.store.records()
            recalled = self.store.recall('sapphire')
            elapsed = time.monotonic() - start
            self.assertEqual(rows, [record])
            self.assertEqual(recalled['records'][0]['id'], record['id'])
            self.assertLess(elapsed, 0.5, f'read blocked for {elapsed:.3f}s')
            print(f'held-writer records+recall: {elapsed:.4f}s')


if __name__ == '__main__':
    unittest.main(verbosity=2)
