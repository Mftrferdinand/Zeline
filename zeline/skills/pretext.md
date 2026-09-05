# Pretext

> Use when building creative browser demos with @chenglou/pretext — DOM-free text layout for ASCII art, typographic flow around obstacles, text-as-geometry games, kinetic typography, and text-powered generative art. Produces single-file HTML demos by default.

## Overview

[`@chenglou/pretext`](https://github.com/chenglou/pretext) is a 15KB zero-dependency TypeScript library by Cheng Lou (React core, ReasonML, Midjourney) for **DOM-free multiline text measurement and layout**. It does one thing: given `(text, font, width)`, return the line breaks, per-line widths, per-grapheme positions, and total height — all via canvas measurement, no reflow.

That sounds like plumbing. It is not. Because it is fast and geometric, it is a **creative primitive**: you can reflow paragraphs around a moving sprite at 60fps, build games whose level geometry is made of real words, drive ASCII logos through prose, shatter text into particles with exact per-grapheme starting positions, or pack shrink-wrapped multiline UI without any `getBoundingClientRect` thrash.

This skill exists so Zeline can make **cool demos** with it — the kind people post to X. See `pretext.cool` and `chenglou.me/pretext` for the community demo corpus.

## When to Use

Use when the user asks for:
- A "pretext demo" / "cool pretext thing" / "text-as-X"
- Text flowing around a moving shape (hero sections, editorial layouts, animated long-form pages)
- ASCII-art effects using **real words or prose**, not monospace rasters
- Games where the playfield / obstacles / bricks are made of text (Tetris-from-letters, Breakout-of-prose)
- Kinetic typography with per-glyph physics (shatter, scatter, flock, flow)
- Typographic generative art, especially with non-Latin scripts or mixed scripts
- Multiline "shrink-wrap" UI (smallest container width that still fits the text)
- Anything that would require knowing line breaks *before* rendering

Don't use for:
- Static SVG/HTML pages where CSS already solves layout — just use CSS
- Rich text editors, general inline formatting engines (pretext is intentionally narrow)
- Image → text (use `ascii-art` / `ascii-video` skills)
- Pure canvas generative art with no text role — use `p5js`

## Creative Standard

This is visual art rendered in a browser. Pretext returns numbers; **you** draw the thing.

- **Don't ship a "hello world" demo.** The `hello-orb-flow.html` template is the *starting* point. Every delivered demo must add intentional color, motion, composition, and one visual detail the user didn't ask for but will appreciate.
- **Dark backgrounds, warm cores, considered palette.** Classic amber-on-black (CRT / terminal) works, but so do cold-white-on-charcoal (editorial) and desaturated pastels (risograph). Pick one and commit.
- **Proportional fonts are the point.** Pretext's whole vibe is "not monospaced" — lean into it. Use Iowan Old Style, Inter, JetBrains Mono, Helvetica Neue, or a variable font. Never default sans.
- **Real source/text, not lorem ipsum.** The corpus should mean something. Short manifestos, poetry, real source code, a found text, the library's own README — never `lorem ipsum`.
- **First-paint excellence.** No loading states, no blank frames. The demo must look shippable the instant it opens.

## Stack

Single self-contained HTML file per demo. No build step.

| Layer | Tool | Purpose |
|-------|------|---------|
| Core | `@chenglou/pretext` via `esm.sh` CDN | Text measurement + line layout |
| Render | HTML5 Canvas 2D | Glyph rendering, per-frame composition |
| Segmentation | `Intl.Segmenter` (built-in) | Grapheme splitting for emoji / CJK / combining marks |
| Interaction | Raw DOM events | Mouse / touch / wheel — no framework |

```html
<script type="module">
import {
  prepare, layout,                   // use-case 1: simple height
  prepareWithSegments, layoutWithLines,  // use-case 2a: fixed-width lines
  layoutNextLineRange, materializeLineRange, // use-case 2b: streaming / variable width
  measureLineStats, walkLineRanges,  // stats without string allocation
} from "https://esm.sh/@chenglou/pretext@0.0.6";
</script>
```

Pin the version. `@0.0.6` at time of writing — check [npm](https://www.npmjs.com/package/@chenglou/pretext) for the latest if demo behavior is off.

## The Two Use Cases

Almost everything reduces to one of these two shapes. Learn both.

### Use-case 1 — measure, then render with CSS/DOM

```js
const prepared = prepare(text, "16px Inter");
const { height, lineCount } = layout(prepared, 320, 20);
```

You still let the browser draw the text. Pretext just tells you how tall the box will be at a given width, **without** a DOM read. Use for:
- Virtualized lists where rows contain wrapping text
- Masonry with precise card heights
- "Does this label fit?" dev-time checks
- Preventing layout shift when remote text loads

**Keep `font` and `letterSpacing` exactly in sync with your CSS.** The canvas `ctx.font` format (e.g. `"16px Inter"`, `"500 17px 'JetBrains Mono'"`) must match the rendered CSS, or measurements drift.

### Use-case 2 — measure *and* render yourself

```js
const prepared = prepareWithSegments(text, FONT);
const { lines } = layoutWithLines(prepared, 320, 26);
for (let i = 0; i < lines.length; i++) {
  ctx.fillText(lines[i].text, 0, i * 26);
}
```

This is where the creative work lives. You own the drawing, so you can:
- Render to canvas, SVG, WebGL, or any coordinate system
- Substitute per-glyph transforms (rotation, jitter, scale, opacity)
- Use line metadata (width, grapheme positions) as geometry

For **variable-width-per-line** flow (text around a shape, text in a donut band, text in a non-rectangular column):

```js
let cursor = { segmentIndex: 0, graphemeIndex: 0 };
let y = 0;
while (true) {
  const lineWidth = widthAtY(y);  // your function: how wide is the corridor at this y?
  const range = layoutNextLineRange(prepared, cursor, lineWidth);
  if (!range) break;
  const line = materializeLineRange(prepared, range);
  ctx.fillText(line.text, leftEdgeAtY(y), y);
  cursor = range.end;
  y += lineHeight;
}
```

This is the most important pattern in the whole library. It's what unlocks "text flowing around a dragged sprite" — the demo that went viral on X.

### Helpers worth knowing

- `measureLineStats(prepared, maxWidth)` → `{ lineCount, maxLineWidth }` — the widest line, i.e. multiline shrink-wrap width.
- `walkLineRanges(prepared, maxWidth, callback)` — iterate lines without allocating strings. Use for stats/physics over graphemes when you don't need the characters.
- `@chenglou/pretext/rich-inline` — the same system but for paragraphs mixing fonts / chips / mentions. Import from the subpath.

## Demo Recipe Patterns

The community corpus (see `references/patterns.md`) clusters into a handful of strong patterns. Pick one and riff — don't invent a new category unless asked.

| Pattern | Key API | Example idea |
|---|---|---|
| **Reflow around obstacle** | `layoutNextLineRange` + per-row width function | Editorial paragraph that parts around a dragged cursor sprite |
| **Text-as-geometry game** | `layoutWithLines` + per-line collision rects | Breakout where each brick is a measured word |
| **Shatter / particles** | `walkLineRanges` → per-grapheme (x,y) → physics | Sentence that explodes into letters on click |
| **ASCII obstacle typography** | `layoutNextLineRange` + measured per-row obstacle spans | Bitmap ASCII logo, shape morphs, and draggable wire objects that make text open around their actual geometry |
| **Editorial multi-column** | `layoutNextLineRange` per column + shared cursor | Animated magazine spread with pull quotes |
| **Kinetic type** | `layoutWithLines` + per-line transform over time | Star Wars crawl, wave, bounce, glitch |
| **Multiline shrink-wrap** | `measureLineStats` | Quote card that auto-sizes to its tightest container |

See `templates/donut-orbit.html` and `templates/hello-orb-flow.html` for working single-file starters.

## Workflow

1. **Pick a pattern** from the table above based on the user's brief.
2. **Start from a template**:
   - `templates/hello-orb-flow.html` — text reflowing around a moving orb (reflow-around-obstacle pattern)
   - `templates/donut-orbit.html` — advanced example: measured ASCII logo obstacles, draggable wire sphere/cube, morphing shape fields, selectable DOM text, and dev-only controls
   - `write_file` to a new `.html` in `/tmp/` or the user's workspace.
3. **Swap the corpus** for something intentional to the brief. Real prose, 10-100 sentences, no lorem.
4. **Tune the aesthetic** — font, palette, composition, interaction. This is the work; don't skip it.
5. **Verify locally**:
   ```sh
   cd <dir-with-html> && python3 -m http.server 8765
   # then open http://localhost:8765/<file>.html
   ```
6. **Check the console** — pretext will throw if `prepareWithSegments` is called with a bad font string; `Intl.Segmenter` is available in every modern browser.
7. **Show the user the file path**, not just the code — they want to open it.

## Performance Notes

- `prepare()` / `prepareWithSegments()` is the expensive call. Do it **once** per text+font pair. Cache the handle.
- On resize, only rerun `layout()` / `layoutWithLines()` — never re-prepare.
- For per-frame animations where text doesn't change but geometry does, `layoutNextLineRange` in a tight loop is cheap enough to do every frame at 60fps for normal-length paragraphs.
- When rendering ASCII masks per frame, keep a cell buffer (`Uint8Array`/typed arrays), derive measured per-row obstacle spans from the cells or projected geometry, merge spans, then feed those spans into `layoutNextLineRange` before drawing text.
- Keep visual animation and layout animation coupled. If a sphere morphs into a cube, tween both the rendered cell buffer and the obstacle spans with the same value; otherwise the demo looks painted-on instead of physically reflowed.
- For fades, prefer layer opacity over changing glyph intensity or obstacle scale. Put transient ASCII sprites on their own canvas and fade the canvas with CSS/GSAP opacity so geometry does not appear to shrink.
- Canvas `ctx.font` setting is surprisingly slow; set it **once** per frame if font doesn't vary, not per `fillText` call.

## Common Pitfalls

1. **Drifting CSS/canvas font strings.** `ctx.font = "16px Inter"` measured, but CSS says `font-family: Inter, sans-serif; font-size: 16px`. Fine *if* Inter loads. If Inter 404s, CSS falls back to sans-serif and measurements drift by 5-20%. Always `preload` the font or use a web-safe family.

2. **Re-preparing inside the animation loop.** Only `layout*` is cheap. Re-calling `prepare` every frame will tank perf. Keep the prepared handle in module scope.

3. **Forgetting `Intl.Segmenter` for grapheme splits.** Emoji, combining marks, CJK — `"é".split("")` gives you two chars. Use `new Intl.Segmenter(undefined, { granularity: "grapheme" })` when sampling individual visible glyphs.

4. **`break: 'never'` chips without `extraWidth`.** In `rich-inline`, if you use `break: 'never'` for an atomic chip/mention, you must also supply `extraWidth` for the pill padding — otherwise chip chrome overflows the container.

5. **Using `@chenglou/pretext` from `unpkg` with TypeScript-only entry.** Use `esm.sh` — it compiles the TS exports to browser-ready ESM automatically. `unpkg` will 404 or serve raw TS.

6. **Monospace fallbacks silently erasing the whole point.** Users seeing monospace-looking output often have a CSS `font-family` that fell through to `monospace`. Verify the actual rendered font via DevTools.

7. **Skipping rows vs adjusting width** when flowing around a shape. If the corridor on this row is too narrow to fit a line, *skip the row* (`y += lineHeight; continue;`) rather than passing a tiny maxWidth to `layoutNextLineRange` — pretext will return one-grapheme lines that look broken.

8. **Shipping a cold demo.** The default first-paint looks tutorial-grade. Add: vignette, subtle scanline, idle auto-motion, one carefully chosen interactive response (drag, hover, scroll, click). Without these, "cool pretext demo" lands as "intern repro of the README."

## Verification Checklist

- [ ] Demo is a single self-contained `.html` file — opens by double-click or `python3 -m http.server`
- [ ] `@chenglou/pretext` imported via `esm.sh` with pinned version
- [ ] Corpus is real prose, not lorem ipsum, and matches the demo's concept
- [ ] Font string passed to `prepare` matches the CSS font exactly
- [ ] `prepare()` / `prepareWithSegments()` called once, not per frame
- [ ] Dark background + considered palette — not the default white canvas
- [ ] At least one interactive response (drag / hover / scroll / click) or idle auto-motion
- [ ] Tested locally with `python3 -m http.server` and confirmed no console errors
- [ ] 60fps on a mid-tier laptop (or graceful degradation documented)
- [ ] One "extra mile" detail the user didn't ask for

## Reference: Community Demos

Clone these for inspiration / patterns (all MIT-ish, linked from [pretext.cool](https://www.pretext.cool/)):

- **Pretext Breaker** — breakout with word-bricks — `github.com/rinesh/pretext-breaker`
- **Tetris × Pretext** — `github.com/shinichimochizuki/tetris-pretext`
- **Dragon animation** — `github.com/qtakmalay/PreTextExperiments`
- **Somnai editorial engine** — `github.com/somnai-dreams/pretext-demos`
- **Bad Apple!! ASCII** — `github.com/frmlinn/bad-apple-pretext`
- **Drag-sprite reflow** — `github.com/dokobot/pretext-demo`
- **Alarmy editorial clock** — `github.com/SmisLee/alarmy-pretext-demo`

Official playground: [chenglou.me/pretext](https://chenglou.me/pretext/) — accordion, bubbles, dynamic-layout, editorial-engine, justification-comparison, masonry, markdown-chat, rich-note.


---

## Lampiran: `references/patterns.md`

# Pretext Patterns

Copy-pasteable snippets for the most common pretext demo shapes. Each pattern is self-contained — drop into an HTML `<script type="module">` after importing from `https://esm.sh/@chenglou/pretext@0.0.6`.

## 1. Flow around an obstacle (variable-width column)

The signature pretext move. Row-by-row ask "how wide is the corridor here?" and let pretext break lines accordingly.

```js
const prepared = prepareWithSegments(TEXT, FONT);
const LINE_H = 24;

function drawFlow(ctx, obstacle /* {x,y,r} */, COL_X, COL_W, H) {
  let cursor = { segmentIndex: 0, graphemeIndex: 0 };
  let y = 72;
  while (y < H - 40) {
    const dy = y - obstacle.y;
    const inBand = Math.abs(dy) < obstacle.r;
    let x = COL_X, w = COL_W;
    if (inBand) {
      const half = Math.sqrt(obstacle.r ** 2 - dy ** 2);
      const leftW  = Math.max(0, (obstacle.x - half) - COL_X);
      const rightW = Math.max(0, (COL_X + COL_W) - (obstacle.x + half));
      if (leftW >= rightW) { x = COL_X;                 w = leftW  - 12; }
      else                 { x = obstacle.x + half + 12; w = rightW - 12; }
      if (w < 40) { y += LINE_H; continue; } // skip rather than squeeze
    }
    const range = layoutNextLineRange(prepared, cursor, w);
    if (!range) break;
    const line = materializeLineRange(prepared, range);
    ctx.fillText(line.text, x, y);
    cursor = range.end;
    y += LINE_H;
  }
}
```

**Obstacle variants:** circles (above), rectangles (use `Math.max(0, …)` on the row-segment), multiple obstacles (sort segments and emit the wider remaining lane), animated obstacles (recompute every frame — pretext is fast enough).

## 2. Text-as-geometry game (word-bricks with collision)

Use `layoutWithLines` to get stable line rects, then treat each word as an axis-aligned box for physics.

```js
const prepared = prepareWithSegments(WORDS.join(" "), FONT);
const { lines } = layoutWithLines(prepared, FIELD_W, 28);

// Build brick rects: split each line on spaces and measure word-by-word.
const bricks = [];
let y = 50;
for (const line of lines) {
  let x = 10;
  for (const word of line.text.split(" ")) {
    const wPx = ctx.measureText(word).width; // or use walkLineRanges per word
    bricks.push({ x, y, w: wPx, h: 24, text: word, hp: 1 });
    x += wPx + ctx.measureText(" ").width;
  }
  y += 28;
}
```

Collision: standard AABB vs the ball. When `hp` drops to 0, the brick is "eaten." For the aesthetic: fade brick opacity with hp, trail particles from the letters on impact.

## 3. Shatter / explode typography

Use `walkLineRanges` + a manual grapheme walk to get `(x, y)` for every glyph, then spawn particles.

```js
const prepared = prepareWithSegments(TEXT, FONT);
const particles = [];
let y = 100;
walkLineRanges(prepared, COL_W, (line) => {
  // materialize so we get per-grapheme positions
  const range = materializeLineRange(prepared, line);
  const seg = new Intl.Segmenter(undefined, { granularity: "grapheme" });
  let x = COL_X;
  for (const { segment } of seg.segment(range.text)) {
    const w = ctx.measureText(segment).width;
    particles.push({ ch: segment, x, y, vx: 0, vy: 0, homeX: x, homeY: y });
    x += w;
  }
  y += LINE_H;
});

// On click, kick particles outward from click point; ease them back to (homeX, homeY).
canvas.addEventListener("click", (e) => {
  for (const p of particles) {
    const dx = p.x - e.clientX, dy = p.y - e.clientY;
    const d = Math.hypot(dx, dy) || 1;
    const force = 400 / (d * 0.2 + 1);
    p.vx += (dx / d) * force;
    p.vy += (dy / d) * force;
  }
});

function tick(dt) {
  for (const p of particles) {
    p.vx *= 0.92; p.vy *= 0.92;
    p.vx += (p.homeX - p.x) * 0.06;
    p.vy += (p.homeY - p.y) * 0.06;
    p.x += p.vx * dt; p.y += p.vy * dt;
  }
}
```

## 4. ASCII mask as moving obstacle

The "cool demos" money pattern: rasterize an ASCII logo, sprite, or bitmap into a cell buffer, then convert the occupied cells into per-row obstacle spans. Pretext lays the paragraphs around those spans, so the text actually opens around the moving ASCII object instead of being visually overpainted.

See `templates/donut-orbit.html` in this skill for a full implementation. Treat it as an example, not the canonical scene: it shows how to derive spans from an ASCII logo, project a wire shape into obstacle rows, keep text selectable in a DOM layer, and hide tuning controls behind `?dev`. Key structure:

```js
const CELL_W = 12, CELL_H = 15;
const cols = Math.ceil(W / CELL_W), rows = Math.ceil(H / CELL_H);
const asciiMask = new Uint8Array(cols * rows);
const obstacleRows = Array.from({ length: rows }, () => []);

function rasterizeLogo(time) {
  asciiMask.fill(0);
  for (const r of obstacleRows) r.length = 0;

  for (const block of logoBlocks(time)) {
    const r0 = Math.floor(block.y0 / CELL_H);
    const r1 = Math.ceil(block.y1 / CELL_H);
    for (let r = r0; r <= r1; r++) {
      obstacleRows[r]?.push([block.x0 - 18, block.x1 + 22]);
      // Fill asciiMask cells here for drawing.
    }
  }

  mergeRowSpans(obstacleRows);
}

function drawParagraphs(prepared) {
  let cursor = { segmentIndex: 0, graphemeIndex: 0 };
  for (let y = yStart; y < yEnd; y += LINE_H) {
    const spans = obstacleRows[Math.floor(y / CELL_H)];
    for (const [x0, x1] of freeIntervalsAround(spans)) {
      const range = layoutNextLineRange(prepared, cursor, x1 - x0);
      if (!range) return;
      ctx.fillText(materializeLineRange(prepared, range).text, x0, y);
      cursor = range.end;
    }
  }
}
```

The important bit is that the ASCII geometry is not decorative only. The same moving spans that draw the logo or draggable object also carve the line intervals passed to `layoutNextLineRange`.

### Measured spans beat magic padding

When a logo or bitmap is rasterized into cells, measure the actual occupied cells per row and then add a small halo. Do not use one giant bounding box. Tight measured spans make the text read as if it is flowing around the letter shapes.

```js
const rowMin = new Float32Array(rows).fill(Infinity);
const rowMax = new Float32Array(rows).fill(-Infinity);

for (const cell of visibleCells) {
  rowMin[cell.row] = Math.min(rowMin[cell.row], cell.x);
  rowMax[cell.row] = Math.max(rowMax[cell.row], cell.x + CELL_W);
}

for (let row = 0; row < rows; row++) {
  if (!Number.isFinite(rowMin[row])) continue;
  obstacleRows[row].push([rowMin[row] - halo, rowMax[row] + halo]);
}
```

For sharp pixel-art letters, smooth adjacent rows before pushing spans. A 1-2 row halo usually prevents code/prose from touching corners without losing the letter silhouette.

### Morphing shapes need morphing obstacles

If the visible object morphs (sphere to cube, logo to particles, etc.), tween the collision field too. A convincing demo uses the same `mix` value for both the rendered buffer and the pretext obstacle rows.

```js
function pushMorphedRows(aRows, bRows, mix) {
  for (let row = 0; row < rows; row++) {
    const a = aRows[row] ?? [centerX, centerX];
    const b = bRows[row] ?? [centerX, centerX];
    obstacleRows[row].push([
      a[0] + (b[0] - a[0]) * mix,
      a[1] + (b[1] - a[1]) * mix,
    ]);
  }
}
```

Without this, the artwork may morph while the text still wraps around the old shape, which breaks the pretext effect.

### Separate visual layers from collision

Use separate canvases when visual treatment should not affect layout. For example, fade an ASCII object with CSS opacity on its own canvas layer, but keep its obstacle rows controlled by explicit shape state. Fading glyph intensity or scaling obstacle spans often looks like the object is shrinking instead of fading.

## 5. Editorial multi-column with shared cursor

Classic magazine layout: three columns, text flows from the end of column 1 into the top of column 2, etc. Pretext makes this trivial because the cursor is portable between `layoutNextLineRange` calls.

```js
const prepared = prepareWithSegments(ARTICLE, FONT);
let cursor = { segmentIndex: 0, graphemeIndex: 0 };

for (const col of [COL1, COL2, COL3]) {
  let y = col.y;
  while (y < col.y + col.h) {
    const range = layoutNextLineRange(prepared, cursor, col.w);
    if (!range) return;
    const line = materializeLineRange(prepared, range);
    ctx.fillText(line.text, col.x, y);
    cursor = range.end;
    y += LINE_H;
  }
}
```

Add pull quotes by treating them as obstacles in the middle column and using pattern #1 around them.

## 6. Multiline shrink-wrap (tightest-fitting card)

Given a max width, find the **smallest** container width that still produces the same line count. Useful for chat bubbles, quote cards, tooltip sizing.

```js
const prepared = prepareWithSegments(text, FONT);
const { lineCount, maxLineWidth } = measureLineStats(prepared, MAX_W);
// card width = maxLineWidth + padding; card height = lineCount * LINE_H + padding
```

For a demo that *visualizes* this, render the card shrinking from `MAX_W` down to `maxLineWidth` over a second — the line count stays constant but the right edge pulls in.

## 7. Kinetic typography

Animate per-line transforms over time. `layoutWithLines` gives you stable lines; index `i` drives the timing offset.

```js
const { lines } = layoutWithLines(prepared, W - 80, 40);
function frame(t) {
  for (let i = 0; i < lines.length; i++) {
    const phase = t * 0.001 - i * 0.15;
    const y = 100 + i * 40 + Math.sin(phase) * 12;
    const opacity = 0.4 + 0.6 * Math.max(0, Math.sin(phase));
    ctx.globalAlpha = opacity;
    ctx.fillText(lines[i].text, 40, y);
  }
}
```

Variants: Star Wars crawl (perspective skew per line), wave (sine y-offset), bounce (ease-in-out arrival), glitch (per-glyph random offset using `Intl.Segmenter`).

## 8. Font stack patterns

| Vibe | Font string | Palette hint |
|------|-------------|--------------|
| Editorial / serious | `17px/1.4 "Iowan Old Style", Georgia, serif` | bone `#e8e6df` on charcoal `#0c0d10` |
| CRT / terminal | `600 13px "JetBrains Mono", ui-monospace, monospace` | amber `hsl(38 60% 62%)` on `#07070a` |
| Humanist / modern | `500 17px Inter, ui-sans-serif, system-ui, sans-serif` | off-white `#f3efe6` on deep-navy `#0b1020` |
| Display / poster | `700 64px "Playfair Display", serif` | hot-red `#ff4130` on cream `#f0ebe0` |
| Engineering | `14px "IBM Plex Mono", monospace` | neon-green `#7cff7c` on near-black `#0a0a0c` |

Always load the web font explicitly (Google Fonts link tag or `@font-face`) so the canvas measurement matches the CSS render.

---

## Catatan adaptasi Zeline
- File pendukung tidak di-inline (terlalu besar/biner): templates/donut-orbit.html, templates/hello-orb-flow.html.

