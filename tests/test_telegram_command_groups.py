"""Distinct commands retain individual HTML cards in one progress feed."""
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

    def add_command(self, command, **kwargs):
        args = {"command": command, **kwargs}
        self.live.add(self.telegram._tool_progress_text("run_shell", args),
                      invocation_key=self.telegram._tool_progress_key("run_shell", args))

    def test_distinct_commands_keep_every_card_in_one_message(self):
        commands = ["df -h", "du -sh /home/.[a-z]*", "du -sh /storage/*"]
        for command in commands:
            self.add_command(command)
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 3)
        self.assertEqual(rendered.count("<pre>"), 3)
        self.assertNotIn("(x", rendered)
        self.assertEqual(len(self.live.lines), 3)
        for command in commands:
            self.assertIn(command, rendered)
        self.assertLess(rendered.index(commands[0]), rendered.index(commands[1]))
        self.assertLess(rendered.index(commands[1]), rendered.index(commands[2]))
        self.assertEqual(sum(c.args[1] == "sendMessage" for c in self.api.call_args_list), 1)
        edits = [c for c in self.api.call_args_list if c.args[1] == "editMessageText"]
        self.assertEqual(edits[-1].kwargs["text"], rendered)
        self.assertTrue(all(c.kwargs["message_id"] == 10 and c.kwargs["parse_mode"] == "HTML" for c in edits))

    def test_interleaved_activity_preserves_chronological_cards(self):
        self.add_command("df -h")
        self.live.add("📖 Reading settings.py")
        self.add_command("du -sh /home")
        self.add_command("du -sh /storage")
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 3)
        self.assertNotIn("(x", rendered)
        self.assertLess(rendered.index("df -h"), rendered.index("settings.py"))
        self.assertLess(rendered.index("settings.py"), rendered.index("du -sh /home"))
        self.assertLess(rendered.index("du -sh /home"), rendered.index("du -sh /storage"))

    def test_finalize_and_detach_keep_every_card(self):
        for finish in ("finalize", "detach"):
            with self.subTest(finish=finish):
                self.live = self.telegram._LiveStatus("fixture", 1)
                self.add_command("df -h")
                self.add_command("du -sh /storage")
                expected = self.live._feed_text()
                getattr(self.live, finish)()
                edits = [c.kwargs["text"] for c in self.api.call_args_list if c.args[1] == "editMessageText"]
                self.assertEqual(edits[-1], expected)
                self.assertEqual(edits[-1].count("<pre>"), 2)
                self.assertNotIn("(x", edits[-1])

    def test_narration_boundary_starts_fresh_feed(self):
        self.add_command("df -h")
        self.live.detach()
        self.add_command("du -sh /home")
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 1)
        self.assertIn("du -sh /home", rendered)
        self.assertNotIn("df -h", rendered)
        self.assertNotIn("(x", rendered)

    def test_small_window_keeps_first_and_latest_command_cards(self):
        self.live.max_lines = 2
        for command in ("df -h", "du -sh /home", "du -sh /storage"):
            self.add_command(command)
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 2)
        self.assertIn("df -h", rendered)
        self.assertNotIn("du -sh /home", rendered)
        self.assertIn("du -sh /storage", rendered)
        self.assertNotIn("(x", rendered)
        self.assertEqual(len(self.live.lines), 3)

    def test_each_command_html_is_escaped_and_background_label_is_distinct(self):
        self.add_command("printf 'another'")
        self.add_command("printf '<hello> & goodbye'")
        self.add_command("python server.py", background=True)
        rendered = self.live._render()
        self.assertEqual(rendered.count("💻 Executing command…"), 2)
        self.assertEqual(rendered.count("<pre>"), 3)
        self.assertIn("printf 'another'", rendered)
        self.assertIn("&lt;hello&gt; &amp; goodbye", rendered)
        self.assertNotIn("<hello>", rendered)
        self.assertIn("🌟 Starting background process", rendered)
        self.assertIn("python server.py", rendered)

    def test_only_consecutive_exact_full_arguments_repeat(self):
        self.add_command("pwd", timeout=10)
        self.add_command("pwd", timeout=10)
        self.assertEqual(len(self.live.lines), 1)
        self.assertTrue(self.live._feed_text().endswith(" (x2)"))
        # Identical visible command, but different full args: retain a new row.
        self.add_command("pwd", timeout=20)
        self.add_command("ls", timeout=20)
        self.add_command("pwd", timeout=10)
        rendered = self.live._feed_text()
        self.assertEqual(len(self.live.lines), 4)
        self.assertEqual(rendered.count("<pre>"), 4)
        self.assertEqual(rendered.count("(x2)"), 1)
        self.assertEqual(rendered.count("pwd"), 3)
        self.assertIn("ls", rendered)
        self.assertNotIn("(x3)", rendered)

    def test_truncated_command_preview_does_not_merge_distinct_calls(self):
        prefix = "printf " + "a" * 600
        self.add_command(prefix + " first")
        self.add_command(prefix + " second")
        self.assertEqual(self.live.lines[0], self.live.lines[1])
        self.assertEqual(len(self.live.lines), 2)
        self.assertEqual(self.live._feed_text().count("<pre>"), 2)
        self.assertNotIn("(x", self.live._feed_text())

    def test_tool_name_is_part_of_exact_invocation_identity(self):
        args = {"command": "pwd"}
        line = self.telegram._tool_progress_text("run_shell", args)
        for name in ("run_shell", "terminal"):
            self.live.add(line, invocation_key=self.telegram._tool_progress_key(name, args))
        self.assertEqual(len(self.live.lines), 2)
        self.assertEqual(self.live._feed_text().count("<pre>"), 2)
        self.assertNotIn("(x", self.live._feed_text())

    def test_argument_dictionary_order_does_not_split_exact_repeats(self):
        for args in ({"command": "pwd", "timeout": 10}, {"timeout": 10, "command": "pwd"}):
            self.live.add(self.telegram._tool_progress_text("run_shell", args),
                          invocation_key=self.telegram._tool_progress_key("run_shell", args))
        rendered = self.live._feed_text()
        self.assertEqual(len(self.live.lines), 1)
        self.assertTrue(rendered.endswith("</pre> (x2)"))
        self.assertEqual(rendered.count("<pre>"), 1)
        self.assertEqual(self.live._repeat_counts, {0: 2})
