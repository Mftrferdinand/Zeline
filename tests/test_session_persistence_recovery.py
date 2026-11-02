"""Offline completed-turn recovery; all state belongs to disposable identities."""
from contextlib import closing
import importlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = patch.dict(os.environ, {
            'HOME': str(self.home), 'ZELINE_HOME': str(self.home / 'state'),
            'ZELINE_API_KEY': 'fixture', 'ZELINE_MODEL': 'fixture',
            'ZELINE_BASE_URL': 'http://127.0.0.1:1/v1',
        })
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.sessions = importlib.import_module('zeline.sessions')
        self.persistence = self.sessions.SessionPersistence(self.home / 'state' / 'sessions.db')
        self.store = self.sessions.SessionStore(persistence=self.persistence)
        self.identity = 'telegram:private-fixture'
        self.session = self.store.get_or_create(self.identity, 'safe', workspace=str(self.home))
        self.messages = [
            {'role': 'user', 'content': 'write fixture'},
            {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'effect-1', 'type': 'function', 'function': {
                    'name': 'write_file', 'arguments': '{"path":"fixture","content":"done"}'}}]},
            {'role': 'tool', 'tool_call_id': 'effect-1', 'content': 'written'},
            {'role': 'assistant', 'content': 'completed fixture'},
        ]

    def fail_archive(self):
        with closing(self.persistence._connect()) as conn, conn:
            conn.execute("CREATE TRIGGER fixture_fail BEFORE INSERT ON archive "
                         "BEGIN SELECT RAISE(ABORT, 'PRIVATE-EXCEPTION'); END")

    def completed(self):
        def send(*args, **kwargs):
            self.session.agent.load_history(self.messages)
            return 'completed fixture'
        with patch.object(self.session.agent, 'send', side_effect=send):
            return self.store.send(self.identity, 'write fixture', 'safe')

    @unittest.skipUnless(os.name == 'posix', 'Private recovery requires POSIX privacy and directory fsync')
    def test_completed_failure_survives_restart_without_sqlite(self):
        self.fail_archive()
        reply = self.completed()
        self.assertIn('gagal disimpan', reply)
        with closing(self.persistence._connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)
        with patch.object(self.sessions.SessionPersistence, '_connect',
                          side_effect=sqlite3.OperationalError('PRIVATE-EXCEPTION')):
            reopened = self.sessions.SessionPersistence(self.persistence.path)
            rebuilt = self.sessions.SessionStore(persistence=reopened)
            restored = rebuilt.get_or_create(self.identity, 'safe', workspace=str(self.home))
            self.assertEqual([m for m in restored.agent.export_history()
                              if m['role'] != 'system'
                              and not str(m.get('content', '')).startswith('[Zeline recovery]')], self.messages)
            self.assertTrue(restored.persistence_error)
            self.assertNotIn('PRIVATE-EXCEPTION', restored.persistence_error)
            with self.assertRaises(self.sessions.ZelineError):
                rebuilt.get_or_create('telegram:other', 'safe', workspace=str(self.home))

    @unittest.skipUnless(os.name == 'posix', 'Private recovery requires POSIX privacy and directory fsync')
    def test_later_success_and_reset_do_not_resurrect_stale_recovery(self):
        self.fail_archive()
        self.completed()
        with closing(self.persistence._connect()) as conn, conn:
            conn.execute('DROP TRIGGER fixture_fail')
        newer = [{'role': 'user', 'content': 'new'}, {'role': 'assistant', 'content': 'new answer'}]
        self.persistence.save_turn(self.identity, newer, 'new', 'new answer', 'new title')
        with patch.object(self.persistence, '_connect', side_effect=sqlite3.OperationalError('offline')):
            history, title = self.persistence.load(self.identity)
            self.assertEqual(history[:-1], newer)
            self.assertEqual(title, 'new title')
        self.persistence.reset(self.identity)
        with patch.object(self.persistence, '_connect', side_effect=sqlite3.OperationalError('offline')):
            self.assertEqual(self.persistence.load(self.identity), ([], None))

    @unittest.skipUnless(os.name == 'posix', 'Private recovery requires POSIX privacy and directory fsync')
    def test_new_checkpoint_after_recovery_preserves_unknown_outcomes(self):
        self.fail_archive()
        self.completed()
        pending = self.messages + [{'role': 'user', 'content': 'next mutation'},
                                   self.messages[1]]
        self.persistence.save_checkpoint(self.identity, pending, 'next task')
        with patch.object(self.persistence, '_connect', side_effect=sqlite3.OperationalError('offline')):
            history, title = self.persistence.load(self.identity)
        self.assertEqual(history[:-1], pending)
        self.assertIn('UNKNOWN', history[-1]['content'])
        self.assertEqual(title, 'next task')

    def test_recovery_failure_is_visible(self):
        self.fail_archive()
        recovery = importlib.import_module('zeline.session_recovery')
        with patch.object(recovery.os, 'replace', side_effect=OSError('PRIVATE-EXCEPTION')):
            reply = self.completed()
        self.assertIn('Pemulihan lokal juga gagal', reply)
        self.assertIn('recovery failed (storage_error)', self.session.persistence_error)
        self.assertNotIn('PRIVATE-EXCEPTION', reply + self.session.persistence_error)
        self.assertIsNone(self.persistence.recovery.load(self.identity))

    @unittest.skipUnless(os.name == 'posix', 'Private recovery requires POSIX privacy and directory fsync')
    def test_private_atomic_bounded_snapshots_keep_previous_on_failure(self):
        recovery = importlib.import_module('zeline.session_recovery')
        self.persistence.save_recovery(self.identity, self.messages, 'title')
        root = self.persistence.recovery.root
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        files = list(root.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.identity, files[0].name)
        before = files[0].read_bytes()
        with patch.object(recovery.os, 'replace', side_effect=OSError('private')):
            with self.assertRaises(OSError):
                self.persistence.save_recovery(self.identity, [], 'replacement')
        self.assertEqual(files[0].read_bytes(), before)
        with patch.object(recovery, 'MAX_RECOVERY_BYTES', 10):
            with self.assertRaises(ValueError):
                self.persistence.save_recovery(self.identity, self.messages)
        with patch.object(recovery, 'MAX_RECOVERY_IDENTITIES', 1):
            with self.assertRaises(ValueError):
                self.persistence.save_recovery('another identity', [])
        self.assertEqual(files[0].read_bytes(), before)
        target = self.home / 'outside'
        target.write_text('untouched')
        files[0].unlink()
        files[0].symlink_to(target)
        with self.assertRaises(OSError):
            self.persistence.recovery.load(self.identity)
        self.assertEqual(target.read_text(), 'untouched')

    def test_checkpoint_diagnostics_use_allowlist_not_exception_class(self):
        class PRIVATE_CREDENTIAL_EXCEPTION(Exception):
            pass
        with patch.object(self.persistence, 'save_checkpoint',
                          side_effect=PRIVATE_CREDENTIAL_EXCEPTION('PRIVATE-EXCEPTION')):
            with self.assertLogs('zeline.sessions', level='WARNING') as logs:
                with self.assertRaises(self.sessions.ZelineError):
                    self.store.send(self.identity, 'do not execute', 'safe')
        details = self.session.persistence_error + str(logs.output)
        self.assertNotIn('PRIVATE_CREDENTIAL', details)
        self.assertIn('persistence_error', details)

    @unittest.skipUnless(os.name == 'posix', 'Private recovery requires POSIX privacy and directory fsync')
    def test_unwritable_recovery_blocks_later_commit_instead_of_stale_restore(self):
        self.fail_archive()
        self.completed()
        with closing(self.persistence._connect()) as conn, conn:
            conn.execute('DROP TRIGGER fixture_fail')
        with patch.object(self.persistence.recovery, 'save', side_effect=OSError('private')):
            with self.assertRaises(OSError):
                self.persistence.save_turn(self.identity, [], 'new', 'new')
        with closing(self.persistence._connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)
        self.assertEqual(self.persistence.recovery.load(self.identity)['messages'][-1], self.messages[-1])


if __name__ == '__main__':
    unittest.main()
