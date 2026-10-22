"""Minimal QR Code (model 2) generator — byte mode, versions 1-10, level M.

Zero dependencies. Implements just enough of the QR spec for short text
(URLs, tokens, WiFi strings): byte-mode encoding, Reed-Solomon error
correction over GF(256), masking with a fixed low-penalty mask (pattern 0),
and an ASCII-free PNG writer. Deliberately small: anything above ~290 bytes
at level M belongs in a dedicated library.
"""
from __future__ import annotations

import struct
import zlib

# --- GF(256) arithmetic -----------------------------------------------------

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        poly = [poly[j] ^ _gmul(_EXP[i], poly[j - 1]) if j else poly[0] ^ _gmul(_EXP[i], poly[-1] if False else poly[j - 1] if j else 0) for j in range(len(poly) + 1)]
    return poly


def _rs_generator_poly(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        # multiply poly by (x - a^i) -> (x + a^i in GF(2))
        new = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            new[j] ^= _gmul(coef, _EXP[i])
            new[j + 1] ^= coef
        poly = new
    return list(reversed(poly))


def _rs_encode(data: bytes, ecc_len: int) -> list[int]:
    gen = _rs_generator_poly(ecc_len)
    res = list(data) + [0] * ecc_len
    for i in range(len(data)):
        coef = res[i]
        if coef:
            for j in range(len(gen)):
                res[i + j] ^= _gmul(gen[j], coef)
    return res[len(data):]


# --- QR tables (level M) ----------------------------------------------------

# version: (total codewords, ecc codewords per block, block count)
_M_TABLE = {
    1: (26, 10, 1), 2: (44, 16, 1), 3: (70, 26, 1), 4: (100, 18, 2),
    5: (134, 24, 2), 6: (172, 16, 4), 7: (196, 18, 4), 8: (242, 22, 4),
    9: (292, 22, 5), 10: (346, 26, 5),
}
_ALIGNMENT = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
              6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}


def _byte_capacity(version: int) -> int:
    total, ecc_per_block, blocks = _M_TABLE[version]
    data_codewords = total - ecc_per_block * blocks
    # byte mode: 4-bit mode indicator + 8-bit length (versions 1-9) or 16-bit (10+)
    length_bits = 8 if version <= 9 else 16
    return (data_codewords * 8 - 4 - length_bits - 0) // 8


def _pick_version(data: bytes) -> int:
    for version in range(1, 11):
        if len(data) <= _byte_capacity(version):
            return version
    raise ValueError("data too long")


