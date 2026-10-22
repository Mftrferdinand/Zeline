"""Reference banner retains exact glyph positions and continuous colour ramp."""
import io
import re
import unittest
from zeline import branding
from zeline.cli_ui import ChatUI

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

class IdentityLayoutTests(unittest.TestCase):
    def test_reference_marks_are_centred_without_changing_glyph_origins(self):
        for width in (80, 100, 120, 160):
            stream = io.StringIO()
            ui = ChatUI(stream=stream, width=width, color=True, unicode_ok=True)
            ui.welcome(version="0.4.1", agent="Zeline", model="m", provider="p",
                       workspace="/workspace", tool_profile="workspace")
            raw = stream.getvalue()
            plain = ANSI.sub("", raw)
            lines = plain.splitlines()
            actual = lines[1:13]
            expected = []
            for mark in (branding.WORDMARK_FULL, branding.WORDMARK_AGENTIC):
                w = max(map(len, mark))
                left = (width - 2 - w) // 2
                for row in mark:
                    expected.append("│" + " " * left + row.ljust(w)
                                    + " " * (width - 2 - w - left) + "│")
            self.assertEqual(actual, expected, width)
            self.assertEqual(plain.count("╭"), 3, width)
            for line in lines:
                if line:
                    self.assertEqual(len(line), width, (width, line))
            tones = []
            for line in raw.splitlines()[1:13]:
                codes = re.findall(r"\x1b\[(38;5;[0-9]+)m", line)
                tones.append(codes[1])
            expected_tones = [branding._GRADIENT[i * 5 // 11] for i in range(12)]
            self.assertEqual(tones, expected_tones)

    def test_fact_card_wraps_instead_of_truncating_paths(self):
        stream = io.StringIO()
        ui = ChatUI(stream=stream, width=48, color=False, unicode_ok=True)
        value = "/workspace/projects/really-long-project-name/src/settings.json"
        ui._table("Workspace", [("Location", value)])
        pieces = [line.split("│")[2].strip() for line in stream.getvalue().splitlines()
                  if line.startswith("│")]
        self.assertEqual("".join(pieces), value)
