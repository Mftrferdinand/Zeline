"""Repeated shell activity keeps one counted heading and the latest command."""
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

    def test_commands_share_one_counted_heading_with_latest_detail(self):
        for command in ["df -h", "du -sh /home/.[a-z]*", "du -sh /storage/*"]:
            self.add_command(command)
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 1)
        self.assertEqual(rendered.count("<pre>"), 1)
        self.assertIn("💻 Executing command… (x3)", rendered)
        self.assertIn("du -sh /storage/*", rendered)
        self.assertEqual(len(self.live.lines), 1)

    def test_interleaved_activity_keeps_first_group_position(self):
        self.add_command("df -h")
        self.live.add("📖 Reading file: settings.py")
        self.add_command("du -sh /home")
        self.add_command("du -sh /storage")
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 1)
        self.assertIn("(x3)", rendered)
        self.assertIn("settings.py", rendered)
        self.assertLess(rendered.index("Executing command"), rendered.index("settings.py"))

    def test_finalize_and_detach_keep_counts_and_latest_detail(self):
        for finish in ("finalize", "detach"):
            with self.subTest(finish=finish):
                self.live = self.telegram._LiveStatus("fixture", 1)
                self.add_command("df -h")
                self.add_command("du -sh /storage")
                getattr(self.live, finish)()
                edits = [call.kwargs["text"] for call in self.api.call_args_list
                         if call.args[1] == "editMessageText"]
                self.assertIn("💻 Executing command… (x2)", edits[-1])
                self.assertIn("du -sh /storage", edits[-1])

    def test_narration_boundary_starts_fresh_heading(self):
        self.add_command("df -h")
        self.live.detach()
        self.add_command("du -sh /home")
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 1)
        self.assertIn("du -sh /home", rendered)
        self.assertNotIn("df -h", rendered)
        self.assertNotIn("(x", rendered)

    def test_small_window_counts_calls_instead_of_dropping_rows(self):
        self.live.max_lines = 2
        for command in ("df -h", "du -sh /home", "du -sh /storage"):
            self.add_command(command)
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 1)
        self.assertIn("(x3)", rendered)
        self.assertIn("du -sh /storage", rendered)

    def test_latest_command_html_stays_escaped_and_background_label_is_distinct(self):
        self.add_command("printf 'another'")
        self.add_command("printf '<hello> & goodbye'")
        background = self.telegram._tool_progress_text("run_shell", {
            "command": "python server.py", "background": True})
        self.live.add(background)
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 1)
        self.assertIn("&lt;hello&gt; &amp; goodbye", rendered)
        self.assertNotIn("<hello>", rendered)
        self.assertIn(background, rendered)
