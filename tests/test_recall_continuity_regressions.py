"""Offline regressions for automatic recall and pinned worker controls."""
import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


class RecallContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, HOME=self.tmp.name, ZELINE_HOME=self.tmp.name,
                         ZELINE_API_KEY='fixture', ZELINE_MODEL='fixture',
                         ZELINE_BASE_URL='http://127.0.0.1:1/v1')
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.module = importlib.import_module('zeline.agent')
        self.agent = self.module.Zeline(identity='fixture', workspace=self.tmp.name, tool_profile='safe')
        self.contexts = []
        def turn(**kwargs):
            self.contexts.append(self.agent._turn_memory_context)
            self.agent.messages.append({'role': 'assistant', 'content': 'fixture result'})
            return 'fixture result'
        runner = patch.object(self.agent, '_run_turn', side_effect=turn)
        runner.start()
        self.addCleanup(runner.stop)

    def test_short_new_topic_is_not_cached_followup_and_greeting_skips_recall(self):
        memory = self.agent.executor.memory
        with patch.object(memory, 'recall', side_effect=lambda text: {'context': text, 'records': []}) as recall:
            self.agent.send('Inspect Amber project')
            self.agent.send('Blue pricing')
            self.agent.send('halo')
        self.assertEqual([call.args[0] for call in recall.call_args_list], ['Inspect Amber project', 'Blue pricing'])
        self.assertEqual(self.contexts, ['Inspect Amber project', 'Blue pricing', ''])

    def test_topical_pin_survives_and_is_reused_for_explicit_continuation(self):
        self.agent.core_memory_in_prompt = True
        memory = self.agent.executor.memory
        with patch.object(memory, 'recall', return_value={'context': 'BASE', 'records': []}) as recall, \
                patch.object(memory, 'prompt_block', return_value='TOPICAL_PIN'):
            self.agent.send('Inspect Amber project')
            self.agent.send('lanjutin')
        self.assertIn('TOPICAL_PIN', self.contexts[0])
        self.assertIn('TOPICAL_PIN', self.contexts[1])
        self.assertEqual(recall.call_count, 1)

    def test_revision_race_does_not_mark_old_recall_as_fresh(self):
        memory = self.agent.executor.memory
        with patch.object(self.agent, '_memory_read_signature', side_effect=['old', 'new', 'new', 'new']), \
                patch.object(memory, 'recall', side_effect=[{'context': 'OLD', 'records': []}, {'context': 'NEW', 'records': []}]) as recall:
            self.agent.send('Inspect Amber project')
            self.agent.send('lanjut')
        self.assertEqual(recall.call_count, 2)
        self.assertEqual(self.contexts[-1], 'NEW')

    def test_capture_context_keeps_selected_task_and_turn_cache(self):
        executor = self.agent.executor
        executor._task_store = object()
        executor._task_id = 'amber'
        executor._task_resolution_required = True
        executor._turn_history_cache = {}
        captured = executor.capture_context()
        self.assertIs(captured.get('_task_store'), executor._task_store)
        self.assertEqual(captured.get('_task_id'), 'amber')
        self.assertTrue(captured.get('_task_resolution_required'))
        self.assertIs(captured.get('_history_recall_cache'), executor._turn_history_cache)

    def test_missing_selected_checkpoint_does_not_fall_back_to_archive(self):
        executor = self.agent.executor
        tasks = importlib.import_module('zeline.task_context').TaskContextStore()
        executor._task_store = tasks
        executor._task_id = 'missing'
        result = executor.run('recall_history', {})
        self.assertIn('unresolved', result.lower())
        self.assertIn('ask_user', result)


if __name__ == '__main__':
    unittest.main()
