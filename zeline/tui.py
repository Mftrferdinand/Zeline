"""Terminal user interface layer for the Zeline CLI.

Centralizes everything the terminal shows so rendering decisions live in one
place: capability detection (color, unicode, width), ANSI painting, markdown
rendering, spinners, arrow-key selection menus, status cards, prompt
prefixes, and slash-command parsing.

``rich`` is OPTIONAL. When it is installed (and the terminal allows color)
markdown goes through ``rich.markdown``; when it is missing every function
falls back to a stdlib-only rendering that works on narrow screens, Termux,
and legacy Windows consoles. Nothing here imports ``zeline.cli`` — the CLI
imports this module, never the other way around — so the layer stays light
enough to use from any entry point.
"""
from __future__ import annotations

import contextlib
import os
import re
import shlex
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from zeline import branding
from zeline._termkey import raw_mode, read_menu_key

try:  # Optional pretty rendering; everything works without it.
    import rich  # noqa: F401
    from rich.console import Console
    from rich.markdown import Markdown

    RICH_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the install
    RICH_AVAILABLE = False


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def colors_enabled() -> bool:
    """True when ANSI color may be emitted on stdout."""
    return branding.color_enabled()


def unicode_supported() -> bool:
    """True when stdout can encode the block/chevron glyphs."""
    return branding.supports_unicode()


def terminal_width(default: int = 80) -> int:
    """Terminal width in columns, floored at 20; ``default`` when unknown."""
    return branding.terminal_width(default)


# ---------------------------------------------------------------------------
# Painting
# ---------------------------------------------------------------------------

# Shared 256-color palette, matching the CLI's established look. Gated by
# colors_enabled() via paint().
COLOR_BLUE = "\033[38;5;39m"  # regular blue — labels before ':'
COLOR_LIGHT_BLUE = "\033[38;5;117m"  # light blue — the 'you' prompt
COLOR_DARK_BLUE = "\033[38;5;27m"  # dark blue — agent reply prefix / rules
COLOR_RED = "\033[38;5;203m"  # soft red — error lines
COLOR_GREEN = "\033[38;5;42m"  # green — success lines
COLOR_DIM = "\033[90m"  # dim gray — hints, spinners, secondary text
COLOR_RESET = "\033[0m"


def paint(text: str, color: str) -> str:
    """Wrap text in an ANSI color only when the terminal supports color."""
    if not colors_enabled():
        return text
    return f"{color}{text}{COLOR_RESET}"


def label(text: str) -> str:
    """Color a 'label :' prefix blue; the value after it stays default."""
    return paint(text, COLOR_BLUE)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

