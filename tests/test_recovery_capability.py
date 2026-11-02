"""Recovery capability refusal must not poison healthy primary persistence."""
from contextlib import closing
import importlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


class UnsupportedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recovery = importlib.import_module('zeline.session_recovery')
        self.sessions = importlib.import_module('zeline.sessions')
        self.persistence = self.sessions.SessionPersistence(self.root / 'sessions.db')

    def test_sqlite_primary_codes_work_without_python311_module_constants(self):
        import sqlite3
        from types import SimpleNamespace
        recovery = importlib.import_module('zeline.session_recovery')
        error = sqlite3.OperationalError('fixture error')
        error.sqlite_errorcode = 5
        import sys
        with patch.dict(sys.modules, {'sqlite3': SimpleNamespace(Error=sqlite3.Error)}):
            self.assertEqual(recovery.failure_category(error), 'sqlite_busy')

    def test_unsupported_save_refuses_before_creating_root(self):
        with patch.object(self.recovery, '_supports_private_snapshots', return_value=False):
            with self.assertRaisesRegex(OSError, 'unsupported platform'):
                self.persistence.save_recovery('fixture', [], 'private title')
        self.assertFalse(self.persistence.recovery.root.exists())

    def test_unsupported_recovery_does_not_break_later_healthy_sqlite(self):
        messages = [{'role': 'user', 'content': 'fixture'},
                    {'role': 'assistant', 'content': 'completed fixture'}]
        with patch.object(self.recovery, '_supports_private_snapshots', return_value=False):
            with self.assertRaises(OSError):
                self.persistence.save_recovery('fixture', messages, 'private title')
            self.persistence.save_turn('fixture', messages, 'fixture', 'completed fixture', 'title')
            self.assertEqual(self.persistence.load('fixture'), (messages, 'title'))
        with closing(self.persistence._connect()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM archive').fetchone()[0], 2)
        self.assertFalse(self.persistence.recovery.root.exists())

    @unittest.skipUnless(__import__('os').name == 'posix', 'Create a private POSIX journal fixture')
    def test_existing_private_journal_is_not_ignored_on_unsupported_platform(self):
        self.persistence.save_recovery('fixture', [], 'private title')
        with patch.object(self.recovery, '_supports_private_snapshots', return_value=False):
            with self.assertRaisesRegex(OSError, 'unsupported platform'):
                self.persistence.recovery.load('fixture')
        self.assertTrue(self.persistence.recovery.root.exists())
