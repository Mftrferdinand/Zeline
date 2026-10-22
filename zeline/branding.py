"""Zeline terminal identity — one wordmark, rendered safely on every terminal.

The interactive CLI, setup wizard, doctor screen, and gateway runner all share
this module so the product shows ONE identity. Two immutable wordmarks are
stored, never generated at runtime (no figlet dependency):

- WORDMARK_FULL: ansi_shadow block art. Its glyphs (full block + box drawing)
  all live in cp437, so they survive a UTF-8-reconfigured Windows console.
- WORDMARK_COMPACT: 'small' figlet, PURE ASCII. Doubles as the fallback for
  narrow terminals and for any stream whose encoding cannot represent a block.

Colour is opt-in and generated from the SAME source lines as plain mode, so a
NO_COLOR / TERM=dumb / non-TTY / CI context degrades to clean monochrome.
"""
from __future__ import annotations

import os
import sys

WORDMARK_FULL = (
    '███████╗███████╗██╗     ██╗███╗   ██╗███████╗',
    '╚══███╔╝██╔════╝██║     ██║████╗  ██║██╔════╝',
    '  ███╔╝ █████╗  ██║     ██║██╔██╗ ██║█████╗  ',
    ' ███╔╝  ██╔══╝  ██║     ██║██║╚██╗██║██╔══╝  ',
    '███████╗███████╗███████╗██║██║ ╚████║███████╗',
    '╚══════╝╚══════╝╚══════╝╚═╝╚═╝  ╚═══╝╚══════╝',
)
FULL_WIDTH = 45

# Compact ASCII wordmark for narrow screens (Termux, split panes). Pure ASCII so
# it survives a cp1252 console, 4 rows so it fits a phone without scrolling.
WORDMARK_COMPACT = (
    '  ____  _____ _     ___ _   _ _____ ',
    ' |_  / | ____| |   |_ _| \\ | | ____|',
    '  / /  |  _| | |    | ||  \\| |  _|  ',
    ' /___| |_____|_____|___|_| \\_|_____|',
)
COMPACT_WIDTH = 36


WORDMARK_AGENTIC = (
    ' █████╗  ██████╗ ███████╗███╗   ██╗████████╗██╗ ██████╗    █████╗ ██╗',
    '██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝██║██╔════╝   ██╔══██╗██║',
    '███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   ██║██║        ███████║██║',
    '██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ██║██║        ██╔══██║██║',
    '██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ██║╚██████╗   ██║  ██║██║',
    '╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝ ╚═════╝   ╚═╝  ╚═╝╚═╝',
)
AGENTIC_WIDTH = 69

# The one-line mark: 'ZELINE AGENTIC AI' in the small shadow font.
# Three rows so the whole lockup reads as a single block of text.
# Narrow fallback: the same lockup split over two stacked lines so a
# phone panel keeps every glyph instead of clipping the art.

# Same lockup at a 69-col budget: one line of text, four rows of art,
# used when the panel cannot fit the 74-col original.
TITLE_ONELINE = 'Z E L I N E   A G E N T I C   A I'
TITLE_ONELINE_WIDTH = 33

# Full block lockup: the original 6-row ZELINE glyph set, extended to the
# whole phrase so it reads as one wordmark on a wide terminal.
# Full 6-row block lockup. ZELINE sits on top and AGENTIC AI underneath,
# each word rendered one line wide in the original glyph set.
# 6-row block lockup: ZELINE over AGENTIC AI, both centred inside one
# shared width so the two blocks share a clean vertical axis.
# 6-row block logotype. ZELINE is letter-spaced to the exact width of
# AGENTIC AI so both lines form one rectangle, centred.
# 6-row block logotype. Both lines occupy the same 68-col box: ZELINE is
# letter-spaced to match AGENTIC AI so the lockup forms one rectangle.
# 6-row block lockup, no letter-spacing: ZELINE on top, AGENTIC AI
# below, both centred on shared width so the pair reads as one mark.
# The approved lockup: ZELINE in 6-row block art, with AGENTIC AI as a
# single wordmark line beneath it (rendered by cli_ui).
WORDMARK_TITLE = (
    '███████╗███████╗██╗     ██╗███╗   ██╗███████╗',
    '╚══███╔╝██╔════╝██║     ██║████╗  ██║██╔════╝',
    '  ███╔╝ █████╗  ██║     ██║██╔██╗ ██║█████╗  ',
    ' ███╔╝  ██╔══╝  ██║     ██║██║╚██╗██║██╔══╝  ',
    '███████╗███████╗███████╗██║██║ ╚████║███████╗',
    '╚══════╝╚══════╝╚══════╝╚═╝╚═╝  ╚═══╝╚══════╝',
)
TITLE_WIDTH = 45

