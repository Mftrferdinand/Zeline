"""Tiny zero-dependency chart renderer: bar / line / pie → PNG.

Deliberately simple — enough pixels to make a report legible, not a
matplotlib replacement. Pure stdlib: values are mapped to rectangles on a
fixed canvas, and the PNG is written with the same minimal encoder the QR
module uses.
"""
from __future__ import annotations

import struct
import zlib

PALETTE = [
    (79, 129, 189), (192, 80, 77), (155, 187, 89), (128, 100, 162),
    (75, 172, 198), (247, 150, 70), (155, 187, 89), (128, 100, 162),
]
BG = (255, 255, 255)
FG = (60, 60, 60)
W, H = 640, 400
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 60, 20, 40, 50


def _png(width: int, height: int, pixels: list[list[tuple[int, int, int]]]) -> bytes:
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter 0
        for r, g, b in row:
            raw.extend((r, g, b))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _canvas() -> list[list[tuple[int, int, int]]]:
    return [[BG for _ in range(W)] for _ in range(H)]


def _px(c: list, x: int, y: int, color) -> None:
    if 0 <= y < H and 0 <= x < W:
        c[y][x] = color


def _rect(c, x0, y0, x1, y1, color) -> None:
    for y in range(max(0, y0), min(H, y1 + 1)):
        for x in range(max(0, x0), min(W, x1 + 1)):
            c[y][x] = color


def _line(c, x0, y0, x1, y1, color) -> None:
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for i in range(steps + 1):
        x = round(x0 + (x1 - x0) * i / steps)
        y = round(y0 + (y1 - y0) * i / steps)
        for dx in (0, 1):
            for dy in (0, 1):
                _px(c, x + dx, y + dy, color)


def _text5(c, text: str, x: int, y: int, color=FG) -> None:
    """3x5 bitmap font, uppercase + digits + basic punctuation."""
    F = {
        'A': ["010", "101", "111", "101", "101"], 'B': ["110", "101", "110", "101", "110"],
        'C': ["011", "100", "100", "100", "011"], 'D': ["110", "101", "101", "101", "110"],
        'E': ["111", "100", "110", "100", "111"], 'F': ["111", "100", "110", "100", "100"],
        'G': ["011", "100", "101", "101", "011"], 'H': ["101", "101", "111", "101", "101"],
        'I': ["111", "010", "010", "010", "111"], 'J': ["001", "001", "001", "101", "010"],
        'K': ["101", "101", "110", "101", "101"], 'L': ["100", "100", "100", "100", "111"],
        'M': ["101", "111", "111", "101", "101"], 'N': ["110", "101", "101", "101", "101"],
        'O': ["010", "101", "101", "101", "010"], 'P': ["110", "101", "110", "100", "100"],
        'Q': ["010", "101", "101", "110", "011"], 'R': ["110", "101", "110", "101", "101"],
        'S': ["011", "100", "010", "001", "110"], 'T': ["111", "010", "010", "010", "010"],
        'U': ["101", "101", "101", "101", "111"], 'V': ["101", "101", "101", "101", "010"],
        'W': ["101", "101", "111", "111", "101"], 'X': ["101", "101", "010", "101", "101"],
        'Y': ["101", "101", "010", "010", "010"], 'Z': ["111", "001", "010", "100", "111"],
        '0': ["111", "101", "101", "101", "111"], '1': ["010", "110", "010", "010", "111"],
        '2': ["111", "001", "111", "100", "111"], '3': ["111", "001", "111", "001", "111"],
        '4': ["101", "101", "111", "001", "001"], '5': ["111", "100", "111", "001", "111"],
        '6': ["111", "100", "111", "101", "111"], '7': ["111", "001", "001", "001", "001"],
        '8': ["111", "101", "111", "101", "111"], '9': ["111", "101", "111", "001", "111"],
        '%': ["101", "001", "010", "100", "101"], '.': ["000", "000", "000", "000", "010"],
        '-': ["000", "000", "111", "000", "000"], ':': ["000", "010", "000", "010", "000"],
        '/': ["001", "001", "010", "100", "100"], ' ': ["000", "000", "000", "000", "000"],
    }
    cx = x
    for ch in text.upper():
        glyph = F.get(ch, F[' '])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == '1':
                    _rect(c, cx + gx * 2, y + gy * 2, cx + gx * 2 + 1, y + gy * 2 + 1, color)
        cx += 8


def render_png(kind: str, rows: list[tuple[str, float]], title: str = "") -> bytes:
    c = _canvas()
    plot_x0, plot_y0 = MARGIN_L, MARGIN_T
    plot_x1, plot_y1 = W - MARGIN_R, H - MARGIN_B
    max_val = max(v for _, v in rows) or 1.0
    min_val = min(0.0, min(v for _, v in rows))
    span = (max_val - min_val) or 1.0

    if title:
        _text5(c, title, MARGIN_L, 10)

    # axes
    _line(c, plot_x0, plot_y0, plot_x0, plot_y1, FG)
    _line(c, plot_x0, plot_y1, plot_x1, plot_y1, FG)

    n = len(rows)
    if kind == "bar":
        slot = (plot_x1 - plot_x0) / n
        bar_w = max(4, int(slot * 0.6))
        for i, (label, value) in enumerate(rows):
            h = int((value - min_val) / span * (plot_y1 - plot_y0))
            color = PALETTE[i % len(PALETTE)]
            _rect(c, int(plot_x0 + i * slot + (slot - bar_w) / 2), plot_y1 - h,
                  int(plot_x0 + i * slot + (slot - bar_w) / 2) + bar_w, plot_y1, color)
            _text5(c, label[:8], int(plot_x0 + i * slot + 2), plot_y1 + 6)
    elif kind == "line":
        slot = (plot_x1 - plot_x0) / max(1, n - 1)
        points = []
        for i, (label, value) in enumerate(rows):
            x = int(plot_x0 + i * slot)
            y = int(plot_y1 - (value - min_val) / span * (plot_y1 - plot_y0))
            points.append((x, y))
            _rect(c, x - 2, y - 2, x + 2, y + 2, PALETTE[0])
            _text5(c, label[:6], x - 8, plot_y1 + 6)
        for (xa, ya), (xb, yb) in zip(points, points[1:]):
            _line(c, xa, ya, xb, yb, PALETTE[0])
    else:  # pie
        import math
        cx, cy, r = (plot_x0 + plot_x1) // 2, (plot_y0 + plot_y1) // 2, min(
            plot_x1 - plot_x0, plot_y1 - plot_y0) // 2 - 10
        total = sum(v for _, v in rows) or 1.0
        angle = -math.pi / 2
        for i, (label, value) in enumerate(rows):
            sweep = value / total * 2 * math.pi
            color = PALETTE[i % len(PALETTE)]
            steps = max(8, int(sweep / 0.01))
            for s in range(steps + 1):
                a = angle + sweep * s / steps
                # fill wedge by drawing rays
                for rr in range(0, r, 2):
                    _px(c, int(cx + rr * math.cos(a)), int(cy + rr * math.sin(a)), color)
            angle += sweep
        # legend
        ly = MARGIN_T
        for i, (label, _) in enumerate(rows[:8]):
            _rect(c, W - 130, ly, W - 122, ly + 6, PALETTE[i % len(PALETTE)])
            _text5(c, label[:10], W - 116, ly - 2)
            ly += 12

    # y-axis ticks
    for t in range(5):
        v = min_val + span * t / 4
        y = int(plot_y1 - t / 4 * (plot_y1 - plot_y0))
        _text5(c, f"{v:.0f}", 8, y - 4)

    return _png(W, H, c)
