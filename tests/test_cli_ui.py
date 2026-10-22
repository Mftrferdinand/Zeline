"""Focused, offline tests for the blue chat renderer."""

from __future__ import annotations

import io
import re
import unittest

from zeline.cli_ui import ChatUI

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def render(**kwargs) -> str:
    stream = io.StringIO()
    ChatUI(stream=stream, width=100, color=True, unicode_ok=True, **kwargs)
    return stream.getvalue()


class ExitHintTests(unittest.TestCase):
    def test_startup_footer_explains_exit_at_all_widths(self):
        for width in (24, 32, 48, 80, 120):
            for color in (False, True):
                stream = io.StringIO()
                ui = ChatUI(stream=stream, width=width, color=color, unicode_ok=True)
                ui.footer()
                text = ANSI.sub("", stream.getvalue())
                self.assertIn("exit", text)
                self.assertIn("close chat", text)
                for line in text.splitlines():
                    self.assertLessEqual(len(line), width)


class WelcomeTests(unittest.TestCase):
    def test_welcome_shows_wordmark_and_session_dashboard_without_secrets(self):
        stream = io.StringIO()
        ui = ChatUI(stream=stream, width=100, color=True, unicode_ok=True)
        ui.welcome(
            version="0.4.1",
            agent="Zeline",
            model="demo-model",
            provider="Demo provider",
            workspace="/data/data/com.termux/files/home",
            tool_profile="full",
        )
        text = stream.getvalue()
        plain = ANSI.sub("", text)
        self.assertIn("Z", plain)
        self.assertIn("Zeline", plain)
        self.assertIn("demo-model", plain)
        self.assertIn("Workspace", plain)
        self.assertIn("files/home", plain)
        self.assertIn("\x1b[", text)  # blue accents are actually emitted


def _block_width(line: str) -> int:
    body = line.strip("│")
    return len(body.rstrip()) - len(body.lstrip())


class LayoutTests(unittest.TestCase):
    def test_every_box_edge_aligns_at_every_width(self):
        # The request: all boxes the same width, title centred between the
        # borders. A one-column drift is what made the layout look sloppy.
        for width in (48, 56, 64, 80, 92, 100, 120, 160):
            stream = io.StringIO()
            ui = ChatUI(stream=stream, width=width, color=True, unicode_ok=True)
            ui.welcome(version="0.4.1", agent="Zeline", model="Riv/model-name-long-enough",
                       provider="9Router", workspace="/data/data/com.termux/files/home",
                       tool_profile="workspace", tools=37, skills=424, memory=True,
                       status="ONLINE", clock="12:00", config_path="~/.zeline/config.json",
                       runtime_lines=["tool rounds/turn: 150"], session_lines=["endpoint: local"])
            plain = ANSI.sub("", stream.getvalue())
            widths = {len(line) for line in plain.splitlines() if line.strip()}
            self.assertEqual(widths, {width}, f"{width}: {sorted(widths)}")

    def test_logo_block_is_centred_between_the_borders(self):
        """The lockup survives every width without breaking the frame."""
        for width in (80, 110, 140):
            stream = io.StringIO()
            ui = ChatUI(stream=stream, width=width, color=True, unicode_ok=True)
            ui.welcome(version="0.4.1", agent="Zeline", model="m", provider="p",
                       workspace="/tmp", tool_profile="w")
            plain = ANSI.sub("", stream.getvalue())
            rows = [l for l in plain.splitlines() if "█" in l and l.startswith("│")]
            self.assertTrue(rows, width)
            for line in rows:
                self.assertEqual(len(line), width, line)
                # The mark starts after the border and never touches it.
                body = line.strip("│")
                self.assertGreater(len(body) - len(body.lstrip()), 0, line)


class WidthTests(unittest.TestCase):
    def test_no_line_ever_exceeds_the_terminal_width(self):
        # The real Termux bug: a frame or footer ran one column past the edge
        # and wrapped, which is what made the dashboard look scrambled.
        for width in (30, 40, 48, 64, 80, 110, 140):
            stream = io.StringIO()
            ui = ChatUI(stream=stream, width=width, color=True, unicode_ok=True)
            ui.welcome(version="0.4.1", agent="Zeline", model="m" * 60, provider="p",
                       workspace="/data/data/com.termux/files/home/zeline",
                       tool_profile="workspace", tools=37, skills=424, clock="07:55",
                       config_path="/data/data/com.termux/files/home/.zeline/config.json")
            ui.question("pertanyaan " * 6, ["satu", "dua"])
            ui.footer()
            for line in ANSI.sub("", stream.getvalue()).splitlines():
                self.assertLessEqual(len(line.rstrip()), width, f"{width}: {line!r}")


