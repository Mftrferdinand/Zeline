"""Checkpoint mutation failure boundaries under temporary state only."""
import importlib
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from unittest.mock import patch


class CheckpointTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / 'checkpoints'
        self.cp = importlib.import_module('zeline.checkpoints')
        scope = patch.object(self.cp, '_root', return_value=self.root)
        scope.start()
        self.addCleanup(scope.stop)
        enabled = patch.object(self.cp, 'enabled', return_value=True)
        enabled.start()
        self.addCleanup(enabled.stop)

    def file(self, name, text):
        target = self.home / name
        target.write_text(text, encoding='utf-8')
        return target

    def test_threads_do_not_lose_updates(self):
        first = self.file('a.txt', 'a')
        second = self.file('b.txt', 'b')
        entered = threading.Event()
        release = threading.Event()
        attempted = threading.Event()
        loaded = threading.Event()
        original = self.cp._load_index

        def load():
            entries = original()
            if threading.current_thread().name.endswith('_0'):
                entered.set()
                self.assertTrue(release.wait(5))
            else:
                loaded.set()
            return entries

        def other():
            attempted.set()
            return self.cp.snapshot(second)

        with patch.object(self.cp, '_load_index', side_effect=load):
            with ThreadPoolExecutor(2) as pool:
                one = pool.submit(self.cp.snapshot, first)
                self.assertTrue(entered.wait(5))
                two = pool.submit(other)
                self.assertTrue(attempted.wait(5))
                try:
                    self.assertFalse(loaded.wait(0.2), 'contender read uncommitted index')
                finally:
                    release.set()
                ids = [one.result(5), two.result(5)]
        self.assertNotIn(None, ids)
        self.assertEqual({x['id'] for x in self.cp.list_checkpoints()}, set(ids))

    def test_index_temp_is_unique_private_synced_and_cleaned(self):
        import os
        seen = []
        replace = os.replace

        def publish(source, destination):
            source = Path(source)
            seen.append(source.name)
            if os.name != 'nt':
                self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            return replace(source, destination)

        with patch.object(self.cp.os, 'replace', side_effect=publish), patch.object(
                self.cp.os, 'fsync', wraps=os.fsync) as sync:
            self.cp.snapshot(self.file('a.txt', 'a'))
            self.cp.snapshot(self.file('b.txt', 'b'))
            self.assertGreaterEqual(sync.call_count, 2)
        self.assertEqual(len(set(seen)), 2)
        self.assertFalse(list(self.root.glob('*.tmp')))
        with patch.object(self.cp.os, 'fsync', side_effect=OSError('full')):
            self.assertIsNone(self.cp.snapshot(self.file('c.txt', 'c')))
        self.assertFalse(list(self.root.glob('*.tmp')))
        for entry in self.cp.list_checkpoints():
            self.assertTrue((self.root / entry['blob']).is_file())

    def test_restore_refuses_to_destroy_current_bytes_without_undo(self):
        target = self.file('a.txt', 'original')
        checkpoint = self.cp.snapshot(target)
        target.write_text('current')
        with patch.object(self.cp, '_save_index', side_effect=OSError('full')):
            self.assertFalse(self.cp.restore(checkpoint)[0])
        self.assertEqual(target.read_text(), 'current')

    def test_processes_share_mutation_lock_and_keep_both_updates(self):
        import os
        import subprocess
        import sys
        script = '''
import sys
from zeline import checkpoints as cp
path, role = sys.argv[1:]
load = cp._load_index
connect = cp.sqlite3.connect
def connecting(*args, **kwargs):
    print('attempt', flush=True)
    return connect(*args, **kwargs)
cp.sqlite3.connect = connecting
def loading():
    result = load()
    if role == 'holder':
        print('loaded', flush=True)
        sys.stdin.readline()
    return result
cp._load_index = loading
print(cp.snapshot(path), flush=True)
'''
        env = dict(os.environ, ZELINE_HOME=str(self.home))
        one = subprocess.Popen([sys.executable, '-c', script,
                                str(self.file('a.txt', 'a')), 'holder'],
                               env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: one.poll() is None and one.kill())
        self.assertEqual(one.stdout.readline().strip(), 'attempt')
        self.assertEqual(one.stdout.readline().strip(), 'loaded')
        two = subprocess.Popen([sys.executable, '-c', script,
                                str(self.file('b.txt', 'b')), 'contender'],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
        self.addCleanup(lambda: two.poll() is None and two.kill())
        self.assertEqual(two.stdout.readline().strip(), 'attempt')
        output1, error1 = one.communicate('\n', timeout=5)
        output2, error2 = two.communicate(timeout=5)
        self.assertEqual((one.returncode, two.returncode), (0, 0), error1 + error2)
        ids = {output1.strip(), output2.strip()}
        self.assertNotIn('None', ids)
        self.assertEqual({x['id'] for x in self.cp.list_checkpoints()}, ids)

    def test_lock_failures_close_connections_and_leave_writer_free(self):
        import sqlite3
        from zeline import tools
        target = self.file('a.txt', 'before')
        actual = self.cp.sqlite3.connect
        connections = []

        class BrokenConnection:
            closed = False

            def execute(self, statement):
                raise sqlite3.OperationalError('busy')

            def close(self):
                self.closed = True

        def broken(*args, **kwargs):
            connection = BrokenConnection()
            connections.append(connection)
            return connection

        with patch.object(self.cp.sqlite3, 'connect', side_effect=broken):
            self.assertIsNone(self.cp.snapshot(target))
            self.assertFalse(self.cp.restore('missing')[0])
            self.assertEqual(self.cp.clear(), 0)
            result = tools._write_file(str(target), 'after', self.home)
            self.assertIn('OK', result)
        self.assertEqual(target.read_text(), 'after')
        self.assertTrue(all(connection.closed for connection in connections))
        with patch.object(self.cp.sqlite3, 'connect', actual):
            self.assertIsNotNone(self.cp.snapshot(target))

    def test_post_replace_fsync_failure_keeps_referenced_blob(self):
        import os
        if os.name == 'nt':
            self.skipTest('Windows does not support directory fsync')
        sync = os.fsync

        published = threading.Event()
        replace = os.replace

        def publish(*args):
            replace(*args)
            published.set()

        def syncing(fd):
            if published.is_set():
                raise OSError('directory fsync failed')
            sync(fd)

        with patch.object(self.cp.os, 'replace', side_effect=publish), patch.object(
                self.cp.os, 'fsync', side_effect=syncing):
            self.assertIsNone(self.cp.snapshot(self.file('a.txt', 'saved')))
        entries = self.cp.list_checkpoints()
        self.assertEqual(len(entries), 1)
        self.assertEqual((self.root / entries[0]['blob']).read_text(), 'saved')

    def test_restore_serializes_prune_until_target_copy_and_is_undoable(self):
        target = self.file('a.txt', 'old')
        checkpoint = self.cp.snapshot(target)
        target.write_text('current')
        copied = threading.Event()
        release = threading.Event()
        attempted = threading.Event()
        loaded = threading.Event()
        copy = self.cp.shutil.copy2
        load = self.cp._load_index

        def copying(source, destination, *args, **kwargs):
            if Path(destination) == target:
                copied.set()
                self.assertTrue(release.wait(5))
            return copy(source, destination, *args, **kwargs)

        def loading():
            if threading.current_thread().name.endswith('_1'):
                loaded.set()
            return load()

        def prune():
            attempted.set()
            return self.cp.snapshot(self.file('b.txt', 'b'))

        with patch.object(self.cp, 'MAX_SNAPSHOTS', 2), patch.object(
                self.cp.shutil, 'copy2', side_effect=copying), patch.object(
                self.cp, '_load_index', side_effect=loading):
            with ThreadPoolExecutor(2) as pool:
                restoring = pool.submit(self.cp.restore, checkpoint)
                self.assertTrue(copied.wait(5))
                pruning = pool.submit(prune)
                self.assertTrue(attempted.wait(5))
                try:
                    self.assertFalse(loaded.wait(0.2))
                finally:
                    release.set()
                self.assertTrue(restoring.result(5)[0])
                self.assertIsNotNone(pruning.result(5))
        self.assertEqual(target.read_text(), 'old')
        undo = [x for x in self.cp.list_checkpoints() if x['reason'] == 'pre-restore']
        self.assertEqual(len(undo), 1)
        self.assertTrue(self.cp.restore(undo[0]['id'])[0])
        self.assertEqual(target.read_text(), 'current')

    def test_clear_waits_for_restore_target_copy(self):
        target = self.file('a.txt', 'old')
        checkpoint = self.cp.snapshot(target)
        target.write_text('current')
        copying = threading.Event()
        release = threading.Event()
        attempting = threading.Event()
        deleting = threading.Event()
        copy = self.cp.shutil.copy2
        save = self.cp._save_index

        def copy_bytes(source, destination, *args, **kwargs):
            if Path(destination) == target:
                copying.set()
                self.assertTrue(release.wait(5))
            return copy(source, destination, *args, **kwargs)

        def saving(entries):
            if not entries:
                deleting.set()
            return save(entries)

        def clearing():
            attempting.set()
            return self.cp.clear()

        with patch.object(self.cp.shutil, 'copy2', side_effect=copy_bytes), patch.object(
                self.cp, '_save_index', side_effect=saving):
            with ThreadPoolExecutor(2) as pool:
                restoring = pool.submit(self.cp.restore, checkpoint)
                self.assertTrue(copying.wait(5))
                clearing_future = pool.submit(clearing)
                self.assertTrue(attempting.wait(5))
                try:
                    self.assertFalse(deleting.wait(0.2))
                finally:
                    release.set()
                self.assertTrue(restoring.result(5)[0])
                self.assertEqual(clearing_future.result(5), 2)
        self.assertEqual(target.read_text(), 'old')
        self.assertEqual(self.cp.list_checkpoints(), [])

    def test_blob_is_synced_before_index_publication(self):

        import os
        import stat
        synced = []
        sync = os.fsync
        save = self.cp._save_index

        def syncing(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                synced.extend(str(blob) for blob in self.root.glob('*.blob')
                              if os.path.samestat(os.fstat(fd), blob.stat()))
            sync(fd)

        def saving(entries):
            self.assertTrue(any(name.endswith('.blob') for name in synced))
            save(entries)

        with patch.object(self.cp.os, 'fsync', side_effect=syncing), patch.object(
                self.cp, '_save_index', side_effect=saving):
            self.assertIsNotNone(self.cp.snapshot(self.file('a.txt', 'a')))

    def test_failed_publication_cleans_unpublished_blob(self):
        self.cp.snapshot(self.file('a.txt', 'a'))
        before = set(self.root.glob('*.blob'))
        with patch.object(self.cp, '_save_index', side_effect=OSError('full')):
            self.assertIsNone(self.cp.snapshot(self.file('b.txt', 'b')))
        self.assertEqual(set(self.root.glob('*.blob')), before)

    def test_clear_keeps_published_empty_index(self):

        self.cp.snapshot(self.file('a.txt', 'a'))
        self.assertEqual(self.cp.clear(), 1)
        self.assertEqual(self.cp._load_index(), [])
        self.assertTrue(self.cp._index_path().exists())

    def test_clear_commit_failure_preserves_blobs(self):
        checkpoint = self.cp.snapshot(self.file('a.txt', 'a'))
        before = self.cp._index_path().read_bytes()
        with patch.object(self.cp, '_save_index', side_effect=OSError('full')):
            self.assertEqual(self.cp.clear(), 0)
        self.assertEqual(self.cp._index_path().read_bytes(), before)
        self.assertTrue((self.root / (checkpoint + '.blob')).is_file())

    def test_connection_close_failure_releases_admission(self):
        import sqlite3

        class CloseFailure:
            def execute(self, statement):
                return None

            def close(self):
                raise sqlite3.OperationalError('injected close failure')

        with patch.object(self.cp, '_MUTEX', threading.RLock()), patch.object(
                self.cp, '_LOCAL', threading.local()):
            with patch.object(self.cp.sqlite3, 'connect', return_value=CloseFailure()):
                try:
                    result = self.cp.snapshot(self.file('close.txt', 'saved'))
                except sqlite3.Error:
                    self.fail('connection close escaped the best-effort boundary')
                self.assertIsNone(result)
            with ThreadPoolExecutor(1) as pool:
                self.assertIsNotNone(pool.submit(self.cp.snapshot, self.home / 'close.txt').result(5))

    def test_clock_rollback_preserves_restore_undo_at_capacity(self):
        target = self.file('clock.txt', 'old')
        with patch.object(self.cp, 'MAX_SNAPSHOTS', 1):
            with patch.object(self.cp.time, 'time', return_value=200):
                checkpoint = self.cp.snapshot(target)
            target.write_text('current', encoding='utf-8')
            with patch.object(self.cp.time, 'time', return_value=100):
                ok, message = self.cp.restore(checkpoint)
            self.assertTrue(ok, message)
            entries = self.cp.list_checkpoints()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['reason'], 'pre-restore')
            self.assertEqual((self.root / entries[0]['blob']).read_text(), 'current')
            with patch.object(self.cp.time, 'time', return_value=50):
                ok, message = self.cp.restore(entries[0]['id'])
            self.assertTrue(ok, message)
            self.assertEqual(target.read_text(), 'current')

    def test_read_error_does_not_replace_existing_index(self):
        checkpoint = self.cp.snapshot(self.file('read-error.txt', 'saved'))
        before = self.cp._index_path().read_bytes()
        read = Path.read_text

        def reading(path, *args, **kwargs):
            if path == self.cp._index_path():
                raise PermissionError('injected transient read failure')
            return read(path, *args, **kwargs)

        with patch.object(Path, 'read_text', reading):
            self.assertIsNone(self.cp.snapshot(self.file('contender.txt', 'new')))
            self.assertEqual(self.cp.clear(), 0)
        self.assertEqual(self.cp._index_path().read_bytes(), before)
        self.assertTrue((self.root / (checkpoint + '.blob')).exists())

    def test_blob_fsync_uses_write_capable_descriptor(self):
        import os
        import stat
        sync = os.fsync

        def syncing(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                # Windows FlushFileBuffers needs a write-capable handle. A
                # zero-byte write proves access mode without altering content.
                os.write(fd, b'')
            return sync(fd)

        with patch.object(self.cp.os, 'fsync', side_effect=syncing):
            checkpoint = self.cp.snapshot(self.file('writable.txt', 'unchanged'))
        self.assertIsNotNone(checkpoint)
        self.assertEqual((self.root / (checkpoint + '.blob')).read_text(), 'unchanged')

    def test_failed_index_commit_does_not_prune_published_blobs(self):

        first = self.cp.snapshot(self.file('first.txt', 'original'))
        before = self.cp._index_path().read_bytes()
        with patch.object(self.cp, 'MAX_SNAPSHOTS', 1), patch.object(
                self.cp, '_save_index', side_effect=OSError('index unavailable')):
            result = self.cp.snapshot(self.file('second.txt', 'new'))
        self.assertIsNone(result)
        self.assertEqual(self.cp._index_path().read_bytes(), before)
        self.assertTrue((self.root / (first + '.blob')).is_file(),
                        'failed publication destroyed a previously published checkpoint')
