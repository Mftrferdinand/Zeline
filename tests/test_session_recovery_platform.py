"""Portable recovery guards, exercised without a Windows host or owner data."""
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == 'posix', 'Positive private journal fixtures require POSIX')
class RecoveryPlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.module = importlib.import_module('zeline.session_recovery')
        self.recovery = self.module.RecoverySnapshots(self.home / 'sessions.db')

    def test_non_posix_writable_modes_fail_closed_without_acl_validation(self):
        self.recovery.save('fixture', [{'role': 'assistant', 'content': 'retained'}], None, 'token')
        # A mock Windows branch is not evidence of ACL privacy. Keep it fail-closed.
        self.recovery.root.chmod(0o777)
        self.recovery._path('fixture').chmod(0o666)
        with patch.object(self.module.os, 'name', 'nt'), self.assertRaises(OSError):
            self.recovery.load('fixture')

    def test_directory_fsync_failure_is_visible_not_claimed_durable(self):
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


if __name__ == '__main__':
    unittest.main()