_CODE_SPAN_RE = re.compile(r"(?<!`)`([^`\n]+?)`(?!`)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\w)\*(.+?)\*(?!\w)|(?<!\w)_(.+?)_(?!\w)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_RULE_RE = re.compile(r"^([-*_]\s*){3,}$")
_BULLET_RE = re.compile(r"^([-*+])\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")
_PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")


def _inline(text: str) -> str:
    """Strip inline markdown, keeping the readable text (and link targets)."""
    spans: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        spans.append(match.group(1))
        return f"\x00{len(spans) - 1}\x00"

    text = _CODE_SPAN_RE.sub(_stash, text)

    def _image(match: re.Match[str]) -> str:
        alt, url = match.group(1).strip(), match.group(2).strip()
        return f"{alt} ({url})" if alt else url

    text = _IMAGE_RE.sub(_image, text)
    text = _LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    text = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _STRIKE_RE.sub(r"\1", text)
    return _PLACEHOLDER_RE.sub(lambda m: spans[int(m.group(1))], text)


def render_markdown(text: str) -> str:
    """Render markdown to plain text (stdlib fallback when rich is missing).

    Handles headings, bold/italic/strikethrough, inline code, fenced code
    blocks (indented verbatim), links, images, bullets, numbered lists,
    blockquotes, and horizontal rules. Unknown constructs pass through with
    their markers stripped where safe, never with content lost.
    """
    unicode_ok = unicode_supported()
    lines = str(text or "").splitlines()
    out: list[str] = []
    in_fence = False
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            # Verbatim: no inline transforms inside a code block.
            out.append("    " + raw.rstrip())
            continue
        if not stripped:
            out.append("")
            continue
        lead = raw[: len(raw) - len(raw.lstrip())]
        match = _HEADING_RE.match(stripped)
        if match:
            # Uppercase the PLAIN text before _inline plants ANSI escapes:
            # .upper() after _inline corrupts escapes (e.g. \x1b[38;5;39m
            # becomes \x1b[38;5;39M = Delete Lines).
            raw_content = match.group(2).strip()
            if len(match.group(1)) <= 2:
                raw_content = raw_content.upper()
            out.append(_inline(raw_content))
            continue
        if _RULE_RE.match(stripped):
            out.append(branding.rule(unicode_ok=unicode_ok))
            continue
        if stripped.startswith(">"):
            bar = "│" if unicode_ok else "|"
            out.append(f"{bar} {_inline(stripped[1:].lstrip())}")
            continue
        match = _BULLET_RE.match(stripped)
        if match:
            bullet = "•" if unicode_ok else "*"
            out.append(f"{lead}{bullet} {_inline(match.group(2).strip())}")
            continue
        match = _NUMBERED_RE.match(stripped)
        if match:
            out.append(f"{lead}{match.group(1)}. {_inline(match.group(2).strip())}")
            continue
        out.append(_inline(raw.rstrip()))
    # Collapse runs of blank lines to a single one; drop leading/trailing.
    collapsed: list[str] = []
    for line in out:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip("\n")


def print_markdown(text: str, *, file=None) -> None:
    """Print markdown, via rich when available, else the plain renderer."""
    out = file if file is not None else sys.stdout
    if RICH_AVAILABLE:
        console = Console(file=out)
        console.print(Markdown(str(text or "")))
    else:
        print(render_markdown(text), file=out)


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------


class Spinner:
    """Indeterminate progress indicator; a silent no-op off-TTY.

    Usage::

        with tui.spinner("Thinking"):
            result = expensive_call()

    Frames use braille dots on capable terminals, ASCII otherwise, so Termux
    and legacy consoles degrade gracefully instead of printing mojibake.
    The worker thread is a daemon and is always joined on exit.
    """

    def __init__(self, message: str = "Working", *, file=None):
        self.message = message
        self.file = file if file is not None else sys.stderr
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _active(self) -> bool:
        try:
            return bool(self.file.isatty())
        except Exception:  # noqa: BLE001 - a weird stream means "not a tty"
            return False

    def _frames(self) -> list[str]:
        if unicode_supported():
            return ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        return ["-", "\\", "|", "/"]

    def _run(self) -> None:
        frames = self._frames()
        index = 0
        while not self._stop.wait(0.08):
            try:
                self.file.write(f"\r{frames[index % len(frames)]} {self.message}")
                self.file.flush()
            except Exception:  # noqa: BLE001 - never break the wrapped work
                return
            index += 1

    def update(self, message: str) -> None:
        """Change the message shown next to the spinner."""
        self.message = message

    def __enter__(self) -> "Spinner":
        if self._active():
            self._thread = threading.Thread(
                target=self._run, name="tui-spinner", daemon=True
            )
            self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        try:
            clear = "\r" + " " * (len(self.message) + 2) + "\r"
            self.file.write(clear)
            self.file.flush()
        except Exception:  # noqa: BLE001 - cosmetic only
            pass


def spinner(message: str = "Working", *, file=None) -> Spinner:
    """Create a :class:`Spinner` (use as a context manager)."""
    return Spinner(message, file=file)


# ---------------------------------------------------------------------------
# Selection menu
# ---------------------------------------------------------------------------


def _select_numeric(
    title: str,
    items: list[str],
    *,
    bullet: str,
    prompt: Callable[[str], str],
    _title_shown: bool = False,
) -> int:
    """Pilih via nomor. "" (Enter kosong) dan EOF = cancel (-1), bukan pilihan.

    Pilihan tak valid diulang; tidak pernah menebak pilihan pertama dari
    input kosong — itu yang bikin Enter refleks memilih opsi yang salah.
    Bila _title_shown True (judul sudah di-render arrow-picker sebelum gagal),
    judul tidak di-render ulang.
    """
    if not _title_shown:
        print(title)
    for index, option in enumerate(items, 1):
        print(f"  {index}. {bullet} {option}")
    while True:
        try:
            answer = prompt(f"Choice [1-{len(items)}] (empty = cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            return -1
        if not answer:
            return -1
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return int(answer) - 1
        print("  Invalid choice.")


def _select_arrows(
    items: list[str],
    *,
    start: int,
    read_key: Callable[[], str],
    bullet: str,
) -> int:
    """Picker panah (up/down + Enter). ESC / q / Ctrl-C = cancel (-1).

    Judul di-render SEKALI oleh pemanggil (select) sebelum picker jalan —
    bukan di sini — supaya fallback numerik tidak me-render ulang judul bila
    read_key raise di tengah jalan.
    """
    selected = max(0, min(start, len(items) - 1))
    chevron = branding.prompt_glyph()
    try:
        while True:
            for index, option in enumerate(items):
                cursor = paint(chevron, COLOR_BLUE) if index == selected else " "
                print(f"\r\033[K  {cursor} {bullet} {option}")
            key = read_key()
            if key == "up":
                selected = (selected - 1) % len(items)
            elif key == "down":
                selected = (selected + 1) % len(items)
            elif key == "enter":
                return selected
            elif key == "cancel":
                return -1
            print(f"\033[{len(items)}A", end="", flush=True)
    except KeyboardInterrupt:
        return -1
    finally:
        print()


def select(
    title: str,
    options: Sequence[str],
    *,
    start: int = 0,
    input_func: Callable[[str], str] | None = None,
    _stdin=None,
    _key_reader: Callable[[], str] | None = None,
    _raw_mode: Callable[[], contextlib.AbstractContextManager[None]] | None = None,
) -> int:
    """Arrow-key picker (up/down + Enter). Returns the index, -1 on cancel.

    Falls back to numeric input when stdin is not a TTY (redirected input,
    tests, automation) AND when the terminal does not support raw mode
    (entering raw_mode() fails) — never a traceback. In the numeric path,
    "" (empty Enter) and EOF count as cancel, never as the first option.
    ESC / q / Ctrl-C cancels. The underscore parameters exist so tests can
    drive both paths without a real terminal. The title is rendered exactly
    once: if the arrow picker fails midway, the numeric fallback does not
    render it again.
    """
    items = list(options)
    if not items:
        return -1
    stdin = _stdin if _stdin is not None else sys.stdin
    read_key = _key_reader if _key_reader is not None else read_menu_key
    raw = _raw_mode if _raw_mode is not None else raw_mode
    bullet = branding.marker()
    prompt = input_func if input_func is not None else input

    try:
        is_tty = bool(stdin.isatty())
    except Exception:  # noqa: BLE001 - a weird stream means "not a tty"
        is_tty = False

    if is_tty:
        # Judul di-render SEKALI di sini, sebelum arrow-picker jalan. Bila
        # read_key raise di tengah picker, fallback numerik tidak me-render
        # ulang judul (dulu: judul tampil 2x). Bila raw() gagal SEBELUM judul
        # tampil, fallback numerik tetap me-rendernya (flag False).
        title_shown = False
        try:
            with raw():
                print(title + "  (↑/↓ then Enter, Esc = cancel)")
                title_shown = True
                return _select_arrows(
                    items, start=start, read_key=read_key, bullet=bullet
                )
        except Exception:  # noqa: BLE001 - raw mode unsupported / read_key gagal; numeric fallback below
            pass
        return _select_numeric(title, items, bullet=bullet, prompt=prompt, _title_shown=title_shown)
    return _select_numeric(title, items, bullet=bullet, prompt=prompt)


# ---------------------------------------------------------------------------
# Cards, status bar, prompts
# ---------------------------------------------------------------------------


def card(
    rows: Mapping[str, str] | Sequence[tuple[str, str]],
    *,
    title: str | None = None,
) -> str:
    """Render an aligned key:value card (session info, status, model).

    Labels are padded to one width so values line up in a column; a thin
    rule (box-drawing on capable terminals, ASCII on legacy) frames the
    block. Returns the text — the caller decides where it goes.
    """
    items = (
        [(str(key), str(value)) for key, value in rows.items()]
        if isinstance(rows, Mapping)
        else [(str(key), str(value)) for key, value in rows]
    )
    if not items:
        return ""
    pad = max(len(name) for name, _ in items)
    rule = branding.rule(unicode_ok=unicode_supported())
    lines = [paint(rule, COLOR_DARK_BLUE)]
    if title:
        lines.append(f"  {paint(title, COLOR_BLUE)}")
        lines.append(paint(rule, COLOR_DARK_BLUE))
    for name, value in items:
        lines.append(f"  {label(f'{name:<{pad}} :')} {value}")
    lines.append(paint(rule, COLOR_DARK_BLUE))
    return "\n".join(lines)


def print_card(
    rows: Mapping[str, str] | Sequence[tuple[str, str]],
    *,
    title: str | None = None,
    file=None,
) -> None:
    """Print :func:`card` to ``file`` (stdout by default)."""
    print(card(rows, title=title), file=file if file is not None else sys.stdout)


def status_bar(parts: Sequence[str]) -> str:
    """Render a dim single-line status bar from short text parts."""
    sep = " • " if unicode_supported() else " - "
    return paint(sep.join(str(part) for part in parts), COLOR_DIM)


def render_prompt(who: str) -> str:
    """Render the ``<who> ❯ `` input prefix, colored when possible."""
    chevron = branding.prompt_glyph()
    return paint(f"{who} {chevron} ", COLOR_LIGHT_BLUE)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    """One slash command: name, help text, usage, and aliases."""

    name: str
    description: str
    usage: str = ""
    aliases: tuple[str, ...] = ()


@dataclass
class CommandRegistry:
    """Parse ``/command args`` input and describe the known commands."""

    _commands: dict[str, CommandSpec] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    def register(self, spec: CommandSpec) -> None:
        """Add a command; re-registering a name replaces it in place."""
        name = spec.name.lower()
        self._commands[name] = spec
        for alias in spec.aliases:
            self._commands[alias.lower()] = spec
        if name not in self._order:
            self._order.append(name)

    def get(self, name: str) -> CommandSpec | None:
        return self._commands.get((name or "").lower())

    def command_names(self) -> list[str]:
        """Slash command names in registration order, with a leading ``/``."""
        return ["/" + name for name in self._order if name in self._commands]

    def parse(self, text: str) -> tuple[CommandSpec, list[str]] | None:
        """Parse ``/name arg1 arg2``. None when not a known slash command."""
        stripped = (text or "").strip()
        if not stripped.startswith("/"):
            return None
        body = stripped[1:].strip()
        if not body:
            return None
        parts = _split_command_body(body)
        spec = self.get(parts[0])
        if spec is None:
            return None
        return spec, parts[1:]

    def is_command(self, text: str) -> bool:
        """True when ``text`` parses as a known slash command."""
        return self.parse(text) is not None

    def help_text(self) -> str:
        """Aligned ``/command — description`` list for /help output."""
        names = [name for name in self._order if name in self._commands]
        width = max((len(name) for name in names), default=0)
        lines = ["Slash commands:"]
        for name in names:
            spec = self._commands[name]
            alias_note = f" (also: {', '.join('/' + a for a in spec.aliases)})" if spec.aliases else ""
            lines.append(f"  /{name:<{width}}  {spec.description}{alias_note}")
        return "\n".join(lines)


def default_registry() -> CommandRegistry:
    """Build the registry with the CLI's standard slash commands."""
    registry = CommandRegistry()
    for spec in (
        CommandSpec("help", "Show this command list.", usage="/help"),
        CommandSpec("model", "Show the active provider model (switch with: zeline model).", usage="/model"),
        CommandSpec("status", "Show agent, model, provider, and session state.", usage="/status"),
        CommandSpec("goals", "List durable goals and recent progress.", usage="/goals"),
        CommandSpec("workers", "List background workers and their state.", usage="/workers"),
        CommandSpec("memory", "Search or list stored memories.", usage="/memory [query]"),
        CommandSpec("clear", "Clear the screen; the session keeps running.", usage="/clear"),
        CommandSpec("compact", "Summarize and compress the session history.", usage="/compact"),
        CommandSpec("undo", "Undo the last file change.", usage="/undo [--list]"),
        CommandSpec("editor", "Compose a message in your $EDITOR.", usage="/editor"),
        CommandSpec("stats", "Show token usage and cost summary.", usage="/stats [--by-day] [--reset]"),
        CommandSpec("export", "Export this session's transcript to a JSON file.", usage="/export [path]"),
        CommandSpec("tools", "List the native tools available to the agent.", usage="/tools"),
        CommandSpec(
            "exit",
            "End the chat session.",
            usage="/exit",
            aliases=("quit", "q"),
        ),
    ):
        registry.register(spec)
    return registry


#: The standard registry; the CLI wires handlers to these names.
DEFAULT_REGISTRY = default_registry()


def _split_command_body(body: str) -> list[str]:
    """Split a slash-command body into tokens, honoring shell-style quotes.

    ``shlex.split`` keeps ``/export "my file.json"`` as one argument.
    Bodies without any quote characters take the old plain-whitespace
    path, so unquoted Windows-style backslash paths (``C:\\data\\x.json``)
    are never mangled by POSIX backslash escaping. Unbalanced quotes fall
    back to plain whitespace splitting instead of raising.
    """
    if '"' not in body and "'" not in body:
        return body.split()
    try:
        return shlex.split(body)
    except ValueError:
        return body.split()


def parse_command(text: str) -> tuple[CommandSpec, list[str]] | None:
    """Parse ``text`` against the default command registry."""
    return DEFAULT_REGISTRY.parse(text)


def command_help() -> str:
    """The /help listing for the default command registry."""
    return DEFAULT_REGISTRY.help_text()


# ---------------------------------------------------------------------------
# Streaming responses
# ---------------------------------------------------------------------------

#: Minimum seconds between rich Live refreshes (~10 updates/second max).
_STREAM_THROTTLE_S = 0.1


def _rich_live_components():
    """Return ``(Console, Live, Markdown)`` when rich is usable, else None.

    Imported lazily so this module never raises ImportError when rich is
    missing — the caller just gets the plain-text path.
    """
    if not RICH_AVAILABLE:
        return None
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.markdown import Markdown
    except ImportError:
        return None
    return Console, Live, Markdown


def supports_stream() -> bool:
    """True when live streaming to stdout is sensible: a real TTY.

    Off-TTY (piped/redirected) output takes the old non-streaming path so
    logs and automation never see Live control sequences or half-rendered
    markdown.
    """
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001 - a weird stream means "not a tty"
        return False


class StreamRenderer:
    """Incremental renderer for one streaming agent reply.

    Feed text deltas with :meth:`feed`; the live view depends on the
    terminal:

    - rich installed *and* stdout is a TTY: a ``rich.live.Live`` block
      renders the accumulated text as Markdown, refreshed at most
      ~10x/second to avoid flicker. ``transient=True`` so the block is
      wiped when the turn ends.
    - TTY without rich: plain incremental writes to ``file``, flushed.

    :meth:`done` stops the Live block and returns the full accumulated
    text so the caller can do one final clean render via
    :func:`print_markdown`. Constructing or feeding is always safe without
    rich — no ImportError is ever raised. On a non-TTY the caller should
    not use this class at all; see :func:`supports_stream`.

    :attr:`rendered_incrementally` reports whether the plain no-rich path
    actually streamed deltas to the output — the caller skips the final
    render when it is True (the text is already on screen), but must still
    do the final render after a transient rich Live block, which is wiped
    when the turn ends.
    """

    def __init__(self, *, file=None):
        self.file = file if file is not None else sys.stdout
        self._buffer: list[str] = []
        self._live = None
        self._rich = None
        self._last_update = 0.0
        self._incremental_writes = 0
        self._finished = False
        if self._on_tty():
            self._rich = _rich_live_components()

    @property
    def rendered_incrementally(self) -> bool:
        """True when incremental deltas were actually written to the output.

        True only on the plain no-rich TTY path, where :meth:`feed` writes
        each delta to the file as it arrives. False when rich's transient
        Live block handled the rendering (the block is wiped at the end of
        the turn, so a final render is still needed), and False when no
        delta ever reached the output (non-TTY or empty feeds).
        """
        return self._incremental_writes > 0

    def _on_tty(self) -> bool:
        try:
            return bool(self.file.isatty())
        except Exception:  # noqa: BLE001 - a weird stream means "not a tty"
            return False

    def feed(self, delta: str) -> None:
        """Append one text delta and refresh the live view.

        Safe to call after :meth:`done` — it becomes a no-op so a stray
        late delta can never resurrect the live block or scribble over the
        final render.
        """
        if not delta or self._finished:
            return
        self._buffer.append(delta)
        if self._rich is not None:
            Console, Live, Markdown = self._rich
            now = time.monotonic()
            try:
                if self._live is None:
                    console = Console(file=self.file, force_terminal=True)
                    self._live = Live(
                        Markdown(""),
                        console=console,
                        refresh_per_second=10,
                        transient=True,
                    )
                    self._live.start()
                    self._last_update = now
                if now - self._last_update >= _STREAM_THROTTLE_S:
                    self._live.update(Markdown("".join(self._buffer)))
                    self._last_update = now
            except Exception:  # noqa: BLE001 - streaming is cosmetic; never break the turn
                pass
        elif self._on_tty():
            try:
                self.file.write(delta)
                self.file.flush()
                self._incremental_writes += 1
            except Exception:  # noqa: BLE001 - cosmetic only
                pass

    def done(self) -> str:
        """Stop the live view and return the full accumulated text."""
        self._finished = True
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:  # noqa: BLE001 - cosmetic only
                pass
            self._live = None
        return "".join(self._buffer)


# ---------------------------------------------------------------------------
# @file mention expansion
# ---------------------------------------------------------------------------

#: Max bytes read from a single @-mentioned file (100 KB, per spec).
_MENTION_CAP_BYTES = 100 * 1024

#: ``@path`` — the ``@`` must not be glued to a word char (kills emails like
#: ``user@example.com``). The path itself is POSIX-style: ``/`` separators,
#: ``~`` home, leading ``..`` (traversal is resolved and rejected/allowed by
#: the base-dir check, not by the lexer), ``.``/``-``/``+`` allowed. Trailing
#: ``.,;:!?`` is stripped as sentence punctuation, not part of the path.
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_~./][A-Za-z0-9_./~+-]*)")

