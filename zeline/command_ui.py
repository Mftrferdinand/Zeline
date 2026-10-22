"""Unified command presentation. Never redirect stdout or patch builtins.

Machine-readable output and non-TTY service logs retain their original form.
"""
from __future__ import annotations
import argparse
import builtins
import contextvars
import functools
import io
import os
import re
import sys
import threading
from datetime import datetime
from zeline import __version__
from zeline.cli_ui import ChatUI, _clip_ansi, _vlen, _wrap, sanitize, terminal_width

_current = contextvars.ContextVar("zeline_command_screen", default=None)
_live_screen = None


def current_screen():
    return _current.get()


def interactive(stream=None):
    stream = sys.stdout if stream is None else stream
    try:
        return bool(stream.isatty()) and os.environ.get("TERM") != "dumb"
    except (AttributeError, OSError, ValueError):
        return False


class CommandScreen:
    """One identity, structured cards and an atomic streaming activity panel."""
    def __init__(self, title, *, stream=None, width=None, live=False, show_logo=False):
        self.ui = ChatUI(stream=stream, width=width)
        self.title, self.live = title, live
        self.show_logo = show_logo
        self.lock = threading.RLock()
        self.open_panel = False
        self._state = None
        self._status_visible = False
        self._status_stop = threading.Event()
        self._status_thread = None
        self._frame = 0

    def begin(self):
        if self.show_logo:
            self.ui.identity(version=__version__, status="CLI")
            self.ui._write()

    def close(self):
        with self.lock:
            self._erase_status()
            if self.open_panel:
                self.ui._panel_foot()
                self.open_panel = False
            self.ui.stream.flush()

    def section(self, title):
        with self.lock:
            self.close()
            self.ui._panel_title(sanitize(title)[:max(1, self.ui._w() - 6)])
            self.open_panel = True

    def facts(self, title, rows):
        with self.lock:
            self.close()
            self.ui._table(title, [(sanitize(k), sanitize(v)) for k, v in rows])
            self.ui._write()
            self.ui.stream.flush()

    def text(self, text):
        with self.lock:
            self._erase_status()
            for line in sanitize(text).split("\n"):
                line = line.strip()
                if line.startswith("==>") or (line.endswith(":") and len(line) < 60):
                    self.section(line.removeprefix("==>").strip().rstrip(":"))
                    continue
                if not self.open_panel:
                    self.section("Live activity" if self.live else self.title)
                tone = "error" if re.search(r"\b(error|failed|crash|refused)\b", line, re.I) else "light"
                if self.live and line:
                    line = datetime.now().strftime("%H:%M:%S") + "  " + line
                for wrapped in _wrap(line, self.ui._w() - 4):
                    self.ui._row(self.ui._paint(wrapped, tone))
            self.ui.stream.flush()

    def _erase_status(self):
        if self._status_visible:
            self.ui.stream.write("\r\x1b[2K")
            self._status_visible = False

    def _draw_status(self):
        if self._state is None:
            return
        state, detail = self._state
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if self.ui.unicode_ok else "-\\|/"
        frame = frames[self._frame % len(frames)]
        self._frame += 1
        width = self.ui._w() - 1
        text = _clip_ansi(f"{frame} {state} | {detail}", width)
        # One padded write replaces the previous frame. Never flush a blank
        # erased row between frames: mobile terminals show that as blinking.
        padded = text + " " * max(0, width - _vlen(text))
        self.ui.stream.write("\r" + self.ui._paint(padded, "bright"))
        self.ui.stream.flush()
        self._status_visible = True

    def _animate(self):
        # A single small footer, no alternate screen, cursor hiding or services.
        while not self._status_stop.wait(0.25):
            with self.lock:
                if self._status_stop.is_set():
                    break
                self._draw_status()

    def set_state(self, state, detail=""):
        state, detail = sanitize(state), " ".join(sanitize(detail).split())
        with self.lock:
            if self._state == (state, detail):
                return
            self.close()
            self._state = (state, detail)
            if self.live and self.ui.color and interactive(self.ui.stream):
                self._draw_status()
                if self._status_thread is None:
                    self._status_thread = threading.Thread(
                        target=self._animate, daemon=True, name="zeline-cli-indicator")
                    self._status_thread.start()
            else:
                self.ui._write(f"[{state}] {detail}")

    def finish(self, *, failed=False):
        self._status_stop.set()
        if self._status_thread is not None:
            self._status_thread.join(timeout=1)
        with self.lock:
            self.close()
            if self.live and self._state is not None:
                state = "FAILED" if failed or self._state[0] == "FAILED" else "STOPPED"
                detail = self._state[1] if state == "FAILED" else "foreground gateway exited"
                self.ui._write(f"[{state}] {detail}")
                self.ui.stream.flush()



