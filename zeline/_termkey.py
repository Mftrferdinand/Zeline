"""Cross-platform terminal key reading.

Zeline's interactive prompts (masked secret input, arrow-key pickers) need to
read single keypresses without waiting for Enter. The POSIX way is
``termios`` + ``tty``; neither module exists on Windows, where the equivalent is
``msvcrt``. Importing ``termios`` at module top level made ``zeline`` unusable
on Windows: ``import zeline.cli`` raised ``ModuleNotFoundError`` before any
command could run.

This module hides that difference behind one API:

    with raw_mode():
        key = read_key()          # single character
        action = read_menu_key()  # "up" / "down" / "enter" / "cancel" / ""

On Windows ``raw_mode()`` is a no-op because ``msvcrt.getwch()`` already reads
unbuffered, and arrow keys arrive as a two-part sequence with a 0x00/0xE0
prefix instead of the ANSI ``ESC [ A`` form.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator

IS_WINDOWS = os.name == "nt"


@contextlib.contextmanager
def raw_mode() -> Iterator[None]:
    """Put the terminal in raw mode for the duration of the block.

    No-op on Windows (``msvcrt`` reads are already unbuffered) and whenever
    stdin is not a TTY, so callers can wrap prompt loops unconditionally.
    """
    if IS_WINDOWS or not sys.stdin.isatty():
        yield
        return

    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def read_key() -> str:
    """Read one keypress and return it as a string.

    Returns ``""`` at end of input. Arrow keys yield their raw prefix here;
    use :func:`read_menu_key` to decode them into actions.
    """
    if IS_WINDOWS:  # pragma: no cover - platform specific
        import msvcrt

        return msvcrt.getwch()  # type: ignore[attr-defined]
    return os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="ignore")


def read_menu_key() -> str:
    """Read a keypress and decode it into a menu action.

    Returns ``"up"``, ``"down"``, ``"enter"``, ``"cancel"``, or ``""`` for keys
    with no meaning in a menu. Raises ``KeyboardInterrupt`` on Ctrl-C, which
    raw mode would otherwise swallow.
    """
    char = read_key()

    if char in {"\r", "\n"}:
        return "enter"
    if char == "\x03":
        raise KeyboardInterrupt
    if char in {"q", "Q"}:
        return "cancel"

    if IS_WINDOWS:  # pragma: no cover - platform specific
        # Windows sends arrows as two reads: a 0x00/0xE0 prefix, then a code.
        if char in {"\x00", "\xe0"}:
            code = read_key()
            if code == "H":
                return "up"
            if code == "P":
                return "down"
            return ""
        if char == "\x1b":  # bare Esc
            return "cancel"
        return ""

    # POSIX: arrows arrive as the ANSI sequence ESC [ A / ESC [ B.
    if char == "\x1b":
        second = read_key()
        third = read_key()
        if second == "[" and third == "A":
            return "up"
        if second == "[" and third == "B":
            return "down"
        if second == "" and third == "":  # Esc alone
            return "cancel"
    return ""


def read_secret(prompt: str) -> str:
    """Read a secret, echoing one star per character.

    Falls back to ``getpass`` when stdin is not a TTY (piped input, CI).
    """
    if not sys.stdin.isatty():
        import getpass

        return getpass.getpass(prompt)

    chars: list[str] = []
    print(prompt, end="", flush=True)
    with raw_mode():
        while True:
            char = read_key()
            if char in {"\r", "\n"}:
                print()
                break
            if char == "\x03":
                raise KeyboardInterrupt
            if char in {"\x7f", "\b"}:
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
                continue
            if char and char.isprintable():
                chars.append(char)
                print("*", end="", flush=True)
    return "".join(chars)