#: Frame tags (openers AND closers) inside file content are neutralized so a
#: hostile file cannot break out of its data frame (mirrors the boundary-tag
#: hygiene of the ``<untrusted_external_data>`` blocks used elsewhere in the
#: codebase). Opening tags may carry attributes (``<file path="...">``); the
#: ``\\b`` keeps lookalikes like ``<files>`` untouched.
_FRAME_TAG_RE = re.compile(
    r"<\s*/?\s*(?:untrusted_external_data|file)\b[^<>]*>", re.IGNORECASE
)

#: Basename yang tampak seperti kredensial — hanya memicu WARNING (bukan blokir):
#: user meminta expand secara eksplisit, tapi accidental-paste private key ke
#: prompt adalah kesalahan yang mahal. Pola konservatif: nama file kunci
#: privat umum, .env, dan nama yang mengandung "credential"/"secret"/"token".
_SECRET_NAME_RE = re.compile(
    r"(^|[/\\])(\.env(\.|$)|id_rsa$|id_ed25519$|id_ecdsa$|id_dsa$"
    r"|\.pem$|\.key$|\.p12$|\.pfx$|\.jks$"
    r"|[^/\\]*credential[^/\\]*|[^/\\]*secret[^/\\]*|[^/\\]*\.token$)",
    re.IGNORECASE,
)


