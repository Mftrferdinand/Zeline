"""Distinct shell invocations retain individual cards in one feed."""
import importlib
from unittest import mock

from tests.test_ask_user import AskUserBase


class CommandGroupingTests(AskUserBase):
    def setUp(self):
        super().setUp()
        self.telegram = importlib.import_module("zeline.gateways.telegram")
        self.api = mock.patch.object(self.telegram, "_api_call", return_value={
            "ok": True, "result": {"message_id": 10}}).start()
        self.addCleanup(mock.patch.stopall)
        self.live = self.telegram._LiveStatus("fixture", 1)

    def add_command(self, command):
        self.live.add(self.telegram._tool_progress_text("run_shell", {"command": command}))

    def test_consecutive_commands_keep_individual_cards_and_every_command(self):
        commands = ["df -h", "du -sh /home/.[a-z]*", "du -sh /storage/*"]
        for command in commands:
            self.add_command(command)
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 3)
        self.assertNotIn("(x", rendered)
        self.assertEqual(sum(c.args[1] == "sendMessage" for c in self.api.call_args_list), 1)
        self.assertEqual(rendered.count("<pre>"), 3)
        self.assertLess(rendered.index(commands[0]), rendered.index(commands[1]))
        self.assertLess(rendered.index(commands[1]), rendered.index(commands[2]))
        for command in commands:
            self.assertIn(command, rendered)
        self.assertEqual(len(self.live.lines), 3)

    def test_different_activity_starts_a_new_group(self):
        self.add_command("df -h")
        self.live.add("📖 Reading file: settings.py")
        self.add_command("du -sh /home")
        self.add_command("du -sh /storage")
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 3)
        self.assertLess(rendered.index("df -h"), rendered.index("settings.py"))
        self.assertLess(rendered.index("settings.py"), rendered.index("du -sh /home"))
        self.assertIn("du -sh /storage", rendered)

    def test_finalize_and_detach_keep_every_card(self):
        for finish in ("finalize", "detach"):
            with self.subTest(finish=finish):
                self.live = self.telegram._LiveStatus("fixture", 1)
                self.add_command("df -h")
                self.add_command("du -sh /storage")
                getattr(self.live, finish)()
                edits = [call.kwargs["text"] for call in self.api.call_args_list
                         if call.args[1] == "editMessageText"]
                self.assertEqual(edits[-1].count("💻 Executing command…"), 2)
                self.assertIn("df -h", edits[-1])
                self.assertIn("du -sh /storage", edits[-1])

    def test_narration_boundary_starts_fresh_heading(self):
        self.add_command("df -h")
        self.live.detach()  # Gateway does this before delivering narration.
        self.add_command("du -sh /home")
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 1)
        self.assertIn("du -sh /home", rendered)
        self.assertNotIn("df -h", rendered)

    def test_visible_window_keeps_first_and_latest_card(self):
        self.live.max_lines = 2
        for command in ("df -h", "du -sh /home", "du -sh /storage"):
            self.add_command(command)
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 2)
        self.assertIn("df -h", rendered)
        self.assertNotIn("du -sh /home", rendered)
        self.assertEqual(len(self.live.lines), 3)
        self.assertIn("du -sh /storage", rendered)

    def test_command_html_stays_escaped_and_background_label_is_unchanged(self):
        self.add_command("printf '<hello> & goodbye'")
        self.add_command("printf 'another'")
        background = self.telegram._tool_progress_text("run_shell", {
            "command": "python server.py", "background": True})
        self.live.add(background)
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 2)
        self.assertEqual(rendered.count("<pre>"), 3)
        self.assertIn("printf 'another'", rendered)
        self.assertIn("&lt;hello&gt; &amp; goodbye", rendered)
        self.assertNotIn("<hello>", rendered)
        self.assertIn(background, rendered)
