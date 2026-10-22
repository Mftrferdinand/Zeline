"""Premium terminal rendering for the local Zeline chat REPL.

This is a *framed* interface, not a stream of plain lines: the banner, the
session dashboard, every turn, tool activity and question sit inside blue
panels with a consistent gutter, so the screen reads as one product.

The CLI still keeps native scrollback (no full-screen app): panels are printed
once and stay in the terminal's own buffer, so answers can be selected, copied
and piped.

Design rules
------------
* Zeline blue only: royal/steel blues for frames, headings and links. Text
  itself stays the terminal's foreground colour, so it works on dark and light
  themes. Red is reserved for real errors, amber for warnings.
* Framed layout: ``╭─ Zeline ───────────╮`` panels, a dashboard table, a turn
  rule between exchanges. Every block shares the same left gutter.
* Responsive: frames, tables and the wordmark adapt to the real terminal
  width. At 40 columns panels drop to a compact header, at 80+ the full frame
  is used. Answer content is never truncated — only decoration shrinks.
* Degrades safely: ``NO_COLOR``/``TERM=dumb``/non-TTY emits plain Markdown with
  zero ANSI, and a stream that cannot encode box drawing gets ASCII frames.
* Untrusted text (model output, tool names, paths, errors, question text) is
  stripped of terminal control sequences. Tool *arguments* are never printed —
  they routinely contain API keys and tokens.

Rich is imported lazily so the CLI still runs (plainly) if it is missing.
"""
from __future__ import annotations

import io
import os
import re
import sys
from typing import Any, Iterable, Sequence

from zeline import branding

_CTRL = re.compile(
    r"""
    \x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?   # OSC ... BEL/ST
  | \x1b\[[0-9;?]*[ -/]*[@-~]          # consume the entire CSI first
  | \x1b[@-_]                          # remaining two-byte escape codes
  | [\x00-\x08\x0b\x0c\x0e-\x1f\x7f]   # C0 controls except \t \n
    """,
    re.VERBOSE,
)

# Human, quiet labels for tool calls. Arguments are never shown.
_TOOL_NAMES = {
    "run_shell": "shell",
    "read_file": "read",
    "write_file": "write",
    "patch_file": "edit",
    "search_files": "search",
    "list_files": "list",
    "web_search": "web search",
    "web_fetch": "fetch",
    "http_request": "http",
    "browser": "browser",
    "generate_image": "image",
    "vision_analyze": "vision",
    "recall_history": "memory",
    "add_memory": "remember",
    "delegate_task": "delegate",
}

# One blue ramp. Rich's stock Markdown colours (magenta headings, cyan tables,
# green strings) make one product look like four, so every class is remapped.
_MARKDOWN_STYLES: dict[str, str] = {
    "markdown.h1": "bold #8ecdff",
    "markdown.h2": "bold #63b3ff",
    "markdown.h3": "bold #63b3ff",
    "markdown.h4": "bold #4a90e2",
    "markdown.h5": "bold #4a90e2",
    "markdown.h6": "bold #3b76c4",
    "markdown.h1.border": "#1f4e79",
    "markdown.block_quote": "#9dc6f0",
    "markdown.link": "underline #63b3ff",
    "markdown.link_url": "underline #4a90e2",
    "markdown.item.bullet": "bold #63b3ff",
    "markdown.item.number": "bold #63b3ff",
    # Tables: blue box + steel text, so a result table belongs to the panel
    # instead of Rich's default cyan grid on an unrelated colour.
    "markdown.table.header": "bold #ffffff",
    "markdown.table.border": "#2f6fa8",
    "markdown.table.row": "#cfe0f5",
    "markdown.table.first_column": "bold #9dc6f0",
    "markdown.code": "bold #9dc6f0",
    "markdown.code_block": "#cfe0f5",
    "markdown.emph": "italic #cfe0f5",
    "markdown.strong": "bold #ffffff",
    "pygments.text": "#dbeafe",
    "pygments.keyword": "bold #7cc4ff",
    "pygments.keyword.type": "#63b3ff",
    "pygments.name": "#dbeafe",
    "pygments.name.function": "bold #9dc6f0",
    "pygments.name.class": "bold #9dc6f0",
    "pygments.name.builtin": "#63b3ff",
    "pygments.name.decorator": "#63b3ff",
    "pygments.literal.string": "#a8c7e8",
    "pygments.literal.string.doc": "italic #8aa9c9",
    "pygments.literal.number": "#7cc4ff",
    "pygments.comment": "italic #5b7799",
    "pygments.operator": "#cfe0f5",
    "pygments.punctuation": "#8aa9c9",
    "pygments.generic.heading": "bold #7cc4ff",
    "pygments.generic.error": "#ff9aa2",
}

