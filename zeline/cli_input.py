"""Interactive composer for the local chat REPL.

Two modes share one interface so the REPL never has to care which one is live:

- **Rich composer** (prompt_toolkit) on a real terminal: blue-accented prompt,
  multiline editing (Enter submits, Alt+Enter or Esc+Enter inserts a line
  break), in-memory history and slash-command completion.
- **Plain composer** everywhere else: the builtin ``input()``, optionally with
  trailing-backslash continuation for pasted multi-line prompts. This is the
  path used by redirected stdin, ``TERM=dumb``, Windows legacy consoles without
  prompt_toolkit, and the test suite.

prompt_toolkit is imported lazily and treated as optional. A missing library
degrades to the plain composer instead of breaking ``zeline chat``.

No prompt text is ever written to a history file: chat prompts routinely carry
paths, keys and personal data, so history is in-memory for the session only.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Callable, Iterable

# Completes only local REPL commands; the model never sees these.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "Show local commands and key bindings"),
    ("/status", "Show the active session and model"),
    ("/clear", "Forget the in-memory conversation context"),
    ("/undo", "Restore the previous workspace edit"),
    ("/exit", "Leave the local chat"),
)

_CONTINUATION = "\\"


def _stdout_isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _make_output(stream: Any, width: int | None) -> Any:
    """Build a prompt_toolkit output pinned to the UI width.

    ``create_output`` probes the file descriptor and can report a different
    size than the one we are drawing to. Building the VT100 output directly
    lets us hand it both the shimmed stream and a fixed size.
    """
    shim = _WidthLimited(stream, width)
    try:
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.output.vt100 import Vt100_Output

        if width:
            return Vt100_Output(
                shim,
                lambda: Size(rows=40, columns=width),
                term=os.environ.get("TERM", "xterm-256color"),
            )
    except Exception:
        pass
    from prompt_toolkit.output.defaults import create_output

    return create_output(stdout=shim)


class _WidthLimited:
    """File-like shim that truncates each written line to the terminal width.

    prompt_toolkit paints full-width lines; when its idea of the width is wider
    than the visible screen, the terminal wraps and the prompt staircase breaks
    into a mess. Clipping here is the only place that catches every paint path.
    """

    def __init__(self, stream: Any, width: int | None) -> None:
        self._stream = stream
        self._width = width

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def write(self, data: str) -> int:
        if self._width and data:
            data = "\n".join(
                line if len(line) <= self._width else line[: self._width]
                for line in data.split("\n")
            )
        self._stream.write(data)
        return len(data)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass


class ChatInput:
    """Read one operator message, with a safe non-TTY fallback."""

    def __init__(
        self,
        *,
        interactive: bool | None = None,
        input_fn: Callable[[str], str] | None = None,
        stream: Any = None,
        width: int | None = None,
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.width = width
        self._input_fn = input_fn
        if interactive is None:
            interactive = (
                _stdout_isatty(self.stream)
                and _stdout_isatty(getattr(sys, "stdin", None) or self.stream)
                and os.environ.get("TERM") != "dumb"
            )
        self.interactive = bool(interactive)
        self._session: Any = None

    # ------------------------------------------------------------------ hint
    @property
    def hint(self) -> str:
        """One line describing the bindings that are actually active."""
        if self._rich_session() is not None:
            return "Enter to send  •  Alt+Enter for a new line  •  /help for commands"
        return "Enter to send  •  end a line with \\ to continue  •  /help for commands"

    # ------------------------------------------------------------------ read
    def read(self) -> str:
        """Return the operator's message verbatim (caller decides to strip)."""
        session = self._rich_session()
        if session is not None:
            try:
                return session.prompt(
                    [("class:zeline.prompt", "You \u276f ")],  # noqa: ISC003
                )
            except EOFError:
                raise
            except KeyboardInterrupt:
                raise KeyboardInterrupt from None
        return self._plain_read()

    def _plain_read(self) -> str:
        reader = self._input_fn or input
        lines: list[str] = []
        while True:
            text = reader("You \u276f " if not lines else "    \u2026 ")
            stripped = text.rstrip()
            if stripped.endswith(_CONTINUATION) and len(stripped) > 1:
                lines.append(stripped[: -len(_CONTINUATION)])
                continue
            lines.append(text)
            return "\n".join(lines)

    # ---------------------------------------------------------------- choose
    def choose(self, question: str, options: Iterable[str]) -> str:
        """Pick one option, or return the operator's own free-text answer.

        KeyboardInterrupt propagates so the caller can cancel the turn; there
        is deliberately no auto-selected default that could answer on the
        operator's behalf.
        """
        choices = [str(option) for option in options]
        if self._rich_session() is not None:
            picked = self._rich_choose(question, choices)
            if picked is not None:
                return picked
        return self._plain_choose(question, choices)

    def _plain_choose(self, question: str, choices: list[str]) -> str:
        reader = self._input_fn or input
        while True:
            reply = reader("  answer \u203a ").strip()
            if not choices or not reply.isdigit():
                if reply:
                    return reply
                continue
            index = int(reply)
            if 1 <= index <= len(choices):
                return choices[index - 1]
            # A number outside the list is a mistake, not free text: never let
            # the model receive "9" as if the operator had answered it.

    def _rich_choose(self, question: str, choices: list[str]) -> str | None:
        try:
            from prompt_toolkit import Application
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import Layout
            from prompt_toolkit.layout.containers import Window
            from prompt_toolkit.layout.controls import FormattedTextControl
            from prompt_toolkit.styles import Style
        except Exception:
            return None

        options = [*choices, "Type my own answer"]
        state = {"index": 0, "answer": ""}
        bindings = KeyBindings()

        @bindings.add("up")
        def _up(event: Any) -> None:
            state["index"] = (state["index"] - 1) % len(options)

        @bindings.add("down")
        def _down(event: Any) -> None:
            state["index"] = (state["index"] + 1) % len(options)

        for digit in range(1, min(len(options), 9) + 1):

            def _pick(event: Any, position: int = digit - 1) -> None:
                state["index"] = position
                event.app.exit(result=position)

            bindings.add(str(digit))(_pick)

        @bindings.add("enter")
        def _enter(event: Any) -> None:
            event.app.exit(result=state["index"])

        @bindings.add("c-c")
        def _cancel(event: Any) -> None:
            event.app.exit(exception=KeyboardInterrupt())

        def render() -> list[tuple[str, str]]:
            fragments: list[tuple[str, str]] = []
            for position, option in enumerate(options):
                active = position == state["index"]
                label = f" {chr(65 + position) if position < 26 else position + 1} "
                fragments.append(("class:zeline.marker" if active else "", label))
                fragments.append(("class:zeline.active" if active else "", f"{option}\n"))
            fragments.append(("class:zeline.hint", " \u2191\u2193 choose  \u2022  Enter select  \u2022  Esc cancel\n"))
            return fragments

        style = Style.from_dict({
            "zeline.marker": "fg:#63b3ff bold",
            "zeline.active": "fg:#dbeafe bold",
            "zeline.hint": "fg:#7f9cc0",
        })
        app: Any = Application(
            layout=Layout(Window(FormattedTextControl(render), dont_extend_height=True)),
            key_bindings=bindings,
            style=style,
            full_screen=False,
            erase_when_done=True,
        )
        try:
            index = app.run()
        except KeyboardInterrupt:
            raise KeyboardInterrupt from None
        except (EOFError, Exception) as exc:
            if isinstance(exc, EOFError):
                raise
            return None
        if index is None:
            return None
        if index < len(choices):
            return choices[index]
        return self._plain_choose(question, choices)

    # ------------------------------------------------------------------ rich
    def _rich_session(self) -> Any:
        """Build (once) the prompt_toolkit session, or None when unavailable."""
        if not self.interactive:
            return None
        if self._session is not None:
            return self._session if self._session is not False else None
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import Completer, Completion
            from prompt_toolkit.history import InMemoryHistory
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.styles import Style
        except Exception:
            self._session = False
            return None

        class _CommandCompleter(Completer):
            def get_completions(self, document: Any, complete_event: Any) -> Any:
                text = document.text_before_cursor
                if not text.startswith("/"):
                    return
                for command, description in COMMANDS:
                    if command.startswith(text.split()[0]):
                        yield Completion(command, start_position=-len(text), display_meta=description)

        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event: Any) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def _newline(event: Any) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("c-d")
        def _eof(event: Any) -> None:
            raise EOFError

        style = Style.from_dict({
            "zeline.prompt": "fg:#63b3ff bold",
            "": "fg:#e6edf7",
            "completion-menu.completion": "bg:#0b1a2c fg:#cfe0f5",
            "completion-menu.completion.current": "bg:#123a63 fg:#ffffff",
            "completion-menu.meta.completion": "bg:#0b1a2c fg:#7f9cc0",
            "completion-menu.meta.completion.current": "bg:#123a63 fg:#9fc7f5",
        })
        try:
            # prompt_toolkit measures the terminal itself and pads repaints to
            # that size. On a phone the measurement can disagree with the real
            # PTY, which is what pushed the prompt line past the right edge, so
            # publish the UI's width before it samples and cap every write.
            # Output carries its measured size; never leak a composer width
            # into later commands via the process-wide COLUMNS variable.
            output = _make_output(self.stream, self.width)
            if self.width and self.width < 60:
                # On a phone the cancel-echo and CPR probe from prompt_toolkit
                # write past the panel. Plain input keeps the frame intact.
                self._session = False
                return None
            self._session = PromptSession(
                # The input line is the single user-message transcript.
                erase_when_done=False,
                output=output,
                history=InMemoryHistory(),
                completer=_CommandCompleter(),
                key_bindings=bindings,
                multiline=False,
                style=style,
                enable_open_in_editor=False,
                complete_while_typing=True,
                # prompt_toolkit otherwise pads every repaint to 80 columns,
                # which produced ragged over-wide lines on a 48-column Termux.
                reserve_space_for_menu=0,
                prompt_continuation="  ",
            )
        except Exception:
            self._session = False
            return None
        return self._session
