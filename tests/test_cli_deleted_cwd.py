"""Chat must survive a deleted/current-directory-gone shell."""
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
from zeline import cli  # noqa: E402


class DeletedWorkingDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.multiple(
            cli.config, GATEWAY_SETUP_COMPLETE=True, SETUP_COMPLETE=True,
            PROVIDER={"model_verified": True}, API_KEY="not-a-real-secret",
        ))
        self.stack.enter_context(mock.patch.object(cli, "_provider_display", return_value="Demo"))
        store = self.stack.enter_context(mock.patch.object(cli, "SessionStore")).return_value
        self.store = store
        reader = mock.MagicMock()
        reader.read.side_effect = ["exit"]
        reader.hint = "Enter to send"
        self.ui = mock.MagicMock()
        self.stack.enter_context(mock.patch.dict(sys.modules, {
            "zeline.cli_input": types.SimpleNamespace(ChatInput=mock.Mock(return_value=reader)),
            "zeline.cli_ui": types.SimpleNamespace(ChatUI=mock.Mock(return_value=self.ui)),
        }))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def tearDown(self):
        self.stack.close()

    def test_missing_cwd_does_not_crash_the_chat_screen(self):
        # Reproduces the reported traceback: getcwd() raises FileNotFoundError.
        with mock.patch.object(cli.Path, "cwd", side_effect=FileNotFoundError(2, "No such file")):
            self.assertEqual(cli.cmd_chat(), 0)
        workspace = self.ui.welcome.call_args.kwargs["workspace"]
        self.assertEqual(workspace, "(directory unavailable)")


if __name__ == "__main__":
    unittest.main()
