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
        self.assertEqual(len(self.store.get('owner', a['id'])['tool_events']), 8)
        for index in range(40):
            self.store.checkpoint('owner', a['id'], tool_event={'id': 'later' + str(index),
                'tool': 'read_file', 'status': 'unknown', 'result': 'x' * 5000})
        events = self.store.get('owner', a['id'])['tool_events']
        self.assertEqual(len(events), 24)
        self.assertTrue(all(len(event['result']) <= 1000 for event in events))
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)

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
