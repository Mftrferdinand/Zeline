# Excalidraw

> Hand-drawn Excalidraw JSON diagrams (arch, flow, seq).

Create diagrams by writing standard Excalidraw element JSON and saving as `.excalidraw` files. These files can be drag-and-dropped onto [excalidraw.com](https://excalidraw.com) for viewing and editing. No accounts, no API keys, no rendering libraries -- just JSON.

## When to use

Generate `.excalidraw` files for architecture diagrams, flowcharts, sequence diagrams, concept maps, and more. Files can be opened at excalidraw.com or uploaded for shareable links.

## Workflow

1. **Load this skill** (you already did)
2. **Write the elements JSON** -- an array of Excalidraw element objects
3. **Save the file** using `write_file` to create a `.excalidraw` file
4. **Optionally upload** for a shareable link using `scripts/upload.py` via `terminal`

### Saving a Diagram

Wrap your elements array in the standard `.excalidraw` envelope and save with `write_file`:

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "zeline",
  "elements": [ ...your elements array here... ],
  "appState": {
    "viewBackgroundColor": "#ffffff"
  }
}
```

Save to any path, e.g. `~/diagrams/my_diagram.excalidraw`.

### Uploading for a Shareable Link

Run the upload script (located in this skill's `scripts/` directory) via terminal:

```bash
python skills/diagramming/excalidraw/scripts/upload.py ~/diagrams/my_diagram.excalidraw
```

This uploads to excalidraw.com (no account needed) and prints a shareable URL. Requires the `cryptography` pip package (`pip install cryptography`).

---

## Element Format Reference

### Required Fields (all elements)
`type`, `id` (unique string), `x`, `y`, `width`, `height`

### Defaults (skip these -- they're applied automatically)
- `strokeColor`: `"#1e1e1e"`
- `backgroundColor`: `"transparent"`
- `fillStyle`: `"solid"`
- `strokeWidth`: `2`
- `roughness`: `1` (hand-drawn look)
- `opacity`: `100`

Canvas background is white.

### Element Types

**Rectangle**:
```json
{ "type": "rectangle", "id": "r1", "x": 100, "y": 100, "width": 200, "height": 100 }
```
- `roundness: { "type": 3 }` for rounded corners
- `backgroundColor: "#a5d8ff"`, `fillStyle: "solid"` for filled

**Ellipse**:
```json
{ "type": "ellipse", "id": "e1", "x": 100, "y": 100, "width": 150, "height": 150 }
```

**Diamond**:
```json
{ "type": "diamond", "id": "d1", "x": 100, "y": 100, "width": 150, "height": 150 }
```

**Labeled shape (container binding)** -- create a text element bound to the shape:

> **WARNING:** Do NOT use `"label": { "text": "..." }` on shapes. This is NOT a valid
> Excalidraw property and will be silently ignored, producing blank shapes. You MUST
> use the container binding approach below.

The shape needs `boundElements` listing the text, and the text needs `containerId` pointing back:
```json
{ "type": "rectangle", "id": "r1", "x": 100, "y": 100, "width": 200, "height": 80,
  "roundness": { "type": 3 }, "backgroundColor": "#a5d8ff", "fillStyle": "solid",
  "boundElements": [{ "id": "t_r1", "type": "text" }] },
{ "type": "text", "id": "t_r1", "x": 105, "y": 110, "width": 190, "height": 25,
  "text": "Hello", "fontSize": 20, "fontFamily": 1, "strokeColor": "#1e1e1e",
  "textAlign": "center", "verticalAlign": "middle",
  "containerId": "r1", "originalText": "Hello", "autoResize": true }
```
- Works on rectangle, ellipse, diamond
- Text is auto-centered by Excalidraw when `containerId` is set
- The text `x`/`y`/`width`/`height` are approximate -- Excalidraw recalculates them on load
- `originalText` should match `text`
- Always include `fontFamily: 1` (Virgil/hand-drawn font)

**Labeled arrow** -- same container binding approach:
```json
{ "type": "arrow", "id": "a1", "x": 300, "y": 150, "width": 200, "height": 0,
  "points": [[0,0],[200,0]], "endArrowhead": "arrow",
  "boundElements": [{ "id": "t_a1", "type": "text" }] },
{ "type": "text", "id": "t_a1", "x": 370, "y": 130, "width": 60, "height": 20,
  "text": "connects", "fontSize": 16, "fontFamily": 1, "strokeColor": "#1e1e1e",
  "textAlign": "center", "verticalAlign": "middle",
  "containerId": "a1", "originalText": "connects", "autoResize": true }
