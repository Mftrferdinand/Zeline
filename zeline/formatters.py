"""Run the project's formatter after the agent writes or edits a file.

OpenCode does this (docs/formatters): after every write/edit it runs the
matching formatter, so agent-written code lands in the project's real style
instead of producing a diff full of whitespace noise.

Rules that make this safe rather than destructive:

- **Never lose the agent's write.** The file is already on disk before a
  formatter runs. If the formatter fails, times out, or is missing, the written
  content stays exactly as-is and we only report what happened.
- **Only run what is already installed.** Commands are resolved with
  ``shutil.which``; nothing is downloaded. Package-manager runners like ``npx``
  are deliberately NOT used — they would fetch code from the network as a side
  effect of writing a file.
- **Bounded.** A formatter gets a short timeout and its process group is killed
  on expiry, so a hung binary cannot hold the turn.
- **Reported, not silent.** A formatting failure is usually a real syntax error
  in what the model just wrote, which is valuable signal — it is appended to the
  tool result rather than swallowed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from zeline import config

# Seconds a single formatter may run. Formatting one file is fast; anything
# slower is a broken setup, not work worth waiting for.
FORMAT_TIMEOUT_SECONDS = 20

# extension -> ordered candidate commands. First installed one wins.
# `{file}` is replaced with the absolute path.
DEFAULT_FORMATTERS: dict[str, tuple[tuple[str, ...], ...]] = {
    ".py": (("ruff", "format", "{file}"), ("black", "-q", "{file}")),
    ".pyi": (("ruff", "format", "{file}"), ("black", "-q", "{file}")),
    ".rs": (("rustfmt", "{file}"),),
    ".go": (("gofmt", "-w", "{file}"),),
    ".js": (("biome", "format", "--write", "{file}"), ("prettier", "--write", "{file}")),
    ".jsx": (("biome", "format", "--write", "{file}"), ("prettier", "--write", "{file}")),
    ".ts": (("biome", "format", "--write", "{file}"), ("prettier", "--write", "{file}")),
    ".tsx": (("biome", "format", "--write", "{file}"), ("prettier", "--write", "{file}")),
    ".json": (("biome", "format", "--write", "{file}"), ("prettier", "--write", "{file}")),
    ".css": (("biome", "format", "--write", "{file}"), ("prettier", "--write", "{file}")),
    ".scss": (("prettier", "--write", "{file}"),),
    ".html": (("prettier", "--write", "{file}"),),
    ".md": (("prettier", "--write", "{file}"),),
    ".yml": (("prettier", "--write", "{file}"),),
    ".yaml": (("prettier", "--write", "{file}"),),
    ".sh": (("shfmt", "-w", "{file}"),),
    ".bash": (("shfmt", "-w", "{file}"),),
    ".toml": (("taplo", "format", "{file}"),),
    ".lua": (("stylua", "{file}"),),
}


def enabled() -> bool:
    return bool(getattr(config, "FORMAT_ON_WRITE", True))


def _overrides() -> dict[str, tuple[tuple[str, ...], ...]]:
    """Operator-supplied map: {".ext": "cmd {file}"} or {".ext": ["cmd", "{file}"]}.

    An empty value disables formatting for that extension, which is how an
    operator opts one language out without turning the whole feature off.
    """
    raw = getattr(config, "FORMATTERS", None)
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, tuple[tuple[str, ...], ...]] = {}
    for key, value in raw.items():
        ext = str(key).strip().lower()
        if not ext.startswith("."):
            ext = f".{ext}"
        if value in (None, "", [], ()):
            parsed[ext] = ()
            continue
        if isinstance(value, str):
            parts = tuple(value.split())
        else:
            try:
                parts = tuple(str(item) for item in value)
            except TypeError:
                continue
        if not parts:
            parsed[ext] = ()
            continue
        # Always operate on the written file, even if the operator forgot the
        # placeholder — otherwise the formatter would run on the whole project.
        if "{file}" not in parts:
            parts = (*parts, "{file}")
        parsed[ext] = (parts,)
    return parsed


def candidates_for(path: Path) -> tuple[tuple[str, ...], ...]:
    ext = path.suffix.lower()
    overrides = _overrides()
    if ext in overrides:
        return overrides[ext]
    return DEFAULT_FORMATTERS.get(ext, ())


def _resolve(command: tuple[str, ...], path: Path) -> list[str] | None:
    binary = shutil.which(command[0])
    if binary is None:
        return None
    return [binary if part == command[0] else part.replace("{file}", str(path)) for part in command]


def format_file(path: Path) -> str:
    """Format one file in place. Returns a short note, or '' when nothing ran.

    Never raises, and never changes the file when the formatter fails: the
    agent's content is already written and must survive a broken toolchain.
    """
    if not enabled():
        return ""
    try:
        if not path.is_file():
            return ""
    except OSError:
        return ""
    for command in candidates_for(path):
        argv = _resolve(command, path)
        if argv is None:
            continue
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=FORMAT_TIMEOUT_SECONDS,
                cwd=str(path.parent),
                # Keep the formatter out of this process's group so a timeout
                # kill cannot reach the agent itself.
                start_new_session=os.name != "nt",
            )
        except subprocess.TimeoutExpired:
            return f" (formatter {command[0]} timed out after {FORMAT_TIMEOUT_SECONDS}s; file left unformatted)"
        except OSError as exc:
            return f" (formatter {command[0]} could not run: {exc.__class__.__name__})"
        if completed.returncode == 0:
            return f" (formatted with {command[0]})"
        # A non-zero exit is usually a real syntax error in what was just
        # written — surface the first line so the model can fix it.
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        first = detail[0][:200] if detail else f"exit {completed.returncode}"
        return f" (formatter {command[0]} reported: {first})"
    return ""
