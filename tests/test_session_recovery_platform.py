"""Portable recovery guards and native Windows ACL acceptance; disposable state only."""
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class RecoveryPlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.module = importlib.import_module('zeline.session_recovery')
        self.recovery = self.module.RecoverySnapshots(self.home / 'sessions.db')

    def test_non_posix_writable_modes_fail_closed_without_acl_validation(self):
        self.recovery.save('fixture', [{'role': 'assistant', 'content': 'retained'}], None, 'token')
        # Missing/failed native ACL validation must remain fail-closed.
        with patch.object(self.module.os, 'name', 'nt'), \
             patch.object(self.module, '_windows_private', return_value=False), \
             self.assertRaises(ValueError):
            self.recovery.load('fixture')

    def test_directory_fsync_failure_is_visible_not_claimed_durable(self):
        if os.name == 'nt':
            with patch.object(self.module.os, 'fsync', side_effect=OSError('flush failed')), \
                 self.assertRaises(OSError):
                self.recovery.save('fixture', [], None, 'token')
            return
        original = self.module.os.open
        def open_without_directory_handles(path, flags, *args, **kwargs):
            if flags & getattr(self.module.os, 'O_DIRECTORY', 0):
                raise PermissionError('fixture: directory handle not supported')
            return original(path, flags, *args, **kwargs)
        with patch.object(self.module.os, 'open', side_effect=open_without_directory_handles), \
             self.assertRaises(OSError):
            self.recovery.save('fixture', [], None, 'token')

    def test_symlink_is_rejected_even_without_o_nofollow(self):
        self.recovery.save('fixture', [], None, 'token')
        target = self.home / 'outside.json'
        self.recovery._path('fixture').replace(target)
        self.recovery._path('fixture').symlink_to(target)
        with patch.object(self.module.os, 'O_NOFOLLOW', 0, create=True):
            with self.assertRaises(OSError):
                self.recovery.load('fixture')

    def test_orphaned_staging_files_cannot_bypass_existing_identity_cap(self):
        self.recovery.save('fixture', [], None, 'token')
        for index in range(3):
            (self.recovery.root / f'.pending-fixture-{index}').write_bytes(b'fixture')
        with patch.object(self.module, 'MAX_RECOVERY_IDENTITIES', 2):
            with self.assertRaises(ValueError):
                self.recovery.save('fixture', [], None, 'new-token')
        self.assertEqual(self.recovery.load('fixture')['token'], 'token')

    def test_failure_category_works_without_python_sqlite_constants(self):
        import sqlite3
        import sys
        from types import SimpleNamespace
        error = sqlite3.OperationalError('private detail')
        error.sqlite_errorcode = 5 | (1 << 8)
        with patch.dict(sys.modules, {'sqlite3': SimpleNamespace(Error=sqlite3.Error)}):
            self.assertEqual(self.module.failure_category(error), 'sqlite_busy')

    @unittest.skipUnless(os.name == 'nt', 'native Windows ACL acceptance')
    def test_native_acl_rejects_snapshot_replaced_by_public_file(self):
        self.recovery.save('fixture', [], None, 'token')
        self.assertEqual(self.recovery.load('fixture')['token'], 'token')
        public = self.home / 'public.json'
        public.write_bytes(self.recovery._path('fixture').read_bytes())
        os.replace(public, self.recovery._path('fixture'))
        with self.assertRaises(OSError):
            self.recovery.load('fixture')


if __name__ == '__main__':
    unittest.main()
