"""Self-learning context must not promote earlier work over the current request."""
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


class SelfTaskBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'HOME': self.tmp.name, 'ZELINE_HOME': self.tmp.name,
            'ZELINE_API_KEY': 'fixture', 'ZELINE_MODEL': 'fixture',
            'ZELINE_BASE_URL': 'http://127.0.0.1:1/v1'})
        env.start()
        self.addCleanup(env.stop)
        for name in list(sys.modules):
            if name == 'zeline' or name.startswith('zeline.'):
                sys.modules.pop(name, None)
        self.module = importlib.import_module('zeline.agent')
        self.module.config.STREAM_RESPONSES = False
        self.module.config.FALLBACK_MODELS = ()
        self.module.config.FALLBACK_MODEL = ''
        self.lessons = importlib.import_module('zeline.lessons')
        self.identity = 'telegram:fixture'

    def seed_lesson(self, identity, path, error):
        store = self.lessons._get_store()
        args = {'path': path}
        store.record_failure(identity, 'read_file', args, error)
        self.assertTrue(store.record_fix_auto(identity, 'read_file', args))

    def send(self, agent, request):
        response = Mock(ok=True, status_code=200, encoding='utf-8')
        response.text = json.dumps({'choices': [{'message': {'content': 'fixture answer'}}]})
        with patch.object(self.module.requests, 'post', return_value=response) as post:
            agent.send(request)
        return post.call_args.kwargs['json']['messages']

    def agent(self):
        return self.module.Zeline(identity=self.identity, workspace=self.tmp.name, tool_profile='safe')

    def test_old_ui_lesson_is_not_in_current_nft_request_or_system_prompt(self):
        self.seed_lesson(self.identity, 'dashboard/palette.css', 'UI_VIOLET_MARKER palette failed')
        messages = self.send(self.agent(), 'Explain Agent Estates NFT mint requirements')
        current = next(item for item in reversed(messages) if item['role'] == 'user')
        system = next(item for item in messages if item['role'] == 'system')
        self.assertNotIn('UI_VIOLET_MARKER', system['content'])
        self.assertNotIn('UI_VIOLET_MARKER', current['content'])
        self.assertTrue(current['content'].startswith('Explain Agent Estates NFT'))


    def test_relevant_lesson_is_recalled_for_current_turn_not_promoted_to_system(self):
        self.seed_lesson(self.identity, 'nft/estates.json', 'ESTATES_MINT_LESSON nft parsing failed')
        messages = self.send(self.agent(), 'Explain Agent Estates NFT mint requirements')
        current = next(item for item in reversed(messages) if item['role'] == 'user')
        system = next(item for item in messages if item['role'] == 'system')
        self.assertTrue('ESTATES_MINT_LESSON' in current['content'], 'Relevant lesson was not recalled')
        self.assertFalse('ESTATES_MINT_LESSON' in system['content'], 'Lesson became a standing system instruction')


    def test_switch_and_reconstruct_keeps_old_task_but_not_its_lesson_in_new_focus(self):
        tasks = importlib.import_module('zeline.tasks')
        self.seed_lesson(self.identity, 'dashboard/palette.css', 'UI_VIOLET_MARKER palette failed')
        tasks.update(self.identity, 'Repair dashboard palette', 'in_progress')
        agent = self.agent()
        first = self.send(agent, 'Repair dashboard palette')
        self.assertTrue('UI_VIOLET_MARKER' in first[-1]['content'])
        self.send(agent, 'Explain Agent Estates NFT mint requirements')
        restored = self.agent()
        restored.load_history(agent.export_history())
        switched = self.send(restored, 'Continue Agent Estates NFT research')
        self.assertFalse('UI_VIOLET_MARKER' in switched[-1]['content'])
        self.assertTrue(any(item['task'] == 'Repair dashboard palette'
                            for item in tasks.open_items(self.identity)))
        resumed = self.send(restored, 'Resume dashboard palette work')
        self.assertTrue('UI_VIOLET_MARKER' in resumed[-1]['content'])

    def test_generic_continuation_does_not_recall_arbitrary_old_lessons(self):
        self.seed_lesson(self.identity, 'dashboard/palette.css', 'UI_VIOLET_MARKER file read failed')
        for query in ('lanjut', 'read the file', 'please fix task', 'gue kerjain itu dulu'):
            self.assertEqual(self.lessons.lessons_block(self.identity, query=query), '')

    def test_cross_identity_lesson_is_not_recalled(self):
        self.seed_lesson('telegram:other', 'nft/estates.json', 'OTHER_OWNER_MARKER nft failed')
        messages = self.send(self.agent(), 'Explain Agent Estates NFT mint requirements')
        self.assertFalse('OTHER_OWNER_MARKER' in messages[-1]['content'])

    def test_historical_task_prompts_do_not_instruct_unconditional_resumption(self):
        tasks = importlib.import_module('zeline.tasks')
        compaction = importlib.import_module('zeline.compaction')
        tasks.update(self.identity, 'Repair dashboard palette', 'in_progress')
        board = tasks.prompt_block(self.identity)
        digest = compaction._open_task_block(self.identity)
        self.assertFalse('Continue these rather than' in digest,
                         'Compaction still commands resuming historical work')
        self.assertFalse('this is what they mean' in board,
                         'Old task board still claims ownership of future continuation')
        for context in (board, digest):
            self.assertIn('current user request', context.lower())
            self.assertIn('Repair dashboard palette', context)


    def test_relevant_lesson_survives_recent_unrelated_lesson_noise(self):
        self.seed_lesson(self.identity, 'nft/estates.json', 'ESTATES_MINT_LESSON nft parsing failed')
        for index in range(10):
            self.seed_lesson(self.identity, f'dashboard/palette{index}.css',
                             f'UNRELATED_PALETTE_{index} rendering failed')
        block = self.lessons.lessons_block(self.identity, query='Agent Estates NFT mint')
        self.assertIn('ESTATES_MINT_LESSON', block)
        self.assertNotIn('UNRELATED_PALETTE_', block)

    def test_expired_lessons_stay_excluded(self):
        self.seed_lesson(self.identity, 'nft/estates.json', 'EXPIRED_MINT_LESSON nft parsing failed')
        with patch.object(self.lessons, 'LESSON_TTL', -1):
            self.assertEqual(self.lessons.lessons_block(self.identity, query='NFT mint'), '')


if __name__ == '__main__':
    unittest.main()