```

**Standalone text** (titles and annotations only -- no container):
```json
{ "type": "text", "id": "t1", "x": 150, "y": 138, "text": "Hello", "fontSize": 20,
  "fontFamily": 1, "strokeColor": "#1e1e1e", "originalText": "Hello", "autoResize": true }
```
- `x` is the LEFT edge. To center at position `cx`: `x = cx - (text.length * fontSize * 0.5) / 2`
- Do NOT rely on `textAlign` or `width` for positioning

**Arrow**:
```json
{ "type": "arrow", "id": "a1", "x": 300, "y": 150, "width": 200, "height": 0,
  "points": [[0,0],[200,0]], "endArrowhead": "arrow" }
```
- `points`: `[dx, dy]` offsets from element `x`, `y`
- `endArrowhead`: `null` | `"arrow"` | `"bar"` | `"dot"` | `"triangle"`
- `strokeStyle`: `"solid"` (default) | `"dashed"` | `"dotted"`

### Arrow Bindings (connect arrows to shapes)

```json
{
  "type": "arrow", "id": "a1", "x": 300, "y": 150, "width": 150, "height": 0,
  "points": [[0,0],[150,0]], "endArrowhead": "arrow",
  "startBinding": { "elementId": "r1", "fixedPoint": [1, 0.5] },
  "endBinding": { "elementId": "r2", "fixedPoint": [0, 0.5] }
}
```

`fixedPoint` coordinates: `top=[0.5,0]`, `bottom=[0.5,1]`, `left=[0,0.5]`, `right=[1,0.5]`

### Drawing Order (z-order)
- Array order = z-order (first = back, last = front)
- Emit progressively: background zones → shape → its bound text → its arrows → next shape
- BAD: all rectangles, then all texts, then all arrows
- GOOD: bg_zone → shape1 → text_for_shape1 → arrow1 → arrow_label_text → shape2 → text_for_shape2 → ...
- Always place the bound text element immediately after its container shape

### Sizing Guidelines

**Font sizes:**
- Minimum `fontSize`: **16** for body text, labels, descriptions
- Minimum `fontSize`: **20** for titles and headings
- Minimum `fontSize`: **14** for secondary annotations only (sparingly)
- NEVER use `fontSize` below 14

**Element sizes:**
- Minimum shape size: 120x60 for labeled rectangles/ellipses
- Leave 20-30px gaps between elements minimum
- Prefer fewer, larger elements over many tiny ones

### Color Palette

See `references/colors.md` for full color tables. Quick reference:

| Use | Fill Color | Hex |
|-----|-----------|-----|
| Primary / Input | Light Blue | `#a5d8ff` |
| Success / Output | Light Green | `#b2f2bb` |
| Warning / External | Light Orange | `#ffd8a8` |
| Processing / Special | Light Purple | `#d0bfff` |
| Error / Critical | Light Red | `#ffc9c9` |
| Notes / Decisions | Light Yellow | `#fff3bf` |
| Storage / Data | Light Teal | `#c3fae8` |

### Tips
- Use the color palette consistently across the diagram
- **Text contrast is CRITICAL** -- never use light gray on white backgrounds. Minimum text color on white: `#757575`
- Do NOT use emoji in text -- they don't render in Excalidraw's font
- For dark mode diagrams, see `references/dark-mode.md`
- For larger examples, see `references/examples.md`




---

## Lampiran: `references/colors.md`

# Excalidraw Color Palette

Use these colors consistently across diagrams.

## Primary Colors (for strokes, arrows, and accents)