def _looks_like_secret(resolved: str) -> bool:
    """True bila basename path tampak seperti file kredensial.

    Heuristik nama-file saja (tidak membaca isi): false positive hanya
    menghasilkan satu baris warning, tidak memblokir expand.
    """
    return bool(_SECRET_NAME_RE.search(resolved))


def _code_regions(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` spans holding code blocks/spans; mentions inside them
    are literal text, never file references."""
    regions: list[tuple[int, int]] = []
    offset = 0
    in_fence = False
    fence_start = 0
    for line in text.splitlines(keepends=True):
        if line.strip().startswith("```"):
            if not in_fence:
                in_fence = True
                fence_start = offset
            else:
                in_fence = False
                regions.append((fence_start, offset + len(line)))
        elif not in_fence:
            for match in _CODE_SPAN_RE.finditer(line):
                regions.append((offset + match.start(), offset + match.end()))
        offset += len(line)
    if in_fence:  # unclosed fence: mask through the end of the text
        regions.append((fence_start, len(text)))
    return regions


def _neutralize_frame_breaks(content: str) -> str:
    """Neutralize frame tags (openers and closers) inside file content, looped
    to fixpoint.

    The replacement marker contains no ``<``, so no pass can introduce a new
    match — each pass strictly reduces the match count and the loop always
    terminates.

    Example (verified against the actual output)::

        >>> _neutralize_frame_breaks("</untrusted_external_data</untrusted_external_data>")
        '</untrusted_external_data[closing tag removed]'

    The inner closer is removed; what remains is a dangling
    ``</untrusted_external_data`` fragment with no ``>``, which cannot break
    out of the surrounding ``<file>``/``<untrusted_external_data>`` frame.
    """
    previous = None
    while previous != content:
        previous = content
        content = _FRAME_TAG_RE.sub("[closing tag removed]", content)
    return content


def expand_mentions(text: str, *, base_dir: str = ".") -> tuple[str, list[str]]:
    """Expand ``@path`` mentions into data-framed file contents.

    Each mention becomes::

        <file path="notes/todo.md">
        <untrusted_external_data>
        ...file content, data only...
        </untrusted_external_data>
        </file>

    The inner ``<untrusted_external_data>`` frame marks the content
    explicitly as DATA (never instructions), matching the convention used
    by the memory/sync paths elsewhere in the codebase.

    Rules:

    - ``@`` inside fenced code blocks or inline code spans is literal and
      ignored; ``@`` glued to a word (``user@example.com``) is not a
      mention.
    - Paths resolve against ``base_dir``; a mention that resolves OUTSIDE
      ``base_dir`` (``../../etc`` escapes, symlink tricks included) is
      rejected with a warning. ``../`` hops that stay inside the base are
      allowed.
    - Files over 100 KB, binary files (null byte / undecodable as UTF-8),
      directories, and missing paths are NOT expanded — a warning is
      returned and the mention is left untouched so the caller can print
      the warning locally instead of silently sending a broken prompt.
    - A filename that looks like a private key or secret (``id_rsa``,
      ``*.pem``, ``.env``, ...) still expands on explicit request, but a
      caution warning is returned so an accidental paste gets noticed.

    Returns ``(expanded_text, warnings)``.
    """
    text = text or ""
    regions = _code_regions(text)

    def _in_code(pos: int) -> bool:
        return any(start <= pos < end for start, end in regions)

    base = os.path.realpath(os.path.abspath(base_dir))
    warnings: list[str] = []
    out: list[str] = []
    last = 0
    for match in _MENTION_RE.finditer(text):
        if _in_code(match.start()):
            continue
        raw = match.group(1)
        path = raw.rstrip(".,;:!?")
        if not path:
            continue
        mention = f"@{path}"
        consumed = len(mention)
        if path.startswith("~"):
            candidate = os.path.expanduser(path)
        else:
            candidate = os.path.join(base_dir, path)
        resolved = os.path.realpath(os.path.abspath(candidate))
        if resolved != base and not resolved.startswith(base + os.sep):
            warnings.append(f"{mention}: resolves outside the working directory, skipped")
            continue
        if not os.path.exists(resolved):
            warnings.append(f"{mention}: file not found")
            continue
        if os.path.isdir(resolved):
            warnings.append(f"{mention}: is a directory, skipped")
            continue
        try:
            size = os.path.getsize(resolved)
        except OSError as exc:
            warnings.append(f"{mention}: unreadable ({exc})")
            continue
        if size > _MENTION_CAP_BYTES:
            warnings.append(
                f"{mention}: exceeds the 100KB expansion cap ({size} bytes), skipped"
            )
            continue
        try:
            with open(resolved, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            warnings.append(f"{mention}: unreadable ({exc})")
            continue
        if b"\x00" in data:
            warnings.append(f"{mention}: looks like a binary file, skipped")
            continue
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            warnings.append(f"{mention}: not valid UTF-8 text, skipped")
            continue
        if _looks_like_secret(resolved):
            warnings.append(
                f"{mention}: looks like a private key or secret file — expanded "
                "because you asked, but double-check before sending"
            )
        content = _neutralize_frame_breaks(content.rstrip("\n"))
        out.append(text[last : match.start()])
        out.append(
            f'<file path="{path}">\n'
            f"<untrusted_external_data>\n{content}\n</untrusted_external_data>\n</file>"
        )
        last = match.start() + consumed
    out.append(text[last:])
    return "".join(out), warnings


# ---------------------------------------------------------------------------
# Turn footer
# ---------------------------------------------------------------------------


def format_turn_footer(elapsed_s: float, tokens: int | None) -> str:
    """One-line turn summary, e.g. ``⏱ 4.2s • 12.4k tokens``.

    ``tokens=None`` (usage unknown) renders just the elapsed time. Counts
    >= 1000 use the compact ``12.4k`` form. On terminals without unicode
    support this degrades to ASCII: ``4.2s - 12.4k tokens``. Returns plain
    text — the caller dims it with ``paint(..., COLOR_DIM)``.
    """
    elapsed = f"{max(0.0, elapsed_s):.1f}s"
    if tokens is None:
        parts = [elapsed]
    elif tokens >= 1000:
        parts = [elapsed, f"{tokens / 1000:.1f}k tokens"]
    else:
        parts = [elapsed, f"{tokens} tokens"]
    if unicode_supported():
        return "⏱ " + " • ".join(parts)
    return " - ".join(parts)


__all__ = [
    "RICH_AVAILABLE",
    "COLOR_BLUE",
    "COLOR_DARK_BLUE",
    "COLOR_DIM",
    "COLOR_GREEN",
    "COLOR_LIGHT_BLUE",
    "COLOR_RED",
    "COLOR_RESET",
    "CommandRegistry",
    "CommandSpec",
    "DEFAULT_REGISTRY",
    "Spinner",
    "StreamRenderer",
    "card",
    "colors_enabled",
    "command_help",
    "default_registry",
    "expand_mentions",
    "format_turn_footer",
    "label",
    "paint",
    "parse_command",
    "print_card",
    "print_markdown",
    "render_markdown",
    "render_prompt",
    "select",
    "spinner",
    "status_bar",
    "supports_stream",
    "terminal_width",
    "unicode_supported",
]


def _smoke() -> None:  # pragma: no cover - manual sanity check
    """Quick visual check: python -m zeline.tui (needs a terminal)."""
    print(render_prompt("You"))
    print_markdown("# Hello\n\nSome **bold** and `code`:\n\n- one\n- two\n\n```\nx = 1\n```\n\n[docs](https://example.com)\n")
    print_card({"Agent": "Zeline", "Model": "demo"}, title="Session")
    print(status_bar(["model: demo", "workers: 0", "12:00"]))
    print(command_help())
    with spinner("Working"):
        import time

        time.sleep(0.6)
    choice = select("Pick one:", ["alpha", "beta", "gamma"])
    print(f"selected: {choice}")


if __name__ == "__main__":  # pragma: no cover
    _smoke()
