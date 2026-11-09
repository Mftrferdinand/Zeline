"""File-reading progress separates model rounds, not every read."""
import unittest
from unittest import mock

from zeline.gateways import telegram


class ReadFileSpacingTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(telegram, "_api_call", return_value={
            "ok": True, "result": {"message_id": 10},
        })
        self.api = patcher.start()
        self.addCleanup(patcher.stop)
        self.live = telegram._LiveStatus("fixture", 1)

    def read(self, name):
        self.live.add(telegram._tool_progress_text("read_file", {"path": name,
                                                                  "offset": 1, "limit": 40}))

    def test_read_file_name_uses_normal_text_not_monospace(self):
        rendered = telegram._tool_progress_text("read_file", {
            "path": "somewhere/NOTES<draft>&.md", "offset": 1, "limit": 60,
        })
        self.assertEqual(rendered, "📖 Reading file: NOTES&lt;draft&gt;&amp;.md L1-60")
        self.assertNotIn("<code>", rendered)
        self.assertEqual(telegram._tool_progress_text("read_file", {
            "path": "somewhere/PIPELINE.md",
        }), "📖 Reading file: PIPELINE.md")

    def test_same_round_stays_compact_next_round_gets_two_empty_lines(self):
        self.live.set_iteration(1, 20)
        self.read("NOTES.md")
        self.read("PIPELINE.md")
        self.live.set_iteration(2, 20)
        self.read("TickLinkedListPopInvariants.t.sol")
        first, second, third = self.live.lines
        expected = first + "\n" + second + "\n\n\n" + third
        self.assertEqual(self.live._render(), expected)
        self.assertEqual(len(self.live.lines), 3)

    def test_live_final_and_detach_share_spacing_and_reset_at_narration(self):
        for finish in ("finalize", "detach"):
            with self.subTest(finish=finish):
                self.live = telegram._LiveStatus("fixture", 1)
                self.live.set_iteration(1, 20)
                self.read("NOTES.md")
                self.live.set_iteration(2, 20)
                self.read("PIPELINE.md")
                expected = self.live.lines[0] + "\n\n\n" + self.live.lines[1]
                self.assertEqual(self.live._render(), expected)
                getattr(self.live, finish)()
                edits = [c.kwargs["text"] for c in self.api.call_args_list
                         if c.args[1] == "editMessageText"]
                self.assertEqual(edits[-1], expected)
                if finish == "detach":
                    self.live.set_iteration(3, 20)
                    self.read("ANOTHER.md")
                    self.assertEqual(self.live._render(), self.live.lines[0])

    def test_window_and_other_activities_do_not_get_stray_blank_lines(self):
        self.live.max_lines = 2
        self.live.set_iteration(1, 20)
        self.read("OLD.md")
        self.live.set_iteration(2, 20)
        self.read("NOTES.md")
        self.read("PIPELINE.md")
        self.assertEqual(self.live._render(), "\n".join(self.live.lines[-2:]))
        self.live.add("📝 Writing <code>OUT.md</code>")
        self.live.set_iteration(3, 20)
        self.read("NEXT.md")
        self.assertEqual(self.live._render(), "\n".join(self.live.lines[-2:]))


if __name__ == "__main__":
    unittest.main()