| Name | Hex | Use |
|------|-----|-----|
| Blue | `#4a9eed` | Primary actions, links, data series 1 |
| Amber | `#f59e0b` | Warnings, highlights, data series 2 |
| Green | `#22c55e` | Success, positive, data series 3 |
| Red | `#ef4444` | Errors, negative, data series 4 |
| Purple | `#8b5cf6` | Accents, special items, data series 5 |
| Pink | `#ec4899` | Decorative, data series 6 |
| Cyan | `#06b6d4` | Info, secondary, data series 7 |
| Lime | `#84cc16` | Extra, data series 8 |

## Pastel Fills (for shape backgrounds)

| Color | Hex | Good For |
|-------|-----|----------|
| Light Blue | `#a5d8ff` | Input, sources, primary nodes |
| Light Green | `#b2f2bb` | Success, output, completed |
| Light Orange | `#ffd8a8` | Warning, pending, external |
| Light Purple | `#d0bfff` | Processing, middleware, special |
| Light Red | `#ffc9c9` | Error, critical, alerts |
| Light Yellow | `#fff3bf` | Notes, decisions, planning |
| Light Teal | `#c3fae8` | Storage, data, memory |
| Light Pink | `#eebefa` | Analytics, metrics |

## Background Zones (use with opacity: 30-35 for layered diagrams)

| Color | Hex | Good For |
|-------|-----|----------|
| Blue zone | `#dbe4ff` | UI / frontend layer |
| Purple zone | `#e5dbff` | Logic / agent layer |
| Green zone | `#d3f9d8` | Data / tool layer |

## Text Contrast Rules

- **On white backgrounds**: minimum text color is `#757575`. Default `#1e1e1e` is best.
- **Colored text on light fills**: use dark variants (`#15803d` not `#22c55e`, `#2563eb` not `#4a9eed`)
- **White text**: only on dark backgrounds (`#9a5030` not `#c4795b`)
- **Never**: light gray (`#b0b0b0`, `#999`) on white -- unreadable



---

## Lampiran: `references/dark-mode.md`

# Excalidraw Dark Mode Diagrams

To create a dark-themed diagram, use a massive dark background rectangle as the **first element** in the array. Make it large enough to cover any viewport:

```json
{
  "type": "rectangle", "id": "darkbg",
  "x": -4000, "y": -3000, "width": 10000, "height": 7500,
  "backgroundColor": "#1e1e2e", "fillStyle": "solid",
  "strokeColor": "transparent", "strokeWidth": 0
}
```

Then use the following color palettes for elements on the dark background.

## Text Colors (on dark)

| Color | Hex | Use |
|-------|-----|-----|
| White | `#e5e5e5` | Primary text, titles |
| Muted | `#a0a0a0` | Secondary text, annotations |
| NEVER | `#555` or darker | Invisible on dark bg! |

## Shape Fills (on dark)

| Color | Hex | Good For |
|-------|-----|----------|
| Dark Blue | `#1e3a5f` | Primary nodes |
| Dark Green | `#1a4d2e` | Success, output |
| Dark Purple | `#2d1b69` | Processing, special |
| Dark Orange | `#5c3d1a` | Warning, pending |
| Dark Red | `#5c1a1a` | Error, critical |
| Dark Teal | `#1a4d4d` | Storage, data |

## Stroke and Arrow Colors (on dark)

Use the standard Primary Colors from the main color palette -- they're bright enough on dark backgrounds:
- Blue `#4a9eed`, Amber `#f59e0b`, Green `#22c55e`, Red `#ef4444`, Purple `#8b5cf6`

For subtle shape borders, use `#555555`.

## Example: Dark mode labeled rectangle

