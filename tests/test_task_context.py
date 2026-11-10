"""Real temporary SQLite acceptance for task focus; no owner state or network."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TaskContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, HOME=self.tmp.name, ZELINE_HOME=self.tmp.name,
                         ZELINE_API_KEY='fixture', ZELINE_BASE_URL='http://127.0.0.1:1/v1')
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.module = importlib.import_module('zeline.task_context')
        self.store = self.module.TaskContextStore(self.root / 'tasks.sqlite3')

    def test_starting_b_parks_a_without_losing_a(self):
        a = self.store.begin('owner', 'Build Project Amber')['task']
        b = self.store.begin('owner', 'Inspect Project Blue')['task']
        self.assertEqual(self.store.get('owner', a['id'])['status'], 'paused')
        self.assertEqual(self.store.focus('owner')['id'], b['id'])
        self.assertNotEqual(a['id'], b['id'])


    def test_reply_after_b_completes_and_restart_recovers_a_exact_checkpoint(self):
        a = self.store.begin('owner', 'Build Project Amber', message_id=10)['task']
        self.store.checkpoint('owner', a['id'], summary='Read amber.py; no write yet',
                              next_step='Run focused amber test', artifacts=['amber.py'], status='paused')
        b = self.store.begin('owner', 'Inspect Project Blue', message_id=20)['task']
        self.store.checkpoint('owner', b['id'], status='completed')
        rebuilt = self.module.TaskContextStore(self.store.path)
        self.assertIsNone(rebuilt.begin('owner', 'lanjut')['task'],
                          'Finishing B must not automatically select older A')
        resumed = rebuilt.begin('owner', 'lanjut', message_id=30, reply_to_message_id=10)
        self.assertEqual(resumed['task']['id'], a['id'])
        self.assertEqual(resumed['task']['next_step'], 'Run focused amber test')
        self.assertEqual(resumed['task']['artifacts'], ['amber.py'])
        self.assertEqual(resumed['task']['status'], 'active')
        self.assertEqual(resumed['reason'], 'resume')


    def test_named_resume_selects_old_task_and_refuses_ambiguous_topics(self):
        a = self.store.begin('owner', 'Build Project Amber dashboard')['task']
        self.store.begin('owner', 'Inspect Project Blue dashboard')
        result = self.store.begin('owner', 'lanjut Project Amber')
        self.assertEqual(result['task']['id'], a['id'])
        focused = self.store.focus('owner')['id']
        ambiguous = self.store.begin('owner', 'resume dashboard')
        self.assertIsNone(ambiguous['task'])
        self.assertEqual(ambiguous['reason'], 'ambiguous')
        self.assertEqual(len(ambiguous['candidates']), 2)
        self.assertEqual(self.store.focus('owner')['id'], focused)
        self.assertIsNone(self.store.begin('owner', 'resume nonexistent')['task'])


    def test_caps_reject_without_parking_or_deleting_unfinished_work(self):
        self.store.MAX_TASKS = 2
        a = self.store.begin('owner', 'Build Amber')['task']
        b = self.store.begin('owner', 'Build Blue')['task']
        with self.assertRaisesRegex(ValueError, 'capacity'):
            self.store.begin('owner', 'Build Copper')
        self.assertEqual(self.store.focus('owner')['id'], b['id'])
        self.assertEqual(self.store.get('owner', b['id'])['status'], 'active')
        self.assertIsNotNone(self.store.get('owner', a['id']))


    def test_render_prioritizes_operational_checkpoint_over_long_original_request(self):
        task = self.store.begin('owner', 'Amber ' + ('padding ' * 1800))['task']
        task = self.store.checkpoint('owner', task['id'], next_step='NEXT_STEP_MUST_SURVIVE',
                                    artifacts=['state/amber.json'])
        rendered = self.store.render(task)
        self.assertIn('NEXT_STEP_MUST_SURVIVE', rendered)
        self.assertIn('state/amber.json', rendered)
        self.assertLessEqual(len(rendered), 12000)


    def test_identity_isolation_and_unknown_reply_do_not_change_focus(self):
        a = self.store.begin('owner', 'Private Amber', message_id=10)['task']
        b = self.store.begin('other', 'Other project', message_id=20)['task']
        self.assertIsNone(self.store.get('other', a['id']))
        unknown = self.store.begin('other', 'lanjut', reply_to_message_id=10)
        self.assertEqual(unknown['reason'], 'missing_anchor')
        self.assertIsNone(unknown['task'])
        self.assertEqual(self.store.focus('other')['id'], b['id'])
        with self.assertRaises(ValueError):
            self.store.bind_message('other', a['id'], 30)

    def test_event_bounds_privacy_and_concurrent_updates(self):
        from concurrent.futures import ThreadPoolExecutor
        import stat
        a = self.store.begin('owner', 'Amber')['task']
        def record(index):
            self.store.checkpoint('owner', a['id'], tool_event={'id': str(index),
                'tool': 'read_file', 'status': 'success', 'result': 'x' * 5000})
            self.store.bind_message('owner', a['id'], 100 + index)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(record, range(8)))
        events = self.store.get('owner', a['id'])['tool_events']
        self.assertEqual({event['id'] for event in events}, {str(index) for index in range(8)})
        self.assertEqual(len(events), 8)
        self.assertTrue(all(len(event['result']) <= 1000 for event in events))
        for index in range(8):
            self.assertEqual(self.store.begin('owner', 'lanjut', reply_to_message_id=100 + index)
                             ['task']['id'], a['id'])
        self.assertEqual(self.store.begin('other', 'lanjut', reply_to_message_id=100)['reason'],
                         'missing_anchor')
        for index in range(40):
            self.store.checkpoint('owner', a['id'], tool_event={'id': 'later' + str(index),
                'tool': 'read_file', 'status': 'unknown', 'result': 'x' * 5000})
        events = self.store.get('owner', a['id'])['tool_events']
        self.assertEqual(len(events), 24)
        self.assertTrue(all(len(event['result']) <= 1000 for event in events))
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)

    def test_same_path_stores_wait_before_opening_competing_sqlite_writer(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event

        # A lexical alias must share coordination, not just identical Path objects.
        peer = self.module.TaskContextStore(self.root / '..' / self.root.name / 'tasks.sqlite3')
        task = self.store.begin('owner', 'Amber')['task']
        started, connected = Event(), Event()
        connect = self.module.sqlite3.connect

        def immediate_connect(*args, **kwargs):
            connected.set()
            kwargs['timeout'] = 0  # Expose real SQLite contention deterministically.
            return connect(*args, **kwargs)

        def update():
            started.set()
            peer.checkpoint('owner', task['id'], tool_event={
                'id': 'peer', 'tool': 'read_file', 'status': 'success', 'result': 'ok'})
            peer.bind_message('owner', task['id'], 101)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.store._connection():
                with patch.object(self.module.sqlite3, 'connect', side_effect=immediate_connect):
                    future = pool.submit(update)
                    self.assertTrue(started.wait(2))
                    opened_while_held = connected.wait(0.1)
                    if opened_while_held:
                        future.result(timeout=2)
            future.result(timeout=2)
        self.assertFalse(opened_while_held, 'Same-path writer reached SQLite before holder closed')
        self.assertEqual([e['id'] for e in peer.get('owner', task['id'])['tool_events']], ['peer'])
        self.assertEqual(peer.begin('owner', 'lanjut', reply_to_message_id=101)['task']['id'], task['id'])

    def test_distinct_paths_do_not_serialize_transactions(self):
        from concurrent.futures import ThreadPoolExecutor
        other = self.module.TaskContextStore(self.root / 'other.sqlite3')
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.store._connection():
                task = pool.submit(other.begin, 'owner', 'Independent').result(timeout=2)
        self.assertEqual(other.focus('owner')['id'], task['task']['id'])

    def test_same_path_multiple_stores_keep_all_concurrent_events_and_anchors(self):
        from concurrent.futures import ThreadPoolExecutor
        stores = [self.module.TaskContextStore(self.store.path) for _ in range(4)]
        task = self.store.begin('owner', 'Shared')['task']

        def record(index):
            store = stores[index % len(stores)]
            store.checkpoint('owner', task['id'], tool_event={
                'id': str(index), 'tool': 'read_file', 'status': 'success', 'result': 'x' * 5000})
            store.bind_message('owner', task['id'], 200 + index)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(record, range(8)))
        events = self.store.get('owner', task['id'])['tool_events']
        self.assertEqual(len(events), 8)
        self.assertEqual({e['id'] for e in events}, {str(i) for i in range(8)})
        self.assertTrue(all(len(e['result']) <= 1000 for e in events))
        for index in range(8):
            self.assertEqual(stores[index % 4].begin('owner', 'lanjut',
                             reply_to_message_id=200 + index)['task']['id'], task['id'])

    def test_transaction_wait_is_bounded_and_releases_after_timeout(self):
        from concurrent.futures import ThreadPoolExecutor
        peer = self.module.TaskContextStore(self.store.path)
        peer.CONNECTION_TIMEOUT = 0.05
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.store._connection():
                with patch.object(self.module.sqlite3, 'connect', wraps=self.module.sqlite3.connect) as connect:
                    future = pool.submit(peer.begin, 'owner', 'Must not persist')
                    with self.assertRaisesRegex(self.module.sqlite3.OperationalError, 'timed out'):
                        future.result(timeout=2)
                    connect.assert_not_called()
        self.assertIsNone(peer.focus('owner'))
        self.assertIsNotNone(peer.begin('owner', 'After timeout')['task'])

    def test_transaction_rolls_back_and_closes_on_body_and_commit_errors(self):
        connect = self.module.sqlite3.connect
        connections = []

        class TrackingConnection(self.module.sqlite3.Connection):
            closed = False
            rollbacks = 0
            fail_commit = False

            def close(db):
                db.closed = True
                return super().close()

            def rollback(db):
                db.rollbacks += 1
                return super().rollback()

            def commit(db):
                if db.fail_commit:
                    raise RuntimeError('commit failed')
                return super().commit()

        def tracked(*args, **kwargs):
            db = connect(*args, factory=TrackingConnection, **kwargs)
            connections.append(db)
            return db

        task = self.store.begin('owner', 'Amber')['task']
        for fail_commit in (False, True):
            with self.subTest(commit=fail_commit):
                with patch.object(self.module.sqlite3, 'connect', side_effect=tracked):
                    with self.assertRaisesRegex(RuntimeError, 'failed'):
                        with self.store._connection() as db:
                            self.store._bind(db, self.store._owner('owner'), task['id'], 999)
                            db.fail_commit = fail_commit
                            if not fail_commit:
                                raise RuntimeError('body failed')
                self.assertTrue(connections[-1].closed)
                self.assertEqual(connections[-1].rollbacks, 1)
                self.assertEqual(self.store.begin('owner', 'lanjut', reply_to_message_id=999)['reason'],
                                 'missing_anchor')
        with patch.object(self.module.sqlite3, 'connect', side_effect=OSError('open failed')):
            with self.assertRaisesRegex(OSError, 'open failed'):
                self.store.focus('owner')
        self.assertEqual(self.store.focus('owner')['id'], task['id'])

    def test_external_sqlite_writer_still_fails_closed_and_releases_connection(self):
        # Bypass the in-process coordinator, as a different process would.
        peer = self.module.TaskContextStore(self.store.path)
        peer.CONNECTION_TIMEOUT = 0.05
        external = self.module.sqlite3.connect(str(self.store.path))
        try:
            external.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(self.module.sqlite3.OperationalError, 'locked'):
                peer.begin('owner', 'Must not persist')
        finally:
            external.rollback()
            external.close()
        self.assertIsNone(peer.focus('owner'))
        self.assertIsNotNone(peer.begin('owner', 'After external writer')['task'])

    def test_unused_path_coordination_is_not_retained(self):
        import gc
        import weakref
        store = self.module.TaskContextStore(self.root / 'ephemeral.sqlite3')
        key = os.path.normcase(str(store.path.resolve()))
        lock = weakref.ref(store._transaction_lock)
        self.assertIn(key, self.module._PATH_LOCKS)
        del store
        gc.collect()
        self.assertIsNone(lock())
        self.assertNotIn(key, self.module._PATH_LOCKS)

    def test_os_temp_root_alias_is_canonicalized_but_nested_redirect_is_rejected(self):
        actual = self.root / 'actual-temp'
        actual.mkdir()
        alias = self.root / 'os-temp-alias'
        alias.symlink_to(actual, target_is_directory=True)
        with patch.object(self.module.tempfile, 'gettempdir', return_value=str(alias)):
            store = self.module.TaskContextStore(alias / 'state' / 'tasks.sqlite3')
            self.assertEqual(store.path, actual.resolve() / 'state' / 'tasks.sqlite3')
            self.assertIsNotNone(store.begin('owner', 'Portable task')['task'])
            (actual / 'nested-link').symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.module.TaskContextStore(alias / 'nested-link' / 'new' / 'tasks.sqlite3')

    def test_symlink_parent_rejected_before_creating_outside_directory(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        link = Path(self.tmp.name) / 'redirect'
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.module.TaskContextStore(link / 'new' / 'tasks.sqlite3')
        self.assertFalse((outside / 'new').exists())


if __name__ == '__main__':
    unittest.main()
