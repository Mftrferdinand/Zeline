"""Subprocess cleanup must never signal a shared or foreign process group."""
from __future__ import annotations

import importlib
from contextlib import ExitStack
from unittest import TestCase, mock


class ProcessGroupOwnershipTests(TestCase):
    def setUp(self):
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.tools = importlib.import_module("zeline.tools")
        self.process = mock.Mock(pid=4242)
        self.process.poll.return_value = None

        def reap(*, timeout):
            self.process.poll.return_value = 0
            return 0

        self.process.wait.side_effect = reap
        self.patches.enter_context(mock.patch.object(self.tools, "IS_WINDOWS", False))
        self.patches.enter_context(mock.patch.object(self.tools.signal, "SIGKILL", 9, create=True))
        self.getpgid = self.patches.enter_context(mock.patch.object(
            self.tools.os, "getpgid", return_value=31415, create=True,
        ))
        self.killpg = self.patches.enter_context(mock.patch.object(
            self.tools.os, "killpg", create=True,
        ))

    def test_shared_process_group_uses_only_the_child_fallback(self):
        self.tools._terminate_group(self.process)

        self.killpg.assert_not_called()
        self.process.terminate.assert_called_once_with()
        self.process.wait.assert_called_once_with(timeout=3)
        self.process.kill.assert_not_called()

    def test_shared_group_timeout_escalates_only_the_child(self):
        self.process.wait.side_effect = self.tools.subprocess.TimeoutExpired("fixture", 3)

        self.tools._terminate_group(self.process)

        self.killpg.assert_not_called()
        self.process.terminate.assert_called_once_with()
        self.process.kill.assert_called_once_with()

    def test_owned_group_is_terminated_and_force_killed(self):
        self.getpgid.return_value = self.process.pid

        self.tools._terminate_group(self.process)

        self.assertEqual(self.killpg.call_args_list, [
            mock.call(self.process.pid, self.tools.signal.SIGTERM),
            mock.call(self.process.pid, self.tools.signal.SIGKILL),
        ])
        self.getpgid.assert_called_once_with(self.process.pid)
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()

    def test_owned_descendants_are_cleaned_after_the_shell_exits(self):
        self.getpgid.return_value = self.process.pid

        def reap_shell(*, timeout):
            self.process.poll.return_value = 0
            self.getpgid.side_effect = ProcessLookupError("shell reaped")
            return 0

        self.process.wait.side_effect = reap_shell

        self.tools._terminate_group(self.process)

        self.getpgid.assert_called_once_with(self.process.pid)
        self.assertEqual(self.killpg.call_args_list, [
            mock.call(self.process.pid, self.tools.signal.SIGTERM),
            mock.call(self.process.pid, self.tools.signal.SIGKILL),
        ])

    def test_group_lookup_failure_uses_only_the_child(self):
        for error in (ProcessLookupError, PermissionError, OSError):
            with self.subTest(error=error.__name__):
                self.process.reset_mock()
                self.process.poll.return_value = None
                self.getpgid.side_effect = error("lookup unavailable")
                self.killpg.reset_mock()

                self.tools._terminate_group(self.process)

                self.killpg.assert_not_called()
                self.process.terminate.assert_called_once_with()
                self.process.kill.assert_not_called()

    def test_failed_owned_group_signal_falls_back_to_child(self):
        self.getpgid.return_value = self.process.pid
        self.killpg.side_effect = ProcessLookupError("group unavailable")

        self.tools._terminate_group(self.process)

        self.process.terminate.assert_called_once_with()
        self.process.wait.assert_called_once_with(timeout=3)
        for call in self.killpg.call_args_list:
            self.assertEqual(call.args[0], self.process.pid)

    def test_windows_keeps_the_existing_tree_termination(self):
        with mock.patch.object(self.tools, "IS_WINDOWS", True), mock.patch.object(
            self.tools._winproc, "terminate_tree", return_value=True,
        ) as terminate_tree:
            self.tools._terminate_group(self.process)

        terminate_tree.assert_called_once_with(self.process.pid)
        self.getpgid.assert_not_called()
        self.killpg.assert_not_called()
        self.process.terminate.assert_not_called()
        self.process.wait.assert_called_once_with(timeout=5)

    def test_windows_failed_tree_termination_falls_back_to_child(self):
        with mock.patch.object(self.tools, "IS_WINDOWS", True), mock.patch.object(
            self.tools._winproc, "terminate_tree", return_value=False,
        ) as terminate_tree:
            self.tools._terminate_group(self.process)

        terminate_tree.assert_called_once_with(self.process.pid)
        self.getpgid.assert_not_called()
        self.killpg.assert_not_called()
        self.process.kill.assert_called_once_with()