Use container binding (NOT the `"label"` property, which doesn't work). On dark backgrounds, set text `strokeColor` to `"#e5e5e5"` so it's visible:

```json
[
  {
    "type": "rectangle", "id": "r1",
    "x": 100, "y": 100, "width": 200, "height": 80,
    "backgroundColor": "#1e3a5f", "fillStyle": "solid",
    "strokeColor": "#4a9eed", "strokeWidth": 2,
    "roundness": { "type": 3 },
    "boundElements": [{ "id": "t_r1", "type": "text" }]
  },
  {
    "type": "text", "id": "t_r1",
    "x": 105, "y": 120, "width": 190, "height": 25,
    "text": "Dark Node", "fontSize": 20, "fontFamily": 1,
    "strokeColor": "#e5e5e5",
    "textAlign": "center", "verticalAlign": "middle",
    "containerId": "r1", "originalText": "Dark Node", "autoResize": true
  }
]
```

Note: For standalone text elements on dark backgrounds, always set `"strokeColor": "#e5e5e5"` explicitly. The default `#1e1e1e` is invisible on dark.




---

## Lampiran: `scripts/upload.py`

```py
#!/usr/bin/env python3
"""
Upload an .excalidraw file to excalidraw.com and print a shareable URL.

No account required. The diagram is encrypted client-side (the user-GCM) before
upload -- the encryption key is embedded in the URL fragment, so the server
never sees plaintext.

Requirements:
    pip install cryptography

Usage:
    python upload.py <path-to-file.excalidraw>

Example:
    python upload.py ~/diagrams/architecture.excalidraw
    # prints: https://excalidraw.com/#json=abc123,encryptionKeyHere
"""

import json
import os
import struct
import sys
import zlib
import base64
import urllib.request

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("Error: 'cryptography' package is required for upload.")
    print("Install it with: pip install cryptography")
    sys.exit(1)

# Excalidraw public upload endpoint (no auth needed)
UPLOAD_URL = "https://json.excalidraw.com/api/v2/post/"


def concat_buffers(*buffers: bytes) -> bytes:
    """
    Build the Excalidraw v2 concat-buffers binary format.

    Layout: [version=1 (4B big-endian)] then for each buffer:
            [length (4B big-endian)] [data bytes]
    """
    parts = [struct.pack(">I", 1)]  # version = 1
    for buf in buffers:
        parts.append(struct.pack(">I", len(buf)))
        parts.append(buf)
    return b"".join(parts)


def upload(excalidraw_json: str) -> str:
    """
    Encrypt and upload Excalidraw JSON to excalidraw.com.

    Args:
        excalidraw_json: The full .excalidraw file content as a string.

    Returns:
        Shareable URL string.
    """
    # 1. Inner payload: concat_buffers(file_metadata, data)
    file_metadata = json.dumps({}).encode("utf-8")
    data_bytes = excalidraw_json.encode("utf-8")
    inner_payload = concat_buffers(file_metadata, data_bytes)

    # 2. Compress with zlib
    compressed = zlib.compress(inner_payload)

    # 3. the user-GCM 128-bit encrypt
    raw_key = os.urandom(16)   # 128-bit key
    iv = os.urandom(12)        # 12-byte nonce
    aesgcm = AESGCM(raw_key)
    encrypted = aesgcm.encrypt(iv, compressed, None)

    # 4. Encoding metadata
    encoding_meta = json.dumps({
        "version": 2,
        "compression": "pako@1",
        "encryption": "the user-GCM",
    }).encode("utf-8")

    # 5. Outer payload: concat_buffers(encoding_meta, iv, encrypted)
    payload = concat_buffers(encoding_meta, iv, encrypted)

    # 6. Upload
    req = urllib.request.Request(UPLOAD_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Upload failed with HTTP {resp.status}")
        result = json.loads(resp.read().decode("utf-8"))

    file_id = result.get("id")
    if not file_id:
        raise RuntimeError(f"Upload returned no file ID. Response: {result}")

    # 7. Key as base64url (JWK 'k' format, no padding)
    key_b64 = base64.urlsafe_b64encode(raw_key).rstrip(b"=").decode("ascii")

    return f"https://excalidraw.com/#json={file_id},{key_b64}"


def main():
    if len(sys.argv) < 2:
        print("Usage: python upload.py <path-to-file.excalidraw>")
        sys.exit(1)

    file_path = sys.argv[1]

    if not os.path.isfile(file_path):
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Basic validation: should be valid JSON with an "elements" key
    try:
        doc = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"Error: File is not valid JSON: {e}")
        sys.exit(1)

    if "elements" not in doc:
        print("Warning: File does not contain an 'elements' key. Uploading anyway.")

    url = upload(content)
    print(url)


if __name__ == "__main__":
    main()

```

---

## Catatan adaptasi Zeline
- File pendukung tidak di-inline (terlalu besar/biner): references/examples.md.

