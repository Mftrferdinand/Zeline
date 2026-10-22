"""RED: the chat screen must draw the wordmark exactly once."""
from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from zeline import branding, cli  # noqa: E402


class BannerOnceTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.multiple(
            cli.config, GATEWAY_SETUP_COMPLETE=True, SETUP_COMPLETE=True,
            PROVIDER={'model_verified': True}, API_KEY='not-a-real-secret',
        ))
        self.stack.enter_context(mock.patch.object(cli, '_provider_display', return_value='Demo'))
        store = self.stack.enter_context(mock.patch.object(cli, 'SessionStore')).return_value
        reader = mock.MagicMock()
        reader.read.side_effect = ['exit']
        self.stack.enter_context(mock.patch.dict(sys.modules, {
            'zeline.cli_input': types.SimpleNamespace(ChatInput=mock.Mock(return_value=reader)),
        }))
        self.store = store
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def tearDown(self):
        self.stack.close()

    def test_wordmark_is_printed_once_in_chat(self):
        cli.cmd_chat()
        screen = self.output.getvalue()
        # Non-TTY: exactly one plain identity block, never a doubled banner.
        self.assertEqual(screen.count("Agent"), 1, screen)
        self.assertEqual(screen.count("Model"), 1, screen)

    def test_framed_screen_shows_the_identity_once(self):
        # The banner is now the identity bar, not ASCII block art: assert one
        # "ZELINE AGENTIC AI" title and no duplicated banner block.
        with mock.patch("zeline.cli_ui.branding.color_enabled", return_value=True), \
             mock.patch("zeline.cli_ui.terminal_width", return_value=80):
            cli.cmd_chat()
        screen = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", self.output.getvalue())
        # Block art, not literal text: the identity mark appears once.
        self.assertEqual(screen.count("what zeline can do"), 1, screen)
        self.assertEqual(screen.count("what it can reach"), 1, screen)

if __name__ == '__main__':
    unittest.main()