def _build_matrix(version: int, data: bytes) -> list[list[bool]]:
    size = 17 + version * 4
    matrix: list[list[bool | None]] = [[None] * size for _ in range(size)]

    def set_finder(row: int, col: int) -> None:
        # 7x7 finder: black border ring, white ring, 3x3 black core,
        # surrounded by a 1-module white separator.
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = row + r, col + c
                if 0 <= rr < size and 0 <= cc < size:
                    if r in (-1, 7) or c in (-1, 7):
                        matrix[rr][cc] = False  # separator: white
                    else:
                        edge = r in (0, 6) or c in (0, 6)
                        core = 2 <= r <= 4 and 2 <= c <= 4
                        matrix[rr][cc] = edge or core

    set_finder(0, 0)
    set_finder(0, size - 7)
    set_finder(size - 7, 0)

    # alignment patterns
    centers = _ALIGNMENT[version]
    for r in centers:
        for c in centers:
            if (r <= 8 and c <= 8) or (r <= 8 and c >= size - 9) or (r >= size - 9 and c <= 8):
                continue  # overlaps finder/separator area
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    if 0 <= r + dr < size and 0 <= c + dc < size and matrix[r + dr][c + dc] is None:
                        matrix[r + dr][c + dc] = max(abs(dr), abs(dc)) != 1

    # timing patterns
    for i in range(8, size - 8):
        bit = i % 2 == 0
        if matrix[6][i] is None:
            matrix[6][i] = bit
        if matrix[i][6] is None:
            matrix[i][6] = bit

    # dark module
    matrix[size - 8][8] = True

    # reserved format areas (filled later with mask 0 / level M = 00)
    for i in range(9):
        if matrix[8][i] is None:
            matrix[8][i] = False
        if matrix[i][8] is None:
            matrix[i][8] = False
    for i in range(8):
        if matrix[8][size - 1 - i] is None:
            matrix[8][size - 1 - i] = False
        if matrix[size - 1 - i][8] is None:
            matrix[size - 1 - i][8] = False

    # data placement: zigzag from bottom-right, skipping column 6
    total, ecc_per_block, blocks = _M_TABLE[version]
    data_codewords = total - ecc_per_block * blocks
    bits: list[int] = []
    # mode 0100 (byte), length
    bits.extend([0, 1, 0, 0])
    length_bits = 8 if version <= 9 else 16
    for i in range(length_bits - 1, -1, -1):
        bits.append((len(data) >> i) & 1)
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    # terminator + pad
    capacity_bits = data_codewords * 8
    bits.extend([0] * min(4, capacity_bits - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    while len(bits) < capacity_bits:
        bits.extend([1, 1, 1, 0, 1, 1, 0, 0])  # 0xEC
        if len(bits) < capacity_bits:
            bits.extend([0, 0, 0, 1, 0, 0, 0, 1])  # 0x11
    codewords = bytearray()
    for i in range(0, capacity_bits, 8):
        byte = 0
        for b in bits[i:i + 8]:
            byte = (byte << 1) | b
        codewords.append(byte)

    # interleave multi-block data + ecc
    ecc_len, n_blocks = ecc_per_block, blocks
    data_blocks = [codewords[i * (data_codewords // n_blocks):(i + 1) * (data_codewords // n_blocks)] for i in range(n_blocks)]
    ecc_blocks = [bytearray(_rs_encode(bytes(db), ecc_len)) for db in data_blocks]
    final = bytearray()
    max_data = max(len(db) for db in data_blocks)
    for i in range(max_data):
        for db in data_blocks:
            if i < len(db):
                final.append(db[i])
    for i in range(ecc_len):
        for eb in ecc_blocks:
            final.append(eb[i])

    # place bits
    bit_index = 0
    upward = True
    col = size - 1
    while col > 0:
        # Skip column 6 entirely: it holds the timing pattern.
        if col == 6:
            col -= 1
            continue
        col_left = col - 1 if col - 1 != 6 else col - 2
        for _row in range(size):
            row = (size - 1 - _row) if upward else _row
            for c in (col, col_left):
                if matrix[row][c] is None:
                    bit = False
                    if bit_index < len(final) * 8:
                        bit = bool((final[bit_index // 8] >> (7 - bit_index % 8)) & 1)
                        bit_index += 1
                    # mask 0: (row + col) % 2 == 0
                    if (row + c) % 2 == 0:
                        bit = not bit
                    matrix[row][c] = bit
        upward = not upward
        col -= 2

    # format info: level M (00), mask 0 (000) -> precomputed 0x5412
    fmt = 0x5412
    for i in range(15):
        bit = bool((fmt >> i) & 1)
        # copy 1 (around top-left finder)
        if i < 6:
            matrix[i][8] = bit
        elif i == 6:
            matrix[7][8] = bit
        elif i == 7:
            matrix[8][8] = bit
        elif i == 8:
            matrix[8][7] = bit
        else:
            matrix[8][14 - i] = bit
        # copy 2: bottom strip holds bits 14..8 descending, right strip bits 6..0
        if i < 7:
            matrix[size - 1 - i][8] = bool((fmt >> (14 - i)) & 1)
        elif i == 7:
            matrix[8][size - 8] = bit
        else:
            matrix[8][size - 7 + (14 - i)] = bit
    matrix[size - 8][8] = True  # dark module

    return [[bool(v) for v in row] for row in matrix]


def encode_png(data: str, scale: int = 8, quiet: int = 4) -> bytes:
    """Encode text as a QR code and return PNG bytes."""
    raw = data.encode("utf-8")
    version = _pick_version(raw)
    matrix = _build_matrix(version, raw)
    size = len(matrix)
    total = (size + quiet * 2) * scale

    # build grayscale row bytes (1 = white)
    def row_bytes(module_row: list[bool]) -> bytes:
        out = bytearray()
        line = [False] * (quiet * scale) + [m for m in module_row for _ in range(scale)] + [False] * (quiet * scale)
        line = [not v for v in line]  # True = white
        out.append(0)  # filter type 0
        for v in line:
            out.append(0xFF if v else 0x00)
        return bytes(out)

    border_row = bytes([0] + [0xFF] * total)
    raw_rows: list[bytes] = []
    for _ in range(quiet * scale):
        raw_rows.append(border_row)
    for mrow in matrix:
        for _ in range(scale):
            raw_rows.append(row_bytes(mrow))
    for _ in range(quiet * scale):
        raw_rows.append(border_row)

    raw_data = b"".join(raw_rows)
    assert len(raw_data) == total * (total + 1), f"PNG raw size mismatch: {len(raw_data)} != {total * (total + 1)}"

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", total, total, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw_data, 9)) + chunk(b"IEND", b""))
