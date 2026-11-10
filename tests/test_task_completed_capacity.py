"""Unfinished capacity regressions using private temporary SQLite databases."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TaskCompletedCapacityTests(unittest.TestCase):
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
        self.module = importlib.import_module('zeline.task_context')
        self.store = self.module.TaskContextStore(Path(self.tmp.name) / 'tasks.sqlite3')
        self.store.MAX_TASKS = 2

    def test_terminal_history_does_not_exhaust_unfinished_capacity(self):
        for terminal in ('completed', 'cancelled'):
            with self.subTest(status=terminal):
                owner = terminal
                history = []
                for index in range(6):
                    task = self.store.begin(owner, f'Historical task {index}',
                                            message_id=100 + index)['task']
                    record = self.store.checkpoint(owner, task['id'], status=terminal,
                                                   summary=f'Checkpoint {index}',
                                                   artifacts=[f'output-{index}.txt'])
                    history.append(record)
                for record in history:
                    self.assertEqual(self.store.get(owner, record['id']), record)
                self.assertEqual(self.store.begin(owner, 'lanjut')['reason'], 'no_task')
                first = self.store.begin(owner, 'Unfinished first')['task']
                second = self.store.begin(owner, 'Unfinished second')['task']
                with self.assertRaisesRegex(ValueError, 'capacity'):
                    self.store.begin(owner, 'Over capacity')
                self.assertEqual(self.store.get(owner, first['id'])['status'], 'paused')
                self.assertEqual(self.store.get(owner, second['id'])['status'], 'active')
                self.assertEqual(self.store.focus(owner)['id'], second['id'])
                for record in history:
                    self.assertEqual(self.store.get(owner, record['id']), record)

    def test_terminal_resume_consumes_capacity_and_rejection_is_atomic(self):
        for terminal in ('completed', 'cancelled'):
            for route in ('named', 'reply', 'checkpoint'):
                with self.subTest(status=terminal, route=route):
                    owner = terminal + route
                    archived = self.store.begin(owner, 'Unique historical amber', message_id=10)['task']
                    archived = self.store.checkpoint(owner, archived['id'], status=terminal,
                                                     summary='Keep evidence', next_step='Inspect output',
                                                     artifacts=['amber.txt'])
                    first = self.store.begin(owner, 'First unfinished')['task']
                    second = self.store.begin(owner, 'Second unfinished', message_id=20)['task']
                    snapshots = {task['id']: self.store.get(owner, task['id'])
                                 for task in (archived, first, second)}

                    def resume():
                        if route == 'checkpoint':
                            return self.store.checkpoint(owner, archived['id'], status='active')
                        if route == 'named':
                            return self.store.begin(owner, 'resume Unique historical amber', message_id=30)['task']
                        return self.store.begin(owner, 'lanjut', message_id=30,
                                                reply_to_message_id=10)['task']

                    with self.assertRaisesRegex(ValueError, 'capacity'):
                        resume()
                    for task_id, snapshot in snapshots.items():
                        self.assertEqual(self.store.get(owner, task_id), snapshot)
                    self.assertEqual(self.store.focus(owner)['id'], second['id'])
                    self.assertEqual(self.store.begin(owner, 'lanjut', reply_to_message_id=30)['reason'],
                                     'missing_anchor')
                    self.store.checkpoint(owner, second['id'], status=terminal)
                    resumed = resume()
                    self.assertEqual(resumed['id'], archived['id'])
                    self.assertEqual(resumed['status'], 'active')
                    for key in ('summary', 'next_step', 'artifacts', 'request', 'created_at'):
                        self.assertEqual(resumed[key], archived[key])
                    with self.assertRaisesRegex(ValueError, 'capacity'):
                        self.store.begin(owner, 'Still over capacity')
                    # Resuming an already-unfinished task requires no extra slot.
                    self.assertEqual(self.store.begin(owner, 'resume First unfinished')['task']['id'], first['id'])

    def test_historical_lookup_reply_and_named_resume_survive_restart_and_are_private(self):
        history = []
        for index in range(6):
            task = self.store.begin('owner', f'Private amber {index}', message_id=100 + index)['task']
            history.append(self.store.checkpoint('owner', task['id'],
                                                status='completed' if index % 2 else 'cancelled',
                                                summary=f'Evidence {index}'))
        self.store = self.module.TaskContextStore(self.store.path)
        self.store.MAX_TASKS = 2
        other = self.store.begin('other', 'Other focus', message_id=200)['task']
        for index, record in enumerate(history):
            self.assertEqual(self.store.get('owner', record['id']), record)
            self.assertIsNone(self.store.get('other', record['id']))
            self.assertEqual(self.store.begin('other', f'resume {record["id"]}')['reason'], 'no_task')
            self.assertEqual(self.store.begin('other', 'lanjut', reply_to_message_id=100 + index)['reason'],
                             'missing_anchor')
            self.assertEqual(self.store.focus('other')['id'], other['id'])
        for index, record in enumerate(history):
            named = self.store.begin('owner', f'resume {record["id"]}')
            self.assertEqual(named['reason'], 'resume')
            self.assertEqual(named['task']['id'], record['id'])
            self.store.checkpoint('owner', record['id'], status=record['status'])
            reply = self.store.begin('owner', 'lanjut', reply_to_message_id=100 + index)
            self.assertEqual(reply['task']['id'], record['id'])
            self.assertEqual(reply['task']['summary'], record['summary'])
            self.store.checkpoint('owner', record['id'], status=record['status'])

    def test_historical_only_owners_still_count_for_owner_limit(self):
        self.store.MAX_OWNERS = 1
        task = self.store.begin('existing', 'Old work')['task']
        self.store.checkpoint('existing', task['id'], status='completed')
        with self.assertRaisesRegex(ValueError, 'owner capacity'):
            self.store.begin('new owner', 'Cannot replace historical owner')
        self.assertIsNone(self.store.focus('new owner'))
        self.assertEqual(self.store.get('existing', task['id'])['status'], 'completed')
        self.assertEqual(self.store.begin('existing', 'New work')['reason'], 'new')

    def test_all_unfinished_statuses_count_and_terminal_metadata_updates_remain_allowed(self):
        archived = self.store.begin('owner', 'Archived')['task']
        self.store.checkpoint('owner', archived['id'], status='completed')
        first = self.store.begin('owner', 'Paused')['task']
        self.store.checkpoint('owner', first['id'], status='paused')
        second = self.store.begin('owner', 'Blocked')['task']
        self.store.checkpoint('owner', second['id'], status='blocked')
        for status in ('active', 'paused', 'blocked'):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, 'capacity'):
                    self.store.checkpoint('owner', archived['id'], status=status, summary='Must roll back')
                self.assertEqual(self.store.get('owner', archived['id'])['summary'], '')
        self.store.checkpoint('owner', archived['id'], summary='Metadata only')
        self.store.checkpoint('owner', archived['id'], status='cancelled')
        with self.assertRaisesRegex(ValueError, 'capacity'):
            self.store.begin('owner', 'No slot')
        self.assertEqual(self.store.get('owner', first['id'])['status'], 'paused')
        self.assertEqual(self.store.get('owner', second['id'])['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