CREDIT = "AGENTIC AI BY ZEROLINEAR"

# Vertical gradient for the full wordmark, top → bottom:
#   Putih (white) → Biru muda → Biru → Biru sedikit tua → Biru tua
# One 256-colour SGR per row. Luminance falls MONOTONICALLY (no bright row after
# a dark one, so it never "bounces" light→dark→light) and rows 2-6 are all
# blue-leaning (blue channel > green), so the fade never drifts into cyan/teal.
_GRADIENT = ("38;5;231", "38;5;153", "38;5;75", "38;5;39", "38;5;33", "38;5;26")
_COMPACT_COLOR = "38;5;111"
_SUBTITLE_COLOR = "38;5;244"
_RESET = "\033[0m"
_PAD = "  "


def prompt_glyph(unicode_ok: bool | None = None) -> str:
    """The chevron used for prompts (you ❯ / Zeline ❯), ASCII '>' on legacy.

    Gated on the same block-glyph capability as the wordmark, so a console that
    can render the banner also renders the chevron, and a legacy cp1252 stream
    degrades both together instead of printing mojibake.
    """
    if unicode_ok is None:
        unicode_ok = supports_unicode()
    return "\u276f" if unicode_ok else ">"


def marker(*, unicode_ok: bool | None = None) -> str:
    """Menu bullet '[•]', degrading to '[*]' on a console that can't encode •.

    Tests the bullet itself rather than the full-block glyph: U+2022 lives in
    cp1252, so a legacy Windows console still shows '[•]', and only a truly
    limited encoding falls back to the ASCII '[*]'. Either way the label after
    it stays meaningful — never a mojibake box.
    """
    if unicode_ok is None:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            "\u2022".encode(encoding)
            unicode_ok = True
        except (LookupError, UnicodeEncodeError):
            unicode_ok = False
    return "[\u2022]" if unicode_ok else "[*]"


def rule(width: int | None = None, *, unicode_ok: bool | None = None) -> str:
    """A thin horizontal rule sized to the wordmark, box-drawing or ASCII."""
    if unicode_ok is None:
        unicode_ok = supports_unicode()
    if width is None:
        width = FULL_WIDTH
    return (_PAD + ("\u2500" if unicode_ok else "-") * max(4, width))


def color_enabled(stream=None) -> bool:
    """ANSI only when the terminal explicitly supports it.

    NO_COLOR and TERM=dumb force plain output; otherwise colour needs either an
    explicit FORCE_COLOR or a real TTY. A redirected/CI stream stays monochrome.
    """
    if os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def supports_unicode(stream=None) -> bool:
    """True when the stream can encode the block glyph used by the full wordmark.

    A StringIO (encoding=None) is treated as UTF-8 capable, matching how the
    captured test output and real UTF-8 terminals behave.
    """
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "\u2588".encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def terminal_width(default: int = 80) -> int:
    try:
        return max(20, os.get_terminal_size().columns)
    except OSError:
        return default


def subtitle(version: str, *, unicode_ok: bool = True) -> str:
    bullet = "\u2022" if unicode_ok else "-"
    return f"{CREDIT} {bullet} v{version}"


def wordmark_lines(*, unicode_ok: bool, width: int) -> tuple[tuple[str, ...], bool]:
    """Pick the wordmark that fits: full block art, else the compact ASCII art."""
    use_full = unicode_ok and width >= FULL_WIDTH + len(_PAD) * 2
    return (WORDMARK_FULL if use_full else WORDMARK_COMPACT), use_full


def render(version: str, *, color=None, unicode_ok=None, width=None) -> list[str]:
    """Return the banner as a list of ready-to-print lines (no trailing newline).

    All three inputs are auto-detected when omitted, but overridable so tests are
    deterministic regardless of the real terminal.
    """
    if color is None:
        color = color_enabled()
    if unicode_ok is None:
        unicode_ok = supports_unicode()
    if width is None:
        width = terminal_width()

    art, use_full = wordmark_lines(unicode_ok=unicode_ok, width=width)
    lines: list[str] = [""]
    for index, row in enumerate(art):
        if not color:
            lines.append(_PAD + row)
        elif use_full:
            lines.append(f"{_PAD}\033[{_GRADIENT[index]}m{row}{_RESET}")
        else:
            lines.append(f"{_PAD}\033[{_COMPACT_COLOR}m{row}{_RESET}")

    sub = subtitle(version, unicode_ok=unicode_ok)
    if color:
        lines.append(f"{_PAD}\033[{_SUBTITLE_COLOR}m{sub}{_RESET}")
    else:
        lines.append(_PAD + sub)
    lines.append("")
    return lines


def banner(version: str, **kwargs) -> str:
    return "\n".join(render(version, **kwargs))
