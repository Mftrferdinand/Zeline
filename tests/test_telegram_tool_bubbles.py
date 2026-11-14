"""Tool activity shares one feed; only exact repeated runs get a counter."""
import importlib
import unittest
from unittest import mock
import test_agent as fixtures


class ToolBubbleTests(unittest.TestCase):
    setUp = fixtures.AgentLoopTests.setUp
    tearDown = fixtures.AgentLoopTests.tearDown

    def scenario(self, calls):
        telegram = importlib.import_module('zeline.gateways.telegram')
        sent, edits = [], []
        def api(_api, method, **kwargs):
            if method == 'sendMessage':
                sent.append(kwargs['text'])
                return {'ok': True, 'result': {'message_id': len(sent)}}
            if method == 'editMessageText':
                edits.append(kwargs)
            return {'ok': True, 'result': True}
        live = telegram._LiveStatus('offline', 1)
        live._terminal_retries = False
        with mock.patch.object(telegram, '_api_call', side_effect=api):
            for name, args in calls:
                live.add(telegram._tool_progress_text(name, args),
                         invocation_key=telegram._tool_progress_key(name, args))
            live.tick()
            live.tick()
        return telegram, live, sent, edits

    def test_different_tools_and_commands_share_one_feed(self):
        _, live, sent, _ = self.scenario([
            ('run_shell', {'command': 'pwd'}),
            ('run_shell', {'command': 'node --version'}),
            ('read_file', {'path': 'main.py'}),
            ('execute_code', {'code': 'print(1)'}),
        ])
        self.assertEqual(len(sent), 1)
        self.assertEqual(live.message_id, 1)
        feed = live._feed_text()
        for label in ['Executing command', 'pwd', 'node --version', 'Reading', 'main.py', 'Executing code']:
            self.assertIn(label, feed)
        self.assertNotIn('(x2)', feed)

    def test_identical_runs_count_but_ticks_do_not(self):
        _, live, sent, _ = self.scenario([('run_shell', {'command': 'pwd'})] * 3)
        self.assertEqual(len(sent), 1)
        self.assertIn('(x3)', live._feed_text())
        self.assertNotIn('(3x)', live._feed_text())
        self.assertEqual(len(live.lines), 1)

    def test_different_scripts_are_visible_without_false_repeat_count(self):
        _, live, sent, _ = self.scenario([
            ('execute_code', {'code': 'print(1)'}),
            ('execute_code', {'code': 'print(2)'}),
        ])
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(live.lines), 2)
        self.assertNotIn('(x2)', live._feed_text())
        self.assertIn('print(1)', live._feed_text())
        self.assertIn('print(2)', live._feed_text())

    def test_truncated_identical_labels_do_not_count_different_runs(self):
        prefix = 'printf ' + ('long-prefix ' * 50)
        _, live, sent, _ = self.scenario([
            ('run_shell', {'command': prefix + 'ONE'}),
            ('run_shell', {'command': prefix + 'TWO'}),
        ])
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(live.lines), 2)
        self.assertNotIn('(x2)', live._feed_text())

    def test_terminal_restores_standard_shell_code_card(self):
        telegram = importlib.import_module('zeline.gateways.telegram')
        text = telegram._tool_progress_text('run_shell', {'command': 'pwd'})
        self.assertIn('Executing command', text)
        self.assertIn('<pre><code class="language-shell">pwd</code></pre>', text)
        self.assertIn('language-bash', telegram._terminal_progress('bash -lc pwd'))

    def test_return_to_command_after_other_tool_is_not_a_repeat(self):
        _, live, sent, _ = self.scenario([
            ('run_shell', {'command': 'pwd'}),
            ('read_file', {'path': 'main.py'}),
            ('run_shell', {'command': 'pwd'}),
        ])
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(live.lines), 3)
        self.assertNotIn('(x2)', live._feed_text())

    def test_real_gateway_keeps_tools_in_one_feed_and_final_separate(self):
        telegram = importlib.import_module('zeline.gateways.telegram')
        sent = []
        class Session:
            def send(self, **callbacks):
                for name, args in [('run_shell', {'command': 'pwd'}), ('read_file', {'path': 'main.py'})]:
                    callbacks['on_tool'](name, args)
                    callbacks['on_tool_result'](name, args, 'ok')
                return 'Finished.'
        def api(_api, method, **kwargs):
            if method == 'sendMessage':
                sent.append(kwargs['text'])
                return {'ok': True, 'result': {'message_id': len(sent)}}
            return {'ok': True, 'result': True}
        with mock.patch.object(telegram, '_api_call', side_effect=api), mock.patch.object(telegram, '_start_reflection'):
            telegram._send_agent_reply('offline', Session(), chat_id=1,
                identity='bubble:fixture', text='fixture', tool_profile='safe')
        self.assertEqual(len(sent), 2)
        self.assertIn('Executing command', sent[0])
        self.assertEqual(sent[-1], 'Finished.')