class ReplyTests(unittest.TestCase):
    def test_markdown_heading_and_code_block_are_rendered(self):
        ui = ChatUI(stream=io.StringIO(), width=100, color=True, unicode_ok=True)
        text = ui.render_reply("## Result\n\n```python\nprint('hi')\n```")
        plain = ANSI.sub("", text)
        self.assertIn("Result", plain)
        self.assertIn("print('hi')", plain)


class SafetyTests(unittest.TestCase):
    def test_control_sequences_are_neutralised(self):
        ui = ChatUI(stream=io.StringIO(), width=100, color=True, unicode_ok=True)
        hostile = "\x1b[2J wipe\x1b]0;title\x07 \r\x00 end"
        text = ui.render_reply(hostile)
        self.assertNotIn("\x1b[2J", text)
        self.assertNotIn("\x00", text)
        self.assertIn("wipe", text)


class FrameTests(unittest.TestCase):
    def test_wide_terminal_draws_framed_panels(self):
        stream = io.StringIO()
        ui = ChatUI(stream=stream, width=110, color=True, unicode_ok=True)
        ui.welcome(version="0.4.1", agent="Zeline", model="m", provider="p",
                   workspace="/tmp", tool_profile="full")
        plain = ANSI.sub("", stream.getvalue())
        self.assertIn("\u256d", plain)               # framed cards
        self.assertIn("what zeline can do", plain)  # banner rendered (title uses ASCII art)
        self.assertIn("what zeline can do", plain)   # left table
        self.assertIn("what it can reach", plain)    # right table
        # Every framed line must be the same visible width: ragged frames are
        # the classic "looks cheap" bug.
        boxed = [l for l in plain.splitlines() if l.startswith("\u2502")]
        self.assertTrue(boxed)
        for line in boxed:
            self.assertIn(line.rstrip()[-1], "\u2502\u256e\u256f\u2524", line)

    def test_phone_width_stacks_one_column_but_keeps_frames(self):
        # Termux is ~48 columns: frames are still fine, two columns are not.
        stream = io.StringIO()
        ui = ChatUI(stream=stream, width=48, color=True, unicode_ok=True)
        ui.welcome(version="0.4.1", agent="Zeline", model="m", provider="p",
                   workspace="/tmp", tool_profile="full")
        plain = ANSI.sub("", stream.getvalue())
        self.assertIn("\u256d", plain)
        # Stacked: both tables render one above the other at phone width.
        self.assertIn("what zeline can do", plain)
        self.assertIn("what it can reach", plain)
        for line in plain.splitlines():
            self.assertLessEqual(len(line.rstrip()), 48, line)

    def test_very_narrow_terminal_falls_back_to_rows(self):
        # Below MIN_FRAME there is no room for two borders AND content.
        stream = io.StringIO()
        ui = ChatUI(stream=stream, width=30, color=True, unicode_ok=True)
        ui.welcome(version="0.4.1", agent="Zeline", model="m", provider="p",
                   workspace="/tmp", tool_profile="full")
        self.assertNotIn("\u256d", stream.getvalue())

    def test_ascii_stream_uses_ascii_frames_not_box_drawing(self):
        stream = io.StringIO()
        ui = ChatUI(stream=stream, width=90, color=True, unicode_ok=False)
        ui.user("hi")
        text = stream.getvalue()
        self.assertIn("+", text)
        self.assertNotIn("\u256d", text)

    def test_long_answer_is_wrapped_inside_the_frame_not_truncated(self):
        stream = io.StringIO()
        ui = ChatUI(stream=stream, width=90, color=True, unicode_ok=True)
        body = "kata " * 200
        ui.reply(body)
        plain = ANSI.sub("", stream.getvalue())
        self.assertEqual(plain.count("kata"), 200)


if __name__ == "__main__":
    unittest.main()