def emit(*values, sep=" ", end="\n", file=None, flush=False):
    """CLI-owned print, preserving raw streams and interactive prompt fragments."""
    screen = _current.get() or _live_screen
    if screen is None or (file is not None and file is not sys.stdout):
        return builtins.print(*values, sep=sep, end=end, file=file, flush=flush)
    text = (" " if sep is None else sep).join(str(value) for value in values)
    if end != "\n":
        screen.close()
        return builtins.print(sanitize(text), end=end, file=file, flush=flush)
    screen.text(text)


def menu_label(value):
    """Display-only model/menu label: strip controls, flatten and fit one row.

    Callers retain original option values and return their original index.
    """
    label = " ".join(sanitize(value).split())
    return _clip_ansi(label, max(8, terminal_width() - 10))


def menu_frame(title, options, selected, *, width=None, limit=8):
    """Compose a bounded framed menu; caller owns raw-mode CRLF writes."""
    current = current_screen()
    source = current.ui if current is not None else ChatUI()
    stream = io.StringIO()
    ui = ChatUI(stream=stream, width=width or source.width,
                color=source.color, unicode_ok=source.unicode_ok)
    limit = max(1, limit)
    first = min(max(0, selected - limit // 2), max(0, len(options) - limit))
    visible = options[first:first + limit]
    ui._panel_title(sanitize(title).rstrip(":"))
    marker = "❯" if ui.unicode_ok else ">"
    for offset, option in enumerate(visible, first):
        active = offset == selected
        label = _clip_ansi(" ".join(sanitize(option).split()), ui._w() - 7)
        text = f"{marker if active else ' '} {label}"
        ui._row(ui._paint(text, "bright" if active else "light"))
    ui._row("")
    hint = "Up/Down choose | Enter select | Esc back"
    if len(options) > len(visible):
        hint = f"{selected + 1}/{len(options)} | " + hint
    for line in _wrap(hint, ui._w() - 4):
        ui._row(ui._paint(line, "muted"))
    ui._panel_foot()
    return stream.getvalue().splitlines()


def raw_print(*values, **kwargs):
    """Trusted menu cursor operations only; never route untrusted data here."""
    screen = _current.get() or _live_screen
    if screen is not None:
        with screen.lock:
            screen.close()
            return builtins.print(*values, **kwargs)
    return builtins.print(*values, **kwargs)


def read_input(prompt=""):
    """Close the current panel before a native interactive input prompt."""
    screen = _current.get()
    if screen is not None:
        screen.close()
    return builtins.input(prompt)


def _raw_command(name, args, kwargs):
    if name in {"cmd_chat", "cmd_config"}:
        return True
    if name == "cmd_undo" and kwargs.get("diff"):
        return True
    action = args[0] if args else kwargs.get("action")
    return name in {"cmd_openapi", "cmd_customtools", "cmd_plugins"} and action == "path"


def present_command(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        global _live_screen
        if (_current.get() is not None or not interactive()
                or _raw_command(function.__name__, args, kwargs)):
            return function(*args, **kwargs)
        title = function.__name__.removeprefix("cmd_").replace("_", " ").title()
        live = function.__name__ == "cmd_gateway_run"
        show_logo = live or (function.__name__ == "cmd_doctor" and kwargs.get("show_logo", True))
        screen = CommandScreen(title, live=live, show_logo=show_logo)
        token = _current.set(screen)
        previous_live = _live_screen
        if live:
            _live_screen = screen
        failed = True
        try:
            screen.begin()
            result = function(*args, **kwargs)
            failed = result not in (None, 0)
            return result
        finally:
            if live:
                _live_screen = previous_live
            screen.finish(failed=failed)
            _current.reset(token)
    return wrapped


class CommandArgumentParser(argparse.ArgumentParser):
    """Themed terminal help; plain argparse output in pipelines."""
    def print_help(self, file=None):
        destination = sys.stdout if file is None else file
        if not interactive(destination):
            return super().print_help(file=file)
        screen = CommandScreen("Command reference", stream=destination)
        try:
            screen.begin()
            screen.text(self.format_help().rstrip())
        finally:
            screen.close()
