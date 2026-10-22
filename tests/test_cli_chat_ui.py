"""Chat UI integration contracts without a real provider or operator state."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from zeline import cli


class ChatIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.multiple(
            cli.config, GATEWAY_SETUP_COMPLETE=True, SETUP_COMPLETE=True,
            PROVIDER={'model_verified': True}, API_KEY='not-a-real-secret',
        ))
        self.stack.enter_context(mock.patch.object(cli, '_provider_display', return_value='Demo provider'))
        self.store = self.stack.enter_context(mock.patch.object(cli, 'SessionStore')).return_value
        self.store.send.return_value = 'A real response'
        self.reflection = self.stack.enter_context(mock.patch.object(cli, '_run_reflection'))
        self.ui = mock.MagicMock()
        self.reader = mock.MagicMock()
        self.reader.hint = 'Enter to send'
        self.stack.enter_context(mock.patch.dict(sys.modules, {
            'zeline.cli_ui': types.SimpleNamespace(ChatUI=mock.Mock(return_value=self.ui)),
            'zeline.cli_input': types.SimpleNamespace(ChatInput=mock.Mock(return_value=self.reader)),
        }))
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def script(self, *messages):
        self.reader.read.side_effect = list(messages)
        self.stack.enter_context(mock.patch('builtins.input', side_effect=list(messages)))

    def test_help_is_local_and_does_not_spend_a_model_turn(self):
        self.script('/help', 'exit')
        self.assertEqual(cli.cmd_chat(), 0)
        self.store.send.assert_not_called()
        self.ui.help.assert_called_once()
        # The banner + dashboard are part of the same premium screen: exactly
        # one welcome, carrying the live model and workspace.
        self.ui.welcome.assert_called_once()
        kwargs = self.ui.welcome.call_args.kwargs
        self.assertEqual(kwargs['model'], cli.config.MODEL)
        self.assertIn('provider', kwargs)
        self.assertNotIn('key', ' '.join(kwargs).lower())

    def test_a_message_is_sent_once_and_rendered_as_a_reply(self):
        self.script('hello there', 'exit')
        self.assertEqual(cli.cmd_chat(), 0)
        self.store.send.assert_called_once()
        self.assertEqual(self.store.send.call_args.kwargs['text'], 'hello there')
        # Submitted input is already visible in terminal scrollback.
        self.ui.user.assert_not_called()
        self.ui.reply.assert_called_once_with('A real response')

    def test_tool_activity_never_renders_argument_values(self):
        self.script('run it', 'exit')
        self.store.send.side_effect = None

        def send(**kwargs):
            kwargs['on_tool']('run_shell', {'command': 'export TOKEN=sup3rsecret'})
            return 'done'

        self.store.send.side_effect = send
        self.assertEqual(cli.cmd_chat(), 0)
        self.ui.tool.assert_called_once_with('run_shell')
        for call in self.ui.tool.call_args.args:
            self.assertNotIn('sup3rsecret', str(call))

    def test_ctrl_c_during_a_turn_stops_it_and_keeps_the_repl_alive(self):
        self.reader.read.side_effect = ['slow task', 'exit']
        self.store.send.side_effect = KeyboardInterrupt
        self.assertEqual(cli.cmd_chat(), 0)
        self.store.stop.assert_called_once_with('cli:local')
        self.ui.notice.assert_any_call(
            "Stopped. Progress so far is saved; send 'continue' to resume.", kind='warn'
        )


if __name__ == '__main__':
    unittest.main()
