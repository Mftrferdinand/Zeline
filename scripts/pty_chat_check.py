"""End-to-end PTY check of the real `zeline chat` composer and picker.

Runs the installed CLI inside a pseudo-terminal so prompt_toolkit sees a real
TTY, types a message, answers an ask_user prompt with the arrow keys, and
verifies the footer/hint plus that no tool arguments leak.

Only local commands and one stubbed turn are exercised; no network, no model.
"""
from __future__ import annotations

import os
import pty
import re
import select
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "_pty_chat_driver.py"


def spawn() -> tuple[int, int]:
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.environ["TERM"] = "xterm-256color"
        os.execv(sys.executable, [sys.executable, str(SCRIPT)])
    return pid, fd


def read_until(fd: int, pattern: str, timeout: float = 12.0) -> str:
    buffer = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
        if re.search(pattern.encode(), buffer):
            break
    return buffer.decode("utf-8", "replace")


def send(fd: int, text: str) -> None:
    os.write(fd, text.encode())


def main() -> int:
    pid, fd = spawn()
    try:
        screen = read_until(fd, "You \u276f|You >")
        assert "Zeline" in screen, screen[-800:]
        assert "Model" in screen, screen[-800:]
        send(fd, "/help\n")
        screen += read_until(fd, r"commands")
        assert "commands" in screen, screen[-1200:]
        send(fd, "coba pilih\n")
        screen += read_until(fd, r"question")
        assert "question" in screen, screen[-1200:]
        # Answer the ask_user picker with the down arrow then Enter.
        send(fd, "\x1b[B")
        time.sleep(0.3)
        send(fd, "\r")
        screen += read_until(fd, r"answer")
        assert "answer" in screen, screen[-1500:]
        assert "sup3rsecret" not in screen
        send(fd, "exit\n")
        screen += read_until(fd, r"Goodbye|Session closed")
        print("PTY_SCREEN_BYTES", len(screen))
        print("HAS_ANSI", bool(re.search(r"\x1b\[", screen)))
        print("LEAKED_SECRET", "sup3rsecret" in screen)
        for line in screen.splitlines()[-40:]:
            print("|", re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", line))
        return 0
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        os.waitpid(pid, 0)


if __name__ == "__main__":
    raise SystemExit(main())