_ANSI_STRIP = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# The wordmark keeps the original banner gradient (white → light blue → deep
# blue). Frame/accent tones below are separate so panels stay consistent.
_WORDMARK_TONES = ("wm0", "wm1", "wm2", "wm3", "wm4", "wm5")
_ANSI = {
    "wm0": "38;5;231",
    "wm1": "38;5;153",
    "wm2": "38;5;75",
    "wm3": "38;5;39",
    "wm4": "38;5;33",
    "wm5": "38;5;26",
    "frame": "38;5;27",
    "bright": "38;5;39",
    "light": "38;5;117",
    "muted": "38;5;244",
    "warn": "38;5;214",
    "error": "38;5;203",
}


def sanitize(text: Any) -> str:
    """Remove terminal control sequences from untrusted text."""
    cleaned = _CTRL.sub("", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    return cleaned.replace("\x00", "")


def _stream_supports_unicode(stream: Any) -> bool | None:
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return None
    try:
        "\u2500".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def terminal_width(stream=None, default=80):
    """Real terminal width, falling back to the stream's view.

    ``os.get_terminal_size()`` looks at the *process* stdout; when the UI writes
    to another stream (tests, previews, a PTY wrapper) it can report a stale or
    tiny width, which silently collapsed the framed layout into the compact
    one. The stream's own size wins when it has one.
    """
    stream = stream or sys.stdout
    for candidate in (getattr(stream, "fileno", None), None):
        try:
            fd = candidate() if callable(candidate) else None
            size = os.get_terminal_size(fd) if fd is not None else os.get_terminal_size()
            if size.columns > 0:
                return max(20, size.columns)
        except (OSError, ValueError, AttributeError):
            continue
    try:
        columns = int(os.environ.get("COLUMNS", ""))
        if columns > 0:
            return max(20, columns)
    except ValueError:
        pass
    return default


class ChatUI:
    """Panelled, blue terminal UI for Zeline chat.

    Built around one outer panel whose title is the
    product + version, a logo/model block on the left and live capability
    columns on the right, followed by a prompt. Below 92 columns the columns
    stack so a phone screen stays readable.
    """

    STACK_BELOW = 92
    MIN_FRAME = 44

    def __init__(self, *, stream=None, width=None, color=None, unicode_ok=None):
        self.stream = stream if stream is not None else sys.stdout
        self.width = width if width is not None else terminal_width(self.stream)
        self.color = branding.color_enabled(self.stream) if color is None else bool(color)
        if unicode_ok is None:
            capable = _stream_supports_unicode(self.stream)
            unicode_ok = branding.supports_unicode(self.stream) if capable is None else capable
        self.unicode_ok = bool(unicode_ok)
        self._activity = []

    # ------------------------------------------------------------- plumbing
    def _write(self, text=""):
        self.stream.write(text + "\n")

    def ok(self):
        """Frames need colour and room for two borders plus content."""
        return self.color and self.width >= self.MIN_FRAME

    def _g(self):
        if self.unicode_ok:
            return {"tl": "\u256d", "tr": "\u256e", "bl": "\u2570", "br": "\u256f",
                    "h": "\u2500", "v": "\u2502", "lt": "\u251c", "rt": "\u2524",
                    "dot": "\u2022", "chev": "\u276f", "bullet": "\u2022"}
        return {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
                "lt": "+", "rt": "+", "dot": "*", "chev": ">", "bullet": "*"}

    def _paint(self, text, tone="bright"):
        if not self.color:
            return text
        return f"\033[{_ANSI.get(tone, _ANSI['bright'])}m{text}\033[0m"

    def _w(self):
        return max(24, self.width)

    # ------------------------------------------------------------- panel API
    def _panel_title(self, title, tone="frame"):
        g = self._g()
        width = self._w()
        label = f" {title} " if title else ""
        fill = max(0, width - 2 - _vlen(label))
        self._write(f"{self._paint(g['tl'], tone)}{self._paint(label, 'bright')}"
                    f"{self._paint(g['h'] * fill, tone)}{self._paint(g['tr'], tone)}")

    def _panel_rule(self):
        g = self._g()
        width = self._w()
        self._write(self._paint(g["lt"] + g["h"] * (width - 2) + g["rt"], "frame"))

    def _panel_foot(self):
        g = self._g()
        width = self._w()
        self._write(self._paint(g["bl"] + g["h"] * (width - 2) + g["br"], "frame"))

    def _row(self, body, tone="frame"):
        """Bordered row: total visible width is exactly the terminal width."""
        g = self._g()
        width = self._w()
        room = width - 3
        if _vlen(body) > room:
            body = _clip_ansi(body, room)
        self._write(f"{self._paint(g['v'], tone)} {body}{' ' * max(0, room - _vlen(body))}"
                    f"{self._paint(g['v'], tone)}")

    def identity(self, *, version, status="", clock=""):
        """One shared two-line block identity for chat and command screens."""
        width = self._w()
        g = self._g()
        inner = width - 2          # columns between the two borders

        def centred(text, raw=False):
            body = text if raw else self._paint(text, "muted")
            room = inner - _vlen(body)
            left = max(0, room // 2)
            self._write(f"{self._paint(g['v'], 'frame')}{' ' * left}{body}"
                        f"{' ' * max(0, room - left)}{self._paint(g['v'], 'frame')}")

        # Reference layout: two original ANSI-shadow marks, not a replacement
        # bitmap font. Centre each whole raster, keeping its row origins fixed.
        marks = (branding.WORDMARK_FULL, branding.WORDMARK_AGENTIC)
        self._panel_title("")
        total_rows = sum(len(mark) for mark in marks)
        row_offset = 0
        for label, mark in zip(("ZELINE", "AGENTIC AI"), marks):
            mark_width = max(_vlen(row) for row in mark)
            if not self.unicode_ok or mark_width > inner - 2:
                centred(self._paint(label, "light"), raw=True)
                row_offset += len(mark)
                continue
            left = (inner - mark_width) // 2
            right = inner - mark_width - left
            for index, row in enumerate(mark):
                tone_index = (row_offset + index) * (len(branding._GRADIENT) - 1) // (total_rows - 1)
                tone = branding._GRADIENT[tone_index]
                padded = row + " " * (mark_width - _vlen(row))
                body = f"\033[{tone}m{padded}\033[0m" if self.color else padded
                self._write(self._paint(g['v'], 'frame') + " " * left + body
                            + " " * right + self._paint(g['v'], 'frame'))
            row_offset += len(mark)
        state = self._paint(status, "bright")
        if clock:
            state += f" {self._paint(clock, 'muted')}"
        centred(f"{self._paint('v' + version, 'muted')}   {self._paint(g['dot'], 'frame')}   "
                f"{state}", raw=True)
        self._panel_foot()
        self._write()


    # ------------------------------------------------------------- welcome
    def welcome(self, *, version, agent, model, provider, workspace, tool_profile,
                tools=0, skills=0, memory=True, status="ONLINE", clock="",
                config_path="", tool_lines=(), skill_lines=(),
                runtime_lines=(), session_lines=()):
        """Big gradient ZELINE wordmark, then two equal-width info tables.

        The wordmark keeps the original block art and cyan→blue gradient. Every
        extra line (tagline, version, status) is centred on the *inner* width,
        so it sits exactly between the borders rather than near them.
        """
        width = self._w()
        if not self.ok():
            self.status([
                ("Agent", agent), ("Model", model), ("Provider", provider),
                ("Workspace", workspace), ("Tools", tool_profile),
            ])
            return

        self.identity(version=version, status=status, clock=clock)

        # ---- boxes 2 + 3: equal-width info tables ----------------------
        left_rows = [
            ("Model", _short_model(model)),
            ("Provider", provider),
            ("Agent", agent),
            ("Workspace", workspace),
            ("Config", config_path or "-"),
        ]
        right_rows = [
            ("Tools", f"{tools} available"),
            ("Skills", f"{skills} loaded"),
            ("Memory", "enabled" if memory else "disabled"),
            ("Profile", tool_profile),
        ]
        for line in runtime_lines:
            label, _, value = str(line).partition(":")
            left_rows.append((label.strip().title() or "Setting", value.strip() or "-"))
        for line in session_lines:
            label, _, value = str(line).partition(":")
            right_rows.append((label.strip().title() or "Reach", value.strip() or "-"))
        if skill_lines:
            right_rows.append(("Top skills", ", ".join(skill_lines[:2])))

        # Match the reference: paired facts on wide terminals, stacked only
        # when the measured terminal cannot accommodate two readable cards.
        if width >= self.STACK_BELOW:
            self._table_pair("what zeline can do", left_rows,
                             "what it can reach", right_rows)
        else:
            self._table("what zeline can do", left_rows)
            self._write()
            self._table("what it can reach", right_rows)
        self._write()

    # ---------------------------------------------------------- info tables
    def _table(self, title, rows):
        """Full-width fact card with fixed columns and lossless value wrapping."""
        self._panel_title(title)
        inner = self._w() - 2
        label_width = min(18, max(8, inner // 3))
        value_width = max(1, inner - label_width - 7)
        g = self._g()
        for label, value in rows:
            labels = _wrap(sanitize(str(label)), label_width) or [""]
            values = _wrap(sanitize(str(value)), value_width) or [""]
            for i in range(max(len(labels), len(values))):
                label_part = labels[i] if i < len(labels) else ""
                value_part = values[i] if i < len(values) else ""
                body = (" " + self._paint(label_part + " " * max(0, label_width - _vlen(label_part)), "light")
                        + " " + self._paint(g['v'], "frame") + "  " + value_part)
                self._write(self._paint(g['v'], "frame") + body
                            + " " * max(0, inner - _vlen(body))
                            + self._paint(g['v'], "frame"))
        self._panel_foot()

    def _table_pair(self, left_title, left_rows, right_title, right_rows):
        """Two tables side by side, each with its own borders."""
        width = self._w()
        # left card (left_w) + right card (right_w) - shared seam = width
        left_w = max(24, (width + 1) // 2)
        right_w = width - left_w + 1
        pad = max([len(l) for l, _ in left_rows + right_rows] or [3])
        height = max(len(left_rows), len(right_rows))
        g = self._g()

        def top(title, w):
            label = f" {sanitize(title)} "
            fill = max(0, w - 2 - len(label))
            return (self._paint(g["tl"], "frame") + self._paint(label, "bright")
                    + self._paint(g["h"] * fill, "frame") + self._paint(g["tr"], "frame"))

        lines = [top(left_title, left_w) + top(right_title, right_w)]
        for index in range(height):
            cells = []
            for rows, w in ((left_rows, left_w), (right_rows, right_w)):
                if index < len(rows):
                    label, value = rows[index]
                    body = (f"{self._paint(f'{label:<{pad}}', 'bright')}  "
                            f"{self._paint(g['dot'], 'frame')}  {sanitize(str(value))}")
                else:
                    body = ""
                body = _clip_ansi(body, w - 3)
                cells.append(body + " " * max(0, w - 3 - _vlen(body)))
            lines.append(f"{self._paint(g['v'], 'frame')} {cells[0]}"
                         f"{self._paint(g['v'], 'frame')}{self._paint(g['v'], 'frame')} "
                         f"{cells[1]}{self._paint(g['v'], 'frame')}")
        lines.append(self._paint(g["bl"] + g["h"] * (left_w - 2) + g["br"], "frame")
                     + self._paint(g["bl"] + g["h"] * (right_w - 2) + g["br"], "frame"))

        # Trim any overflow deterministically (two cards share one seam).
        overflow = max(_vlen(line) for line in lines) - width
        while overflow > 0 and right_w > 18:
            right_w -= 1
            lines = [top(left_title, left_w) + top(right_title, right_w)]
            for index in range(height):
                cells = []
                for rows, w in ((left_rows, left_w), (right_rows, right_w)):
                    if index < len(rows):
                        label, value = rows[index]
                        body = (f"{self._paint(f'{label:<{pad}}', 'bright')}  "
                                f"{self._paint(g['dot'], 'frame')}  {sanitize(str(value))}")
                    else:
                        body = ""
                    body = _clip_ansi(body, w - 3)
                    cells.append(body + " " * max(0, w - 3 - _vlen(body)))
                lines.append(f"{self._paint(g['v'], 'frame')} {cells[0]}"
                             f"{self._paint(g['v'], 'frame')}{self._paint(g['v'], 'frame')} "
                             f"{cells[1]}{self._paint(g['v'], 'frame')}")
            lines.append(self._paint(g["bl"] + g["h"] * (left_w - 2) + g["br"], "frame")
                         + self._paint(g["bl"] + g["h"] * (right_w - 2) + g["br"], "frame"))
            overflow = max(_vlen(line) for line in lines) - width
        for line in lines:
            self._write(line)

    def _fit(self, value, room):
        value = sanitize(value)
        room = max(8, room)
        if len(value) <= room:
            return value
        keep = max(4, (room - 3) // 2)
        return f"{value[:keep]}...{value[-keep:]}"

    # ---------------------------------------------------------------- status
    def status(self, rows):
        rows = [(sanitize(label), sanitize(value)) for label, value in rows]
        if not rows:
            return
        pad = max(len(label) for label, _ in rows)
        if not self.ok():
            for label, value in rows:
                line = f"  {label:<{pad}} : {value}"
                self._write(line if len(line) <= self._w() else _clip(line, self._w()))
            self._write()
            return
        self._panel_title("status")
        for label, value in rows:
            self._row(f"{self._paint(f'{label:<{pad}}', 'bright')}  {value}")
        self._panel_foot()
        self._write()

    # ---------------------------------------------------------------- blocks
    def user(self, text, *, name="You"):
        body = sanitize(text)
        g = self._g()
        if not self.ok():
            for line in _wrap(f"{sanitize(name)} {g['chev']} {body}", self._w()):
                self._write(line)
            self._write()
            return
        self._panel_title(sanitize(name).lower())
        for raw in body.splitlines() or [""]:
            for line in _wrap(raw, self._w() - 4):
                self._row(line)
        self._panel_foot()
        self._write()

    def reply(self, text, *, agent="Zeline"):
        body = sanitize(text)
        if not self.ok():
            self._write(f"{sanitize(agent)} {self._g()['chev']}")
            self._markdown(body)
            self._write()
            return
        self._panel_title(sanitize(agent).lower())
        self._markdown(body, framed=True)
        self._panel_foot()
        self._write()

    def render_reply(self, text):
        buffer = io.StringIO()
        previous = self.stream
        self.stream = buffer
        try:
            self._markdown(sanitize(text))
        finally:
            self.stream = previous
        return buffer.getvalue()

    def _markdown(self, body, framed=False):
        if not body:
            return
        if not self.color:
            for line in body.splitlines():
                self._write(f"  {line}" if line.strip() else "")
            return
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.theme import Theme
        inner = Console(width=max(20, self._w() - 4 if framed else self._w() - 2),
                        force_terminal=True, color_system="256", highlight=False,
                        emoji=False, markup=False, theme=Theme(_MARKDOWN_STYLES))
        with inner.capture() as capture:
            inner.print(Markdown(body, code_theme="one-dark"), crop=False, overflow="fold")
        rendered = capture.get().splitlines()
        for index, line in enumerate(rendered):
            cleaned = line.rstrip()
            plain = _ANSI_STRIP.sub("", cleaned).strip()
            if plain.startswith("`") or plain.endswith("```"):
                continue
            if framed:
                if not plain:
                    nxt = _ANSI_STRIP.sub("", rendered[index + 1]).strip() if index + 1 < len(rendered) else ""
                    if not nxt:
                        continue
                self._row(cleaned)
            elif cleaned:
                self._write(f"  {cleaned}")

    # ------------------------------------------------------------ activity
    def tool(self, name, arguments=None):
        g = self._g()
        human = _TOOL_NAMES.get(str(name), str(name).replace("_", " "))
        self._activity.append(human)
        body = f"{self._paint(g['bullet'], 'bright')} {sanitize(human)} {self._paint('running', 'muted')}"
        if self.ok():
            self._row(body)
        else:
            self._write(f"  * {sanitize(human)}...")

    def notice(self, text, *, kind="info"):
        g = self._g()
        mark = {"info": g["bullet"], "warn": "!", "error": "x"}.get(kind, g["bullet"])
        tone = {"warn": "warn", "error": "error"}.get(kind, "bright")
        body = f"{self._paint(mark, tone)} {self._paint(sanitize(text), tone)}"
        if self.ok():
            self._row(body)
        else:
            self._write(f"  {sanitize(text)}")

    def file(self, path, caption="", kind=""):
        g = self._g()
        note = f" \u2014 {sanitize(caption)}" if caption else ""
        suffix = f" ({sanitize(kind)})" if kind else ""
        body = f"{self._paint('file', 'bright')} {sanitize(path)}{suffix}{note}"
        self._activity.append(f"file {sanitize(path)}")
        if self.ok():
            self._row(body)
        else:
            self._write(f"  file: {sanitize(path)}{suffix}{note}")

    def question(self, question, options=()):
        choices = [sanitize(option) for option in options]
        g = self._g()
        if not self.ok():
            self._write()
            for line in _wrap(f"  question: {sanitize(question)}", self._w()):
                self._write(line)
            for index, option in enumerate(choices, start=1):
                letter = chr(64 + index) if index <= 26 else str(index)
                for line in _wrap(f"    {letter}) {option}", self._w()):
                    self._write(line)
            return
        self._panel_title("question")
        for raw in sanitize(question).splitlines() or [""]:
            for line in _wrap(raw, self._w() - 4):
                self._row(line)
        if choices:
            self._panel_rule()
            for index, option in enumerate(choices, start=1):
                letter = chr(64 + index) if index <= 26 else str(index)
                self._row(f"{self._paint(letter, 'light')}  {option}")
            self._panel_rule()
            self._row(self._paint("pick a letter or number, or type your own answer", "muted"))
        self._panel_foot()

    def answer(self, text):
        g = self._g()
        body = f"{self._paint('answer', 'bright')} {sanitize(text)}"
        self._activity.append(f"answered {sanitize(text)[:32]}")
        if self.ok():
            self._row(body)
        else:
            self._write(f"  answer: {sanitize(text)}")

    def activity(self, title="recent activity"):
        if not self._activity:
            return
        if not self.ok():
            return
        self._panel_title(title)
        for item in self._activity[-5:]:
            self._row(f"{self._paint(self._g()['bullet'], 'bright')} {sanitize(item)}")
        self._panel_foot()

    def help(self):
        rows = [("/help", "this list"), ("/status", "agent and session status"),
                ("/model", "select model"), ("/clear", "start a fresh context"),
                ("/undo", "restore the last workspace edit"), ("exit", "close chat, Ctrl+D")]
        pad = max(len(c) for c, _ in rows)
        if not self.ok():
            self._write()
            for c, d in rows:
                self._write(f"    {c:<{pad}}  {d}")
            self._write()
            return
        self._panel_title("commands")
        for c, d in rows:
            self._row(f"{self._paint(f'{c:<{pad}}', 'bright')}  {d}")
        self._panel_rule()
        self._row(self._paint("Enter send   Alt+Enter newline   Ctrl+C stop", "muted"))
        self._panel_foot()
        self._write()

    def footer(self):
        g = self._g()
        width = self._w()
        variants = [
            f"{g['chev']} Enter send   Alt+Enter newline   /help   Ctrl+C stop",
            f"{g['chev']} Enter send   /help   Ctrl+C stop",
            f"{g['chev']} Enter send",
        ]
        text = (next((v for v in variants if len(v) <= width), variants[-1])
                if self.ok() else variants[-1])
        self._write(self._paint(text, "muted"))
        hint = "Type exit to close chat" if width >= 24 else "exit: close chat"
        self._write(self._paint(hint, "muted"))

    def goodbye(self, text="Session closed."):
        self._write(self._paint(sanitize(text), "muted"))


def _short_model(model):
    """Model slug without the provider prefix, trimmed for a table cell."""
    short = model.split("/")[-1] if "/" in model else model
    if short.endswith(".gguf"):
        short = short[:-5]
    return short if len(short) <= 34 else short[:31] + "..."


def _zip_rows(left, right):
    """Yield (left, right) pairs padded to equal length."""
    for index in range(max(len(left), len(right))):
        yield (left[index] if index < len(left) else "",
               right[index] if index < len(right) else "")


def _clip_ansi(text, room):
    """Clip visible length while keeping colour codes intact."""
    if _vlen(text) <= room:
        return text
    out, visible, index = [], 0, 0
    while index < len(text) and visible < max(1, room - 1):
        match = _ANSI_STRIP.match(text, index)
        if match:
            out.append(match.group())
            index = match.end()
            continue
        char_width = _vlen(text[index])
        if visible + char_width > max(1, room - 1):
            break
        out.append(text[index])
        visible += char_width
        index += 1
    out.append("\u2026")
    return "".join(out) + "\033[0m" if _ANSI_STRIP.search(text) else "".join(out)


def _wrap(text, room):
    """Wrap terminal cells rather than codepoints (emoji/CJK may be two cells)."""
    room = max(8, room)
    if _vlen(text) <= room:
        return [text]
    lines, current = [], ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if _vlen(candidate) <= room:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        chunk = ""
        for char in word:
            if chunk and _vlen(chunk + char) > room:
                lines.append(chunk)
                chunk = ""
            chunk += char
        current = chunk
    if current:
        lines.append(current)
    return lines or [""]


def _clip(text, room):
    room = max(4, room)
    return text if len(text) <= room else text[: room - 1] + "\u2026"


def _vlen(text):
    plain = _ANSI_STRIP.sub("", text)
    try:
        from wcwidth import wcswidth
        cells = wcswidth(plain)
        return cells if cells >= 0 else len(plain)
    except ImportError:
        return len(plain)
