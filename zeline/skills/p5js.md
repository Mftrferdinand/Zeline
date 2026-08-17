# P5Js

> p5.js sketches: gen art, shaders, interactive, 3D.

## When to use

Use when users request: p5.js sketches, creative coding, generative art, interactive visualizations, canvas animations, browser-based visual art, data viz, shader effects, or any p5.js project.

## What's inside

Production pipeline for interactive and generative visual art using p5.js. Creates browser-based sketches, generative art, data visualizations, interactive experiences, 3D scenes, audio-reactive visuals, and motion graphics — exported as HTML, PNG, GIF, MP4, or SVG. Covers: 2D/3D rendering, noise and particle systems, flow fields, shaders (GLSL), pixel manipulation, kinetic typography, WebGL scenes, audio analysis, mouse/keyboard interaction, and headless high-res export.

## Creative Standard

This is visual art rendered in the browser. The canvas is the medium; the algorithm is the brush.

**Before writing a single line of code**, articulate the creative concept. What does this piece communicate? What makes the viewer stop scrolling? What separates this from a code tutorial example? The user's prompt is a starting point — interpret it with creative ambition.

**First-render excellence is non-negotiable.** The output must be visually striking on first load. If it looks like a p5.js tutorial exercise, a default configuration, or "AI-generated creative coding," it is wrong. Rethink before shipping.

**Go beyond the reference vocabulary.** The noise functions, particle systems, color palettes, and shader effects in the references are a starting vocabulary. For every project, combine, layer, and invent. The catalog is a palette of paints — you write the painting.

**Be proactively creative.** If the user asks for "a particle system," deliver a particle system with emergent flocking behavior, trailing ghost echoes, palette-shifted depth fog, and a background noise field that breathes. Include at least one visual detail the user didn't ask for but will appreciate.

**Dense, layered, considered.** Every frame should reward viewing. Never flat white backgrounds. Always compositional hierarchy. Always intentional color. Always micro-detail that only appears on close inspection.

**Cohesive aesthetic over feature count.** All elements must serve a unified visual language — shared color temperature, consistent stroke weight vocabulary, harmonious motion speeds. A sketch with ten unrelated effects is worse than one with three that belong together.

## Modes

| Mode | Input | Output | Reference |
|------|-------|--------|-----------|
| **Generative art** | Seed / parameters | Procedural visual composition (still or animated) | `references/visual-effects.md` |
| **Data visualization** | Dataset / API | Interactive charts, graphs, custom data displays | `references/interaction.md` |
| **Interactive experience** | None (user drives) | Mouse/keyboard/touch-driven sketch | `references/interaction.md` |
| **Animation / motion graphics** | Timeline / storyboard | Timed sequences, kinetic typography, transitions | `references/animation.md` |
| **3D scene** | Concept description | WebGL geometry, lighting, camera, materials | `references/webgl-and-3d.md` |
| **Image processing** | Image file(s) | Pixel manipulation, filters, mosaic, pointillism | `references/visual-effects.md` § Pixel Manipulation |
| **Audio-reactive** | Audio file / mic | Sound-driven generative visuals | `references/interaction.md` § Audio Input |

## Stack

Single self-contained HTML file per project. No build step required.

| Layer | Tool | Purpose |
|-------|------|---------|
| Core | p5.js 1.11.3 (CDN) | Canvas rendering, math, transforms, event handling |
| 3D | p5.js WebGL mode | 3D geometry, camera, lighting, GLSL shaders |
| Audio | p5.sound.js (CDN) | FFT analysis, amplitude, mic input, oscillators |
| Export | Built-in `saveCanvas()` / `saveGif()` / `saveFrames()` | PNG, GIF, frame sequence output |
| Capture | CCapture.js (optional) | Deterministic framerate video capture (WebM, GIF) |
| Headless | Puppeteer + Node.js (optional) | Automated high-res rendering, MP4 via ffmpeg |
| SVG | p5.js-svg 1.6.0 (optional) | Vector output for print — requires p5.js 1.x |
| Natural media | p5.brush (optional) | Watercolor, charcoal, pen — requires p5.js 2.x + WEBGL |
| Texture | p5.grain (optional) | Film grain, texture overlays |
| Fonts | Google Fonts / `loadFont()` | Custom typography via OTF/TTF/WOFF2 |

### Version Note

**p5.js 1.x** (1.11.3) is the default — stable, well-documented, broadest library compatibility. Use this unless a project requires 2.x features.

**p5.js 2.x** (2.2+) adds: `async setup()` replacing `preload()`, OKLCH/OKLAB color modes, `splineVertex()`, shader `.modify()` API, variable fonts, `textToContours()`, pointer events. Required for p5.brush. See `references/core-api.md` § p5.js 2.0.

## Pipeline

Every project follows the same 6-stage path:

```
CONCEPT → DESIGN → CODE → PREVIEW → EXPORT → VERIFY
```

1. **CONCEPT** — Articulate the creative vision: mood, color world, motion vocabulary, what makes this unique
2. **DESIGN** — Choose mode, canvas size, interaction model, color system, export format. Map concept to technical decisions
3. **CODE** — Write single HTML file with inline p5.js. Structure: globals → `preload()` → `setup()` → `draw()` → helpers → classes → event handlers
4. **PREVIEW** — Open in browser, verify visual quality. Test at target resolution. Check performance
5. **EXPORT** — Capture output: `saveCanvas()` for PNG, `saveGif()` for GIF, `saveFrames()` + ffmpeg for MP4, Puppeteer for headless batch
6. **VERIFY** — Does the output match the concept? Is it visually striking at the intended display size? Would you frame it?

## Creative Direction

### Aesthetic Dimensions

| Dimension | Options | Reference |
|-----------|---------|-----------|
| **Color system** | HSB/HSL, RGB, named palettes, procedural harmony, gradient interpolation | `references/color-systems.md` |
| **Noise vocabulary** | Perlin noise, simplex, fractal (octaved), domain warping, curl noise | `references/visual-effects.md` § Noise |
| **Particle systems** | Physics-based, flocking, trail-drawing, attractor-driven, flow-field following | `references/visual-effects.md` § Particles |
| **Shape language** | Geometric primitives, custom vertices, bezier curves, SVG paths | `references/shapes-and-geometry.md` |
| **Motion style** | Eased, spring-based, noise-driven, physics sim, lerped, stepped | `references/animation.md` |
| **Typography** | System fonts, loaded OTF, `textToPoints()` particle text, kinetic | `references/typography.md` |
| **Shader effects** | GLSL fragment/vertex, filter shaders, post-processing, feedback loops | `references/webgl-and-3d.md` § Shaders |
| **Composition** | Grid, radial, golden ratio, rule of thirds, organic scatter, tiled | `references/core-api.md` § Composition |
| **Interaction model** | Mouse follow, click spawn, drag, keyboard state, scroll-driven, mic input | `references/interaction.md` |
| **Blend modes** | `BLEND`, `ADD`, `MULTIPLY`, `SCREEN`, `DIFFERENCE`, `EXCLUSION`, `OVERLAY` | `references/color-systems.md` § Blend Modes |
| **Layering** | `createGraphics()` offscreen buffers, alpha compositing, masking | `references/core-api.md` § Offscreen Buffers |
| **Texture** | Perlin surface, stippling, hatching, halftone, pixel sorting | `references/visual-effects.md` § Texture Generation |

### Per-Project Variation Rules

Never use default configurations. For every project:
- **Custom color palette** — never raw `fill(255, 0, 0)`. Always a designed palette with 3-7 colors
- **Custom stroke weight vocabulary** — thin accents (0.5), medium structure (1-2), bold emphasis (3-5)
- **Background treatment** — never plain `background(0)` or `background(255)`. Always textured, gradient, or layered
- **Motion variety** — different speeds for different elements. Primary at 1x, secondary at 0.3x, ambient at 0.1x
- **At least one invented element** — a custom particle behavior, a novel noise application, a unique interaction response

### Project-Specific Invention

For every project, invent at least one of:
- A custom color palette matching the mood (not a preset)
- A novel noise field combination (e.g., curl noise + domain warp + feedback)
- A unique particle behavior (custom forces, custom trails, custom spawning)
- An interaction mechanic the user didn't request but that elevates the piece
- A compositional technique that creates visual hierarchy

### Parameter Design Philosophy

Parameters should emerge from the algorithm, not from a generic menu. Ask: "What properties of *this* system should be tunable?"

**Good parameters** expose the algorithm's character:
- **Quantities** — how many particles, branches, cells (controls density)
- **Scales** — noise frequency, element size, spacing (controls texture)
- **Rates** — speed, growth rate, decay (controls energy)
- **Thresholds** — when does behavior change? (controls drama)
- **Ratios** — proportions, balance between forces (controls harmony)

**Bad parameters** are generic controls unrelated to the algorithm:
- "color1", "color2", "size" — meaningless without context
- Toggle switches for unrelated effects
- Parameters that only change cosmetics, not behavior

Every parameter should change how the algorithm *thinks*, not just how it *looks*. A "turbulence" parameter that changes noise octaves is good. A "particle size" slider that only changes `ellipse()` radius is shallow.

## Workflow

### Step 1: Creative Vision

Before any code, articulate:

- **Mood / atmosphere**: What should the viewer feel? Contemplative? Energized? Unsettled? Playful?
- **Visual story**: What happens over time (or on interaction)? Build? Decay? Transform? Oscillate?
- **Color world**: Warm/cool? Monochrome? Complementary? What's the dominant hue? The accent?
- **Shape language**: Organic curves? Sharp geometry? Dots? Lines? Mixed?
- **Motion vocabulary**: Slow drift? Explosive burst? Breathing pulse? Mechanical precision?
- **What makes THIS different**: What is the one thing that makes this sketch unique?

Map the user's prompt to aesthetic choices. "Relaxing generative background" demands different everything from "glitch data visualization."

### Step 2: Technical Design

- **Mode** — which of the 7 modes from the table above
- **Canvas size** — landscape 1920x1080, portrait 1080x1920, square 1080x1080, or responsive `windowWidth/windowHeight`
- **Renderer** — `P2D` (default) or `WEBGL` (for 3D, shaders, advanced blend modes)
- **Frame rate** — 60fps (interactive), 30fps (ambient animation), or `noLoop()` (static generative)
- **Export target** — browser display, PNG still, GIF loop, MP4 video, SVG vector
- **Interaction model** — passive (no input), mouse-driven, keyboard-driven, audio-reactive, scroll-driven
- **Viewer UI** — for interactive generative art, start from `templates/viewer.html` which provides seed navigation, parameter sliders, and download. For simple sketches or video export, use bare HTML

### Step 3: Code the Sketch

For **interactive generative art** (seed exploration, parameter tuning): start from `templates/viewer.html`. Read the template first, keep the fixed sections (seed nav, actions), replace the algorithm and parameter controls. This gives the user seed prev/next/random/jump, parameter sliders with live update, and PNG download — all wired up.

For **animations, video export, or simple sketches**: use bare HTML:

Single HTML file. Structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Project Name</title>
  <script>p5.disableFriendlyErrors = true;</script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.11.3/p5.min.js"></script>
  <!-- <script src="https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.11.3/addons/p5.sound.min.js"></script> -->
  <!-- <script src="https://unpkg.com/p5.js-svg@1.6.0"></script> -->  <!-- SVG export -->
  <!-- <script src="https://cdn.jsdelivr.net/npm/ccapture.js-npmfixed/build/CCapture.all.min.js"></script> -->  <!-- video capture -->
  <style>
    html, body { margin: 0; padding: 0; overflow: hidden; }
    canvas { display: block; }
  </style>
</head>
<body>
<script>
// === Configuration ===
const CONFIG = {
  seed: 42,
  // ... project-specific params
};

// === Color Palette ===
const PALETTE = {
  bg: '#0a0a0f',
  primary: '#e8d5b7',
  // ...
};

// === Global State ===
let particles = [];

// === Preload (fonts, images, data) ===
function preload() {
  // font = loadFont('...');
}

// === Setup ===
function setup() {
  createCanvas(1920, 1080);
  randomSeed(CONFIG.seed);
  noiseSeed(CONFIG.seed);
  colorMode(HSB, 360, 100, 100, 100);
  // Initialize state...
}

// === Draw Loop ===
function draw() {
  // Render frame...
}

// === Helper Functions ===
// ...

// === Classes ===
class Particle {
  // ...
}

// === Event Handlers ===
function mousePressed() { /* ... */ }
function keyPressed() { /* ... */ }
function windowResized() { resizeCanvas(windowWidth, windowHeight); }
</script>
</body>
</html>
```

Key implementation patterns:
- **Seeded randomness**: Always `randomSeed()` + `noiseSeed()` for reproducibility
- **Color mode**: Use `colorMode(HSB, 360, 100, 100, 100)` for intuitive color control
- **State separation**: CONFIG for parameters, PALETTE for colors, globals for mutable state
- **Class-based entities**: Particles, agents, shapes as classes with `update()` + `display()` methods
- **Offscreen buffers**: `createGraphics()` for layered composition, trails, masks

### Step 4: Preview & Iterate

- Open HTML file directly in browser — no server needed for basic sketches
- For `loadImage()`/`loadFont()` from local files: use `scripts/serve.sh` or `python3 -m http.server`
- Chrome DevTools Performance tab to verify 60fps
- Test at target export resolution, not just the window size
- Adjust parameters until the visual matches the concept from Step 1

### Step 5: Export

| Format | Method | Command |
|--------|--------|---------|
| **PNG** | `saveCanvas('output', 'png')` in `keyPressed()` | Press 's' to save |
| **High-res PNG** | Puppeteer headless capture | `node scripts/export-frames.js sketch.html --width 3840 --height 2160 --frames 1` |
| **GIF** | `saveGif('output', 5)` — captures N seconds | Press 'g' to save |
| **Frame sequence** | `saveFrames('frame', 'png', 10, 30)` — 10s at 30fps | Then `ffmpeg -i frame-%04d.png -c:v libx264 output.mp4` |
| **MP4** | Puppeteer frame capture + ffmpeg | `bash scripts/render.sh sketch.html output.mp4 --duration 30 --fps 30` |
| **SVG** | `createCanvas(w, h, SVG)` with p5.js-svg | `save('output.svg')` |

### Step 6: Quality Verification

- **Does it match the vision?** Compare output to the creative concept. If it looks generic, go back to Step 1
- **Resolution check**: Is it sharp at the target display size? No aliasing artifacts?
- **Performance check**: Does it hold 60fps in browser? (30fps minimum for animations)
- **Color check**: Do the colors work together? Test on both light and dark monitors
- **Edge cases**: What happens at canvas edges? On resize? After running for 10 minutes?

## Critical Implementation Notes

### Performance — Disable FES First

The Friendly Error System (FES) adds up to 10x overhead. Disable it in every production sketch:

```javascript
p5.disableFriendlyErrors = true;  // BEFORE setup()

function setup() {
  pixelDensity(1);  // prevent 2x-4x overdraw on retina
  createCanvas(1920, 1080);
}
```

In hot loops (particles, pixel ops), use `Math.*` instead of p5 wrappers — measurably faster:

```javascript
// In draw() or update() hot paths:
let a = Math.sin(t);          // not sin(t)
let r = Math.sqrt(dx*dx+dy*dy); // not dist() — or better: skip sqrt, compare magSq
let v = Math.random();        // not random() — when seed not needed
let m = Math.min(a, b);       // not min(a, b)
```

Never `console.log()` inside `draw()`. Never manipulate DOM in `draw()`. See `references/troubleshooting.md` § Performance.

### Seeded Randomness — Always

Every generative sketch must be reproducible. Same seed, same output.

```javascript
function setup() {
  randomSeed(CONFIG.seed);
  noiseSeed(CONFIG.seed);
  // All random() and noise() calls now deterministic
}
```

Never use `Math.random()` for generative content — only for performance-critical non-visual code. Always `random()` for visual elements. If you need a random seed: `CONFIG.seed = floor(random(99999))`.

### Generative Art Platform Support (fxhash / Art Blocks)

For generative art platforms, replace p5's PRNG with the platform's deterministic random:

```javascript
// fxhash convention
const SEED = $fx.hash;              // unique per mint
const rng = $fx.rand;               // deterministic PRNG
$fx.features({ palette: 'warm', complexity: 'high' });

// In setup():
randomSeed(SEED);   // for p5's noise()
noiseSeed(SEED);

// Replace random() with rng() for platform determinism
let x = rng() * width;  // instead of random(width)
```

See `references/export-pipeline.md` § Platform Export.

### Color Mode — Use HSB

HSB (Hue, Saturation, Brightness) is dramatically easier to work with than RGB for generative art:

```javascript
colorMode(HSB, 360, 100, 100, 100);
// Now: fill(hue, sat, bri, alpha)
// Rotate hue: fill((baseHue + offset) % 360, 80, 90)
// Desaturate: fill(hue, sat * 0.3, bri)
// Darken: fill(hue, sat, bri * 0.5)
```

Never hardcode raw RGB values. Define a palette object, derive variations procedurally. See `references/color-systems.md`.

### Noise — Multi-Octave, Not Raw

Raw `noise(x, y)` looks like smooth blobs. Layer octaves for natural texture:

```javascript
function fbm(x, y, octaves = 4) {
  let val = 0, amp = 1, freq = 1, sum = 0;
  for (let i = 0; i < octaves; i++) {
    val += noise(x * freq, y * freq) * amp;
    sum += amp;
    amp *= 0.5;
    freq *= 2;
  }
  return val / sum;
}
```

For flowing organic forms, use **domain warping**: feed noise output back as noise input coordinates. See `references/visual-effects.md`.

### createGraphics() for Layers — Not Optional

Flat single-pass rendering looks flat. Use offscreen buffers for composition:

```javascript
let bgLayer, fgLayer, trailLayer;
function setup() {
  createCanvas(1920, 1080);
  bgLayer = createGraphics(width, height);
  fgLayer = createGraphics(width, height);
  trailLayer = createGraphics(width, height);
}
function draw() {
  renderBackground(bgLayer);
  renderTrails(trailLayer);   // persistent, fading
  renderForeground(fgLayer);  // cleared each frame
  image(bgLayer, 0, 0);
  image(trailLayer, 0, 0);
  image(fgLayer, 0, 0);
}
```

### Performance — Vectorize Where Possible

p5.js draw calls are expensive. For thousands of particles:

```javascript
// SLOW: individual shapes
for (let p of particles) {
  ellipse(p.x, p.y, p.size);
}

// FAST: single shape with beginShape()
beginShape(POINTS);
for (let p of particles) {
  vertex(p.x, p.y);
}
endShape();

// FASTEST: pixel buffer for massive counts
loadPixels();
for (let p of particles) {
  let idx = 4 * (floor(p.y) * width + floor(p.x));
  pixels[idx] = r; pixels[idx+1] = g; pixels[idx+2] = b; pixels[idx+3] = 255;
}
updatePixels();
```

See `references/troubleshooting.md` § Performance.

### Instance Mode for Multiple Sketches

Global mode pollutes `window`. For production, use instance mode:

```javascript
const sketch = (p) => {
  p.setup = function() {
    p.createCanvas(800, 800);
  };
  p.draw = function() {
    p.background(0);
    p.ellipse(p.mouseX, p.mouseY, 50);
  };
};
new p5(sketch, 'canvas-container');
```

Required when embedding multiple sketches on one page or integrating with frameworks.

### WebGL Mode Gotchas

- `createCanvas(w, h, WEBGL)` — origin is center, not top-left
- Y-axis is inverted (positive Y goes up in WEBGL, down in P2D)
- `translate(-width/2, -height/2)` to get P2D-like coordinates
- `push()`/`pop()` around every transform — matrix stack overflows silently
- `texture()` before `rect()`/`plane()` — not after
- Custom shaders: `createShader(vert, frag)` — test on multiple browsers

### Export — Key Bindings Convention

Every sketch should include these in `keyPressed()`:

```javascript
function keyPressed() {
  if (key === 's' || key === 'S') saveCanvas('output', 'png');
  if (key === 'g' || key === 'G') saveGif('output', 5);
  if (key === 'r' || key === 'R') { randomSeed(millis()); noiseSeed(millis()); }
  if (key === ' ') CONFIG.paused = !CONFIG.paused;
}
```

### Headless Video Export — Use noLoop()

For headless rendering via Puppeteer, the sketch **must** use `noLoop()` in setup. Without it, p5's draw loop runs freely while screenshots are slow — the sketch races ahead and you get skipped/duplicate frames.

```javascript
function setup() {
  createCanvas(1920, 1080);
  pixelDensity(1);
  noLoop();                    // capture script controls frame advance
  window._p5Ready = true;      // signal readiness to capture script
}
```

The bundled `scripts/export-frames.js` detects `_p5Ready` and calls `redraw()` once per capture for exact 1:1 frame correspondence. See `references/export-pipeline.md` § Deterministic Capture.

For multi-scene videos, use the per-clip architecture: one HTML per scene, render independently, stitch with `ffmpeg -f concat`. See `references/export-pipeline.md` § Per-Clip Architecture.

### Agent Workflow

When building p5.js sketches:

1. **Write the HTML file** — single self-contained file, all code inline
2. **Open in browser** — `open sketch.html` (macOS) or `xdg-open sketch.html` (Linux)
3. **Local assets** (fonts, images) require a server: `python3 -m http.server 8080` in the project directory, then open `http://localhost:8080/sketch.html`
4. **Export PNG/GIF** — add `keyPressed()` shortcuts as shown above, tell the user which key to press
5. **Headless export** — `node scripts/export-frames.js sketch.html --frames 300` for automated frame capture (sketch must use `noLoop()` + `_p5Ready`)
6. **MP4 rendering** — `bash scripts/render.sh sketch.html output.mp4 --duration 30`
7. **Iterative refinement** — edit the HTML file, user refreshes browser to see changes
8. **Load references on demand** — use `load_skill(name="p5js", file_path="references/...")` to load specific reference files as needed during implementation

## Performance Targets

| Metric | Target |
|--------|--------|
| Frame rate (interactive) | 60fps sustained |
| Frame rate (animated export) | 30fps minimum |
| Particle count (P2D shapes) | 5,000-10,000 at 60fps |
| Particle count (pixel buffer) | 50,000-100,000 at 60fps |
| Canvas resolution | Up to 3840x2160 (export), 1920x1080 (interactive) |
| File size (HTML) | < 100KB (excluding CDN libraries) |
| Load time | < 2s to first frame |

## References

| File | Contents |
|------|----------|
| `references/core-api.md` | Canvas setup, coordinate system, draw loop, `push()`/`pop()`, offscreen buffers, composition patterns, `pixelDensity()`, responsive design |
| `references/shapes-and-geometry.md` | 2D primitives, `beginShape()`/`endShape()`, Bezier/Catmull-Rom curves, `vertex()` systems, custom shapes, `p5.Vector`, signed distance fields, SVG path conversion |
| `references/visual-effects.md` | Noise (Perlin, fractal, domain warp, curl), flow fields, particle systems (physics, flocking, trails), pixel manipulation, texture generation (stipple, hatch, halftone), feedback loops, reaction-diffusion |
| `references/animation.md` | Frame-based animation, easing functions, `lerp()`/`map()`, spring physics, state machines, timeline sequencing, `millis()`-based timing, transition patterns |
| `references/typography.md` | `text()`, `loadFont()`, `textToPoints()`, kinetic typography, text masks, font metrics, responsive text sizing |
| `references/color-systems.md` | `colorMode()`, HSB/HSL/RGB, `lerpColor()`, `paletteLerp()`, procedural palettes, color harmony, `blendMode()`, gradient rendering, curated palette library |
| `references/webgl-and-3d.md` | WEBGL renderer, 3D primitives, camera, lighting, materials, custom geometry, GLSL shaders (`createShader()`, `createFilterShader()`), framebuffers, post-processing |
| `references/interaction.md` | Mouse events, keyboard state, touch input, DOM elements, `createSlider()`/`createButton()`, audio input (p5.sound FFT/amplitude), scroll-driven animation, responsive events |
| `references/export-pipeline.md` | `saveCanvas()`, `saveGif()`, `saveFrames()`, deterministic headless capture, ffmpeg frame-to-video, CCapture.js, SVG export, per-clip architecture, platform export (fxhash), video gotchas |
| `references/troubleshooting.md` | Performance profiling, per-pixel budgets, common mistakes, browser compatibility, WebGL debugging, font loading issues, pixel density traps, memory leaks, CORS |
| `templates/viewer.html` | Interactive viewer template: seed navigation (prev/next/random/jump), parameter sliders, download PNG, responsive canvas. Start from this for explorable generative art |

---

## Creative Divergence (use only when user requests experimental/creative/unique output)

If the user asks for creative, experimental, surprising, or unconventional output, select the strategy that best fits and reason through its steps BEFORE generating code.

- **Conceptual Blending** — when the user names two things to combine or wants hybrid aesthetics
- **SCAMPER** — when the user wants a twist on a known generative art pattern
- **Distance Association** — when the user gives a single concept and wants exploration ("make something about time")

### Conceptual Blending
1. Name two distinct visual systems (e.g., particle physics + handwriting)
2. Map correspondences (particles = ink drops, forces = pen pressure, fields = letterforms)
3. Blend selectively — keep mappings that produce interesting emergent visuals
4. Code the blend as a unified system, not two systems side-by-side

### SCAMPER Transformation
Take a known generative pattern (flow field, particle system, L-system, cellular automata) and systematically transform it:
- **Substitute**: replace circles with text characters, lines with gradients
- **Combine**: merge two patterns (flow field + voronoi)
- **Adapt**: apply a 2D pattern to a 3D projection
- **Modify**: exaggerate scale, warp the coordinate space
- **Purpose**: use a physics sim for typography, a sorting algorithm for color
- **Eliminate**: remove the grid, remove color, remove symmetry
- **Reverse**: run the simulation backward, invert the parameter space

### Distance Association
1. Anchor on the user's concept (e.g., "loneliness")
2. Generate associations at three distances:
   - Close (obvious): empty room, single figure, silence
   - Medium (interesting): one fish in a school swimming the wrong way, a phone with no notifications, the gap between subway cars
   - Far (abstract): prime numbers, asymptotic curves, the color of 3am
3. Develop the medium-distance associations — they're specific enough to visualize but unexpected enough to be interesting


---

## Lampiran: `references/animation.md`

# Animation

## Frame-Based Animation

### The Draw Loop

```javascript
function draw() {
  // Called ~60 times/sec by default
  // frameCount — integer, starts at 1
  // deltaTime — ms since last frame (use for framerate-independent motion)
  // millis() — ms since sketch start
}
```

### Time-Based vs Frame-Based

```javascript
// Frame-based (speed varies with framerate)
x += speed;

// Time-based (consistent speed regardless of framerate)
x += speed * (deltaTime / 16.67);  // normalized to 60fps
```

### Normalized Time

```javascript
// Progress from 0 to 1 over N seconds
let duration = 5000;  // 5 seconds in ms
let t = constrain(millis() / duration, 0, 1);

// Looping progress (0 → 1 → 0 → 1...)
let period = 3000;  // 3 second loop
let t = (millis() % period) / period;

// Ping-pong (0 → 1 → 0 → 1...)
let raw = (millis() % (period * 2)) / period;
let t = raw <= 1 ? raw : 2 - raw;
```

## Easing Functions

### Built-in Lerp

```javascript
// Linear interpolation — smooth but mechanical
let x = lerp(startX, endX, t);

// Map for non-0-1 ranges
let y = map(t, 0, 1, startY, endY);
```

### Common Easing Curves

```javascript
// Ease in (slow start)
function easeInQuad(t) { return t * t; }
function easeInCubic(t) { return t * t * t; }
function easeInExpo(t) { return t === 0 ? 0 : pow(2, 10 * (t - 1)); }

// Ease out (slow end)
function easeOutQuad(t) { return 1 - (1 - t) * (1 - t); }
function easeOutCubic(t) { return 1 - pow(1 - t, 3); }
function easeOutExpo(t) { return t === 1 ? 1 : 1 - pow(2, -10 * t); }

// Ease in-out (slow both ends)
function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - pow(-2 * t + 2, 3) / 2;
}
function easeInOutQuint(t) {
  return t < 0.5 ? 16 * t * t * t * t * t : 1 - pow(-2 * t + 2, 5) / 2;
}

// Elastic (spring overshoot)
function easeOutElastic(t) {
  if (t === 0 || t === 1) return t;
  return pow(2, -10 * t) * sin((t * 10 - 0.75) * (2 * PI / 3)) + 1;
}

// Bounce
function easeOutBounce(t) {
  if (t < 1/2.75) return 7.5625 * t * t;
  else if (t < 2/2.75) { t -= 1.5/2.75; return 7.5625 * t * t + 0.75; }
  else if (t < 2.5/2.75) { t -= 2.25/2.75; return 7.5625 * t * t + 0.9375; }
  else { t -= 2.625/2.75; return 7.5625 * t * t + 0.984375; }
}

// Smooth step (Hermite interpolation — great default)
function smoothstep(t) { return t * t * (3 - 2 * t); }

// Smoother step (Ken Perlin)
function smootherstep(t) { return t * t * t * (t * (t * 6 - 15) + 10); }
```

### Applying Easing

```javascript
// Animate from startVal to endVal over duration ms
function easedValue(startVal, endVal, startTime, duration, easeFn) {
  let t = constrain((millis() - startTime) / duration, 0, 1);
  return lerp(startVal, endVal, easeFn(t));
}

// Usage
let x = easedValue(100, 700, animStartTime, 2000, easeOutCubic);
```

## Spring Physics

More natural than easing — responds to force, overshoots, settles.

```javascript
class Spring {
  constructor(value, target, stiffness = 0.1, damping = 0.7) {
    this.value = value;
    this.target = target;
    this.velocity = 0;
    this.stiffness = stiffness;
    this.damping = damping;
  }

  update() {
    let force = (this.target - this.value) * this.stiffness;
    this.velocity += force;
    this.velocity *= this.damping;
    this.value += this.velocity;
    return this.value;
  }

  setTarget(t) { this.target = t; }
  isSettled(threshold = 0.01) {
    return abs(this.velocity) < threshold && abs(this.value - this.target) < threshold;
  }
}

// Usage
let springX = new Spring(0, 0, 0.08, 0.85);
function draw() {
  springX.setTarget(mouseX);
  let x = springX.update();
  ellipse(x, height/2, 50);
}
```

### 2D Spring

```javascript
class Spring2D {
  constructor(x, y) {
    this.pos = createVector(x, y);
    this.target = createVector(x, y);
    this.vel = createVector(0, 0);
    this.stiffness = 0.08;
    this.damping = 0.85;
  }

  update() {
    let force = p5.Vector.sub(this.target, this.pos).mult(this.stiffness);
    this.vel.add(force).mult(this.damping);
    this.pos.add(this.vel);
    return this.pos;
  }
}
```

## State Machines

For complex multi-phase animations.

```javascript
const STATES = { IDLE: 0, ENTER: 1, ACTIVE: 2, EXIT: 3 };
let state = STATES.IDLE;
let stateStart = 0;

function setState(newState) {
  state = newState;
  stateStart = millis();
}

function stateTime() {
  return millis() - stateStart;
}

function draw() {
  switch (state) {
    case STATES.IDLE:
      // waiting...
      break;
    case STATES.ENTER:
      let t = constrain(stateTime() / 1000, 0, 1);
      let alpha = easeOutCubic(t) * 255;
      // fade in...
      if (t >= 1) setState(STATES.ACTIVE);
      break;
    case STATES.ACTIVE:
      // main animation...
      break;
    case STATES.EXIT:
      let t2 = constrain(stateTime() / 500, 0, 1);
      // fade out...
      if (t2 >= 1) setState(STATES.IDLE);
      break;
  }
}
```

## Timeline Sequencing

For timed multi-scene animations (motion graphics, title sequences).

```javascript
class Timeline {
  constructor() {
    this.events = [];
  }

  at(timeMs, duration, fn) {
    this.events.push({ start: timeMs, end: timeMs + duration, fn });
    return this;
  }

  update() {
    let now = millis();
    for (let e of this.events) {
      if (now >= e.start && now < e.end) {
        let t = (now - e.start) / (e.end - e.start);
        e.fn(t);
      }
    }
  }
}

// Usage
let timeline = new Timeline();
timeline
  .at(0, 2000, (t) => {
    // Scene 1: title fade in (0-2s)
    let alpha = easeOutCubic(t) * 255;
    fill(255, alpha);
    textSize(48);
    text("Hello", width/2, height/2);
  })
  .at(2000, 1000, (t) => {
    // Scene 2: title fade out (2-3s)
    let alpha = (1 - easeInCubic(t)) * 255;
    fill(255, alpha);
    textSize(48);
    text("Hello", width/2, height/2);
  })
  .at(3000, 5000, (t) => {
    // Scene 3: main content (3-8s)
    renderMainContent(t);
  });

function draw() {
  background(0);
  timeline.update();
}
```

## Noise-Driven Motion

More organic than deterministic animation.

```javascript
// Smooth wandering position
let x = map(noise(frameCount * 0.005, 0), 0, 1, 0, width);
let y = map(noise(0, frameCount * 0.005), 0, 1, 0, height);

// Noise-driven rotation
let angle = noise(frameCount * 0.01) * TWO_PI;

// Noise-driven scale (breathing effect)
let s = map(noise(frameCount * 0.02), 0, 1, 0.8, 1.2);

// Noise-driven color shift
let hue = map(noise(frameCount * 0.003), 0, 1, 0, 360);
```

## Transition Patterns

### Fade In/Out

```javascript
function fadeIn(t) { return constrain(t, 0, 1); }
function fadeOut(t) { return constrain(1 - t, 0, 1); }
```

### Slide

```javascript
function slideIn(t, direction = 'left') {
  let et = easeOutCubic(t);
  switch (direction) {
    case 'left': return lerp(-width, 0, et);
    case 'right': return lerp(width, 0, et);
    case 'up': return lerp(-height, 0, et);
    case 'down': return lerp(height, 0, et);
  }
}
```

### Scale Reveal

```javascript
function scaleReveal(t) {
  let et = easeOutElastic(constrain(t, 0, 1));
  push();
  translate(width/2, height/2);
  scale(et);
  translate(-width/2, -height/2);
  // draw content...
  pop();
}
```

### Staggered Entry

```javascript
// N elements appear one after another
let staggerDelay = 100;  // ms between each
for (let i = 0; i < elements.length; i++) {
  let itemStart = baseTime + i * staggerDelay;
  let t = constrain((millis() - itemStart) / 500, 0, 1);
  let alpha = easeOutCubic(t) * 255;
  let yOffset = lerp(30, 0, easeOutCubic(t));
  // draw element with alpha and yOffset
}
```

## Recording Deterministic Animations

For frame-perfect export, use frame count instead of millis():

```javascript
const TOTAL_FRAMES = 300;  // 10 seconds at 30fps
const FPS = 30;

function draw() {
  let t = frameCount / TOTAL_FRAMES;  // 0 to 1 over full duration
  if (t > 1) { noLoop(); return; }

  // Use t for all animation timing — deterministic
  renderFrame(t);

  // Export
  if (CONFIG.recording) {
    saveCanvas('frame-' + nf(frameCount, 4), 'png');
  }
}
```

## Scene Fade Envelopes (Video)

Every scene in a multi-scene video needs fade-in and fade-out. Hard cuts between visually different generative scenes are jarring.

```javascript
const SCENE_FRAMES = 150;  // 5 seconds at 30fps
const FADE = 15;           // half-second fade

function draw() {
  let lf = frameCount - 1;  // 0-indexed local frame
  let t = lf / SCENE_FRAMES; // 0..1 normalized progress

  // Fade envelope: ramp up at start, ramp down at end
  let fade = 1;
  if (lf < FADE) fade = lf / FADE;
  if (lf > SCENE_FRAMES - FADE) fade = (SCENE_FRAMES - lf) / FADE;
  fade = fade * fade * (3 - 2 * fade);  // smoothstep for organic feel

  // Apply fade to all visual output
  // Option 1: multiply alpha values by fade
  fill(r, g, b, alpha * fade);

  // Option 2: tint entire composited image
  tint(255, fade * 255);
  image(sceneBuffer, 0, 0);
  noTint();

  // Option 3: multiply pixel brightness (for pixel-level scenes)
  pixels[i] = r * fade;
}
```

## Animating Static Algorithms

Some generative algorithms produce a single static result (attractors, circle packing, Voronoi). In video, static content reads as frozen/broken. Techniques to add motion:

### Progressive Reveal

Expand a mask from center outward to reveal the precomputed result:

```javascript
let revealRadius = easeOutCubic(min(t * 1.5, 1)) * (width * 0.8);
// In the render loop, skip pixels beyond revealRadius from center
let dx = x - width/2, dy = y - height/2;
if (sqrt(dx*dx + dy*dy) > revealRadius) continue;
// Soft edge:
let edgeFade = constrain((revealRadius - dist) / 40, 0, 1);
```

### Parameter Sweep

Slowly change a parameter to show the algorithm evolving:

```javascript
// Attractor with drifting parameters
let a = -1.7 + sin(t * 0.5) * 0.2;  // oscillate around base value
let b = 1.3 + cos(t * 0.3) * 0.15;
```

### Slow Camera Motion

Apply subtle zoom or rotation to the final image:

```javascript
push();
translate(width/2, height/2);
scale(1 + t * 0.05);       // slow 5% zoom over scene duration
rotate(t * 0.1);            // gentle rotation
translate(-width/2, -height/2);
image(precomputedResult, 0, 0);
pop();
```

### Overlay Dynamic Elements

Add particles, grain, or subtle noise on top of static content:

```javascript
// Static background
image(staticResult, 0, 0);
// Dynamic overlay
for (let p of ambientParticles) {
  p.update();
  p.display();  // slow-moving specks add life
}
```



---

## Lampiran: `references/color-systems.md`

# Color Systems

## Color Modes

### HSB (Recommended for Generative Art)

```javascript
colorMode(HSB, 360, 100, 100, 100);
// Hue: 0-360 (color wheel position)
// Saturation: 0-100 (gray to vivid)
// Brightness: 0-100 (black to full)
// Alpha: 0-100

fill(200, 80, 90);        // blue, vivid, bright
fill(200, 80, 90, 50);    // 50% transparent
```

HSB advantages:
- Rotate hue: `(baseHue + offset) % 360`
- Desaturate: reduce S
- Darken: reduce B
- Monochrome variations: fix H, vary S and B
- Complementary: `(hue + 180) % 360`
- Analogous: `hue +/- 30`

### HSL

```javascript
colorMode(HSL, 360, 100, 100, 100);
// Lightness 50 = pure color, 0 = black, 100 = white
// More intuitive for tints (L > 50) and shades (L < 50)
```

### RGB

```javascript
colorMode(RGB, 255, 255, 255, 255);  // default
// Direct channel control, less intuitive for procedural palettes
```

## Color Objects

```javascript
let c = color(200, 80, 90);    // create color object
fill(c);

// Extract components
let h = hue(c);
let s = saturation(c);
let b = brightness(c);
let r = red(c);
let g = green(c);
let bl = blue(c);
let a = alpha(c);

// Hex colors work everywhere
fill('#e8d5b7');
fill('#e8d5b7cc');  // with alpha

// Modify via setters
c.setAlpha(128);
c.setRed(200);
```

## Color Interpolation

### lerpColor

```javascript
let c1 = color(0, 80, 100);    // red
let c2 = color(200, 80, 100);  // blue
let mixed = lerpColor(c1, c2, 0.5);  // midpoint blend
// Works in current colorMode
```

### paletteLerp (p5.js 1.11+)

Interpolate through multiple colors at once.

```javascript
let colors = [
  color('#2E0854'),
  color('#850E35'),
  color('#EE6C4D'),
  color('#F5E663')
];
let c = paletteLerp(colors, t);  // t = 0..1, interpolates through all
```

### Manual Multi-Stop Gradient

```javascript
function multiLerp(colors, t) {
  t = constrain(t, 0, 1);
  let segment = t * (colors.length - 1);
  let idx = floor(segment);
  let frac = segment - idx;
  idx = min(idx, colors.length - 2);
  return lerpColor(colors[idx], colors[idx + 1], frac);
}
```

## Gradient Rendering

### Linear Gradient

```javascript
function linearGradient(z29, y1, z30, y2, c1, c2) {
  let steps = dist(z29, y1, z30, y2);
  for (let i = 0; i <= steps; i++) {
    let t = i / steps;
    let c = lerpColor(c1, c2, t);
    stroke(c);
    let x = lerp(z29, z30, t);
    let y = lerp(y1, y2, t);
    // Draw perpendicular line at each point
    let dx = -(y2 - y1) / steps * 1000;
    let dy = (z30 - z29) / steps * 1000;
    line(x - dx, y - dy, x + dx, y + dy);
  }
}
```

### Radial Gradient

```javascript
function radialGradient(cx, cy, r, innerColor, outerColor) {
  noStroke();
  for (let i = r; i > 0; i--) {
    let t = 1 - i / r;
    fill(lerpColor(innerColor, outerColor, t));
    ellipse(cx, cy, i * 2);
  }
}
```

### Noise-Based Gradient

```javascript
function noiseGradient(colors, noiseScale, time) {
  loadPixels();
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let n = noise(x * noiseScale, y * noiseScale, time);
      let c = multiLerp(colors, n);
      let idx = 4 * (y * width + x);
      pixels[idx] = red(c);
      pixels[idx+1] = green(c);
      pixels[idx+2] = blue(c);
      pixels[idx+3] = 255;
    }
  }
  updatePixels();
}
```

## Procedural Palette Generation

### Complementary

```javascript
function complementary(baseHue) {
  return [baseHue, (baseHue + 180) % 360];
}
```

### Analogous

```javascript
function analogous(baseHue, spread = 30) {
  return [
    (baseHue - spread + 360) % 360,
    baseHue,
    (baseHue + spread) % 360
  ];
}
```

### Triadic

```javascript
function triadic(baseHue) {
  return [baseHue, (baseHue + 120) % 360, (baseHue + 240) % 360];
}
```

### Split Complementary

```javascript
function splitComplementary(baseHue) {
  return [baseHue, (baseHue + 150) % 360, (baseHue + 210) % 360];
}
```

### Tetradic (Rectangle)

```javascript
function tetradic(baseHue) {
  return [baseHue, (baseHue + 60) % 360, (baseHue + 180) % 360, (baseHue + 240) % 360];
}
```

### Monochromatic Variations

```javascript
function monoVariations(hue, count = 5) {
  let colors = [];
  for (let i = 0; i < count; i++) {
    let s = map(i, 0, count - 1, 20, 90);
    let b = map(i, 0, count - 1, 95, 40);
    colors.push(color(hue, s, b));
  }
  return colors;
}
```

## Curated Palette Library

### Warm Palettes

```javascript
const SUNSET = ['#2E0854', '#850E35', '#EE6C4D', '#F5E663'];
const EMBER  = ['#1a0000', '#4a0000', '#8b2500', '#cd5c00', '#ffd700'];
const PEACH  = ['#fff5eb', '#ffdab9', '#ff9a76', '#ff6b6b', '#c94c4c'];
const COPPER = ['#1c1108', '#3d2b1f', '#7b4b2a', '#b87333', '#daa06d'];
```

### Cool Palettes

```javascript
const OCEAN   = ['#0a0e27', '#1a1b4b', '#2a4a7f', '#3d7cb8', '#87ceeb'];
const ARCTIC  = ['#0d1b2a', '#1b263b', '#415a77', '#778da9', '#e0e1dd'];
const FOREST  = ['#0b1a0b', '#1a3a1a', '#2d5a2d', '#4a8c4a', '#90c990'];
const DEEP_SEA = ['#000814', '#001d3d', '#003566', '#006d77', '#83c5be'];
```

### Neutral Palettes

```javascript
const GRAPHITE = ['#1a1a1a', '#333333', '#555555', '#888888', '#cccccc'];
const CREAM    = ['#f4f0e8', '#e8dcc8', '#c9b99a', '#a89070', '#7a6450'];
const SLATE    = ['#1e293b', '#334155', '#475569', '#64748b', '#94a3b8'];
```

### Vivid Palettes

```javascript
const NEON     = ['#ff00ff', '#00ffff', '#ff0080', '#80ff00', '#0080ff'];
const RAINBOW  = ['#ff0000', '#ff8000', '#ffff00', '#00ff00', '#0000ff', '#8000ff'];
const VAPOR    = ['#ff71ce', '#01cdfe', '#05ffa1', '#b967ff', '#fffb96'];
const CYBER    = ['#0f0f0f', '#00ff41', '#ff0090', '#00d4ff', '#ffd000'];
```

### Earth Tones

```javascript
const TERRA    = ['#2c1810', '#5c3a2a', '#8b6b4a', '#c4a672', '#e8d5b7'];
const MOSS     = ['#1a1f16', '#3d4a2e', '#6b7c4f', '#9aab7a', '#c8d4a9'];
const CLAY     = ['#3b2f2f', '#6b4c4c', '#9e7676', '#c9a0a0', '#e8caca'];
```

## Blend Modes

```javascript
blendMode(BLEND);       // default — alpha compositing
blendMode(ADD);         // additive — bright glow effects
blendMode(MULTIPLY);    // darkening — shadows, texture overlay
blendMode(SCREEN);      // lightening — soft glow
blendMode(OVERLAY);     // contrast boost — high/low emphasis
blendMode(DIFFERENCE);  // color subtraction — psychedelic
blendMode(EXCLUSION);   // softer difference
blendMode(REPLACE);     // overwrite (no alpha blending)
blendMode(REMOVE);      // subtract alpha
blendMode(LIGHTEST);    // keep brighter pixel
blendMode(DARKEST);     // keep darker pixel
blendMode(BURN);        // darken + saturate
blendMode(DODGE);       // lighten + saturate
blendMode(SOFT_LIGHT);  // subtle overlay
blendMode(HARD_LIGHT);  // strong overlay

// ALWAYS reset after use
blendMode(BLEND);
```

### Blend Mode Recipes

| Effect | Mode | Use case |
|--------|------|----------|
| Additive glow | `ADD` | Light beams, fire, particles |
| Shadow overlay | `MULTIPLY` | Texture, vignette |
| Soft light mix | `SCREEN` | Fog, mist, backlight |
| High contrast | `OVERLAY` | Dramatic compositing |
| Color negative | `DIFFERENCE` | Glitch, psychedelic |
| Layer compositing | `BLEND` | Standard alpha layering |

## Background Techniques

### Textured Background

```javascript
function texturedBackground(baseColor, noiseScale, noiseAmount) {
  loadPixels();
  let r = red(baseColor), g = green(baseColor), b = blue(baseColor);
  for (let i = 0; i < pixels.length; i += 4) {
    let x = (i / 4) % width;
    let y = floor((i / 4) / width);
    let n = (noise(x * noiseScale, y * noiseScale) - 0.5) * noiseAmount;
    pixels[i] = constrain(r + n, 0, 255);
    pixels[i+1] = constrain(g + n, 0, 255);
    pixels[i+2] = constrain(b + n, 0, 255);
    pixels[i+3] = 255;
  }
  updatePixels();
}
```

### Vignette

```javascript
function vignette(strength = 0.5, radius = 0.7) {
  loadPixels();
  let cx = width / 2, cy = height / 2;
  let maxDist = dist(0, 0, cx, cy);
  for (let i = 0; i < pixels.length; i += 4) {
    let x = (i / 4) % width;
    let y = floor((i / 4) / width);
    let d = dist(x, y, cx, cy) / maxDist;
    let factor = 1.0 - smoothstep(constrain((d - radius) / (1 - radius), 0, 1)) * strength;
    pixels[i] *= factor;
    pixels[i+1] *= factor;
    pixels[i+2] *= factor;
  }
  updatePixels();
}

function smoothstep(t) { return t * t * (3 - 2 * t); }
```

### Film Grain

```javascript
function filmGrain(amount = 30) {
  loadPixels();
  for (let i = 0; i < pixels.length; i += 4) {
    let grain = random(-amount, amount);
    pixels[i] = constrain(pixels[i] + grain, 0, 255);
    pixels[i+1] = constrain(pixels[i+1] + grain, 0, 255);
    pixels[i+2] = constrain(pixels[i+2] + grain, 0, 255);
  }
  updatePixels();
}
```



---

## Lampiran: `references/core-api.md`

# Core API Reference

## Canvas Setup

### createCanvas()

```javascript
// 2D (default renderer)
createCanvas(1920, 1080);

// WebGL (3D, shaders)
createCanvas(1920, 1080, WEBGL);

// Responsive
createCanvas(windowWidth, windowHeight);
```

### Pixel Density

High-DPI displays render at 2x by default. This doubles memory usage and halves performance.

```javascript
// Force 1x for consistent export and performance
pixelDensity(1);

// Match display (default) — sharp on retina but expensive
pixelDensity(displayDensity());

// ALWAYS call before createCanvas()
function setup() {
  pixelDensity(1);        // first
  createCanvas(1920, 1080); // second
}
```

For export, always `pixelDensity(1)` and use the exact target resolution. Never rely on device scaling for final output.

### Responsive Resize

```javascript
function windowResized() {
  resizeCanvas(windowWidth, windowHeight);
  // Recreate offscreen buffers at new size
  bgLayer = createGraphics(width, height);
  // Reinitialize any size-dependent state
}
```

## Coordinate System

### P2D (Default)
- Origin: top-left (0, 0)
- X increases rightward
- Y increases downward
- Angles: radians by default, `angleMode(DEGREES)` to switch

### WEBGL
- Origin: center of canvas
- X increases rightward, Y increases **upward**, Z increases toward viewer
- To get P2D-like coordinates in WEBGL: `translate(-width/2, -height/2)`

## Draw Loop

```javascript
function preload() {
  // Load assets before setup — fonts, images, JSON, CSV
  // Blocks execution until all loads complete
  font = loadFont('font.otf');
  img = loadImage('texture.png');
  data = loadJSON('data.json');
}

function setup() {
  // Runs once. Create canvas, initialize state.
  createCanvas(1920, 1080);
  colorMode(HSB, 360, 100, 100, 100);
  randomSeed(CONFIG.seed);
  noiseSeed(CONFIG.seed);
}

function draw() {
  // Runs every frame (default 60fps).
  // Set frameRate(30) in setup() to change.
  // Call noLoop() for static sketches (render once).
}
```

### Frame Control

```javascript
frameRate(30);           // set target FPS
noLoop();                // stop draw loop (static pieces)
loop();                  // restart draw loop
redraw();                // call draw() once (manual refresh)
frameCount              // frames since start (integer)
deltaTime               // milliseconds since last frame (float)
millis()                // milliseconds since sketch started
```

## Transform Stack

Every transform is cumulative. Use `push()`/`pop()` to isolate.

```javascript
push();
  translate(width / 2, height / 2);
  rotate(angle);
  scale(1.5);
  // draw something at transformed position
  ellipse(0, 0, 100, 100);
pop();
// back to original coordinate system
```

### Transform Functions

| Function | Effect |
|----------|--------|
| `translate(x, y)` | Move origin |
| `rotate(angle)` | Rotate around origin (radians) |
| `scale(s)` / `scale(sx, sy)` | Scale from origin |
| `shearX(angle)` | Skew X axis |
| `shearY(angle)` | Skew Y axis |
| `applyMatrix(a, b, c, d, e, f)` | Arbitrary 2D affine transform |
| `resetMatrix()` | Clear all transforms |

### Composition Pattern: Rotate Around Center

```javascript
push();
  translate(cx, cy);       // move origin to center
  rotate(angle);           // rotate around that center
  translate(-cx, -cy);     // move origin back
  // draw at original coordinates, but rotated around (cx, cy)
  rect(cx - 50, cy - 50, 100, 100);
pop();
```

## Offscreen Buffers (createGraphics)

Offscreen buffers are separate canvases you can draw to and composite. Essential for:
- **Layered composition** — background, midground, foreground
- **Persistent trails** — draw to buffer, fade with semi-transparent rect, never clear
- **Masking** — draw mask to buffer, apply with `image()` or pixel operations
- **Post-processing** — render scene to buffer, apply effects, draw to main canvas

```javascript
let layer;

function setup() {
  createCanvas(1920, 1080);
  layer = createGraphics(width, height);
}

function draw() {
  // Draw to offscreen buffer
  layer.background(0, 10);  // semi-transparent clear = trails
  layer.fill(255);
  layer.ellipse(mouseX, mouseY, 20);

  // Composite to main canvas
  image(layer, 0, 0);
}
```

### Trail Effect Pattern

```javascript
let trailBuffer;

function setup() {
  createCanvas(1920, 1080);
  trailBuffer = createGraphics(width, height);
  trailBuffer.background(0);
}

function draw() {
  // Fade previous frame (lower alpha = longer trails)
  trailBuffer.noStroke();
  trailBuffer.fill(0, 0, 0, 15);  // RGBA — 15/255 alpha
  trailBuffer.rect(0, 0, width, height);

  // Draw new content
  trailBuffer.fill(255);
  trailBuffer.ellipse(mouseX, mouseY, 10);

  // Show
  image(trailBuffer, 0, 0);
}
```

### Multi-Layer Composition

```javascript
let bgLayer, contentLayer, fxLayer;

function setup() {
  createCanvas(1920, 1080);
  bgLayer = createGraphics(width, height);
  contentLayer = createGraphics(width, height);
  fxLayer = createGraphics(width, height);
}

function draw() {
  // Background — drawn once or slowly evolving
  renderBackground(bgLayer);

  // Content — main visual elements
  contentLayer.clear();
  renderContent(contentLayer);

  // FX — overlays, vignettes, grain
  fxLayer.clear();
  renderEffects(fxLayer);

  // Composite with blend modes
  image(bgLayer, 0, 0);
  blendMode(ADD);
  image(contentLayer, 0, 0);
  blendMode(MULTIPLY);
  image(fxLayer, 0, 0);
  blendMode(BLEND);  // reset
}
```

## Composition Patterns

### Grid Layout

```javascript
let cols = 10, rows = 10;
let cellW = width / cols;
let cellH = height / rows;
for (let i = 0; i < cols; i++) {
  for (let j = 0; j < rows; j++) {
    let cx = cellW * (i + 0.5);
    let cy = cellH * (j + 0.5);
    // draw element at (cx, cy) within cell size (cellW, cellH)
  }
}
```

### Radial Layout

```javascript
let n = 12;
for (let i = 0; i < n; i++) {
  let angle = TWO_PI * i / n;
  let r = 300;
  let x = width/2 + cos(angle) * r;
  let y = height/2 + sin(angle) * r;
  // draw element at (x, y)
}
```

### Golden Ratio Spiral

```javascript
let phi = (1 + sqrt(5)) / 2;
let n = 500;
for (let i = 0; i < n; i++) {
  let angle = i * TWO_PI / (phi * phi);
  let r = sqrt(i) * 10;
  let x = width/2 + cos(angle) * r;
  let y = height/2 + sin(angle) * r;
  let size = map(i, 0, n, 8, 2);
  ellipse(x, y, size);
}
```

### Margin-Aware Composition

```javascript
const MARGIN = 80;  // pixels from edge
const drawW = width - 2 * MARGIN;
const drawH = height - 2 * MARGIN;

// Map normalized [0,1] coordinates to drawable area
function mapX(t) { return MARGIN + t * drawW; }
function mapY(t) { return MARGIN + t * drawH; }
```

## Random and Noise

### Seeded Random

```javascript
randomSeed(42);
let x = random(100);        // always same value for seed 42
let y = random(-1, 1);      // range
let item = random(myArray);  // random element
```

### Gaussian Random

```javascript
let x = randomGaussian(0, 1);  // mean=0, stddev=1
// Useful for natural-looking distributions
```

### Perlin Noise

```javascript
noiseSeed(42);
noiseDetail(4, 0.5);  // 4 octaves, 0.5 falloff

let v = noise(x * 0.01, y * 0.01);  // returns 0.0 to 1.0
// Scale factor (0.01) controls feature size — smaller = smoother
```

## Math Utilities

| Function | Description |
|----------|-------------|
| `map(v, lo1, hi1, lo2, hi2)` | Remap value between ranges |
| `constrain(v, lo, hi)` | Clamp to range |
| `lerp(a, b, t)` | Linear interpolation |
| `norm(v, lo, hi)` | Normalize to 0-1 |
| `dist(z29, y1, z30, y2)` | Euclidean distance |
| `mag(x, y)` | Vector magnitude |
| `abs()`, `ceil()`, `floor()`, `round()` | Standard math |
| `sq(n)`, `sqrt(n)`, `pow(b, e)` | Powers |
| `sin()`, `cos()`, `tan()`, `atan2()` | Trig (radians) |
| `degrees(r)`, `radians(d)` | Angle conversion |
| `fract(n)` | Fractional part |

## p5.js 2.0 Changes

p5.js 2.0 (released Apr 2025, current: 2.2) introduces breaking changes. The p5.js editor defaults to 1.x until Aug 2026. Use 2.x only when you need its features.

### async setup() replaces preload()

```javascript
// p5.js 1.x
let img;
function preload() { img = loadImage('cat.jpg'); }
function setup() { createCanvas(800, 800); }

// p5.js 2.x
let img;
async function setup() {
  createCanvas(800, 800);
  img = await loadImage('cat.jpg');
}
```

### New Color Modes

```javascript
colorMode(OKLCH);  // perceptually uniform — better gradients
// L: 0-1 (lightness), C: 0-0.4 (chroma), H: 0-360 (hue)
fill(0.7, 0.15, 200);  // medium-bright saturated blue

colorMode(OKLAB);  // perceptually uniform, no hue angle
colorMode(HWB);    // Hue-Whiteness-Blackness
```

### splineVertex() replaces curveVertex()

No more doubling first/last control points:

```javascript
// p5.js 1.x — must repeat first and last
beginShape();
curveVertex(pts[0].x, pts[0].y);  // doubled
for (let p of pts) curveVertex(p.x, p.y);
curveVertex(pts[pts.length-1].x, pts[pts.length-1].y);  // doubled
endShape();

// p5.js 2.x — clean
beginShape();
for (let p of pts) splineVertex(p.x, p.y);
endShape();
```

### Shader .modify() API

Modify built-in shaders without writing full GLSL:

```javascript
let myShader = baseMaterialShader().modify({
  vertexDeclarations: 'uniform float uTime;',
  'vec4 getWorldPosition': `(vec4 pos) {
    pos.y += sin(pos.x * 0.1 + uTime) * 20.0;
    return pos;
  }`
});
```

### Variable Fonts

```javascript
textWeight(700);  // dynamic weight without loading multiple files
```

### textToContours() and textToModel()

```javascript
let contours = font.textToContours('HELLO', 0, 0, 200);
// Returns array of contour arrays (closed paths)

let geo = font.textToModel('HELLO', 0, 0, 200);
// Returns p5.Geometry for 3D extruded text
```

### CDN for p5.js 2.x

```html
<script src="https://cdn.jsdelivr.net/npm/p5@2/lib/p5.min.js"></script>
```



---

## Lampiran: `references/interaction.md`

# Interaction

## Mouse Events

### Continuous State

```javascript
mouseX, mouseY          // current position (relative to canvas)
pmouseX, pmouseY        // previous frame position
mouseIsPressed          // boolean
mouseButton             // LEFT, RIGHT, CENTER (during press)
movedX, movedY          // delta since last frame
winMouseX, winMouseY    // relative to window (not canvas)
```

### Event Callbacks

```javascript
function mousePressed() {
  // fires once on press
  // mouseButton tells you which button
}

function mouseReleased() {
  // fires once on release
}

function mouseClicked() {
  // fires after press+release (same element)
}

function doubleClicked() {
  // fires on double-click
}

function mouseMoved() {
  // fires when mouse moves (no button pressed)
}

function mouseDragged() {
  // fires when mouse moves WITH button pressed
}

function mouseWheel(event) {
  // event.delta: positive = scroll down, negative = scroll up
  zoom += event.delta * -0.01;
  return false;  // prevent page scroll
}
```

### Mouse Interaction Patterns

**Spawn on click:**
```javascript
function mousePressed() {
  particles.push(new Particle(mouseX, mouseY));
}
```

**Mouse follow with spring:**
```javascript
let springX, springY;
function setup() {
  springX = new Spring(width/2, width/2);
  springY = new Spring(height/2, height/2);
}
function draw() {
  springX.setTarget(mouseX);
  springY.setTarget(mouseY);
  let x = springX.update();
  let y = springY.update();
  ellipse(x, y, 50);
}
```

**Drag interaction:**
```javascript
let dragging = false;
let dragObj = null;
let offsetX, offsetY;

function mousePressed() {
  for (let obj of objects) {
    if (dist(mouseX, mouseY, obj.x, obj.y) < obj.radius) {
      dragging = true;
      dragObj = obj;
      offsetX = mouseX - obj.x;
      offsetY = mouseY - obj.y;
      break;
    }
  }
}

function mouseDragged() {
  if (dragging && dragObj) {
    dragObj.x = mouseX - offsetX;
    dragObj.y = mouseY - offsetY;
  }
}

function mouseReleased() {
  dragging = false;
  dragObj = null;
}
```

**Mouse repulsion (particles flee cursor):**
```javascript
function draw() {
  let mousePos = createVector(mouseX, mouseY);
  for (let p of particles) {
    let d = p.pos.dist(mousePos);
    if (d < 150) {
      let repel = p5.Vector.sub(p.pos, mousePos);
      repel.normalize();
      repel.mult(map(d, 0, 150, 5, 0));
      p.applyForce(repel);
    }
  }
}
```

## Keyboard Events

### State

```javascript
keyIsPressed         // boolean
key                  // last key as string ('a', 'A', ' ')
keyCode              // numeric code (LEFT_ARROW, UP_ARROW, etc.)
```

### Event Callbacks

```javascript
function keyPressed() {
  // fires once on press
  if (keyCode === LEFT_ARROW) { /* ... */ }
  if (key === 's') saveCanvas('output', 'png');
  if (key === ' ') CONFIG.paused = !CONFIG.paused;
  return false;  // prevent default browser behavior
}

function keyReleased() {
  // fires once on release
}

function keyTyped() {
  // fires for printable characters only (not arrows, shift, etc.)
}
```

### Continuous Key State (Multiple Keys)

```javascript
let keys = {};

function keyPressed() { keys[keyCode] = true; }
function keyReleased() { keys[keyCode] = false; }

function draw() {
  if (keys[LEFT_ARROW]) player.x -= 5;
  if (keys[RIGHT_ARROW]) player.x += 5;
  if (keys[UP_ARROW]) player.y -= 5;
  if (keys[DOWN_ARROW]) player.y += 5;
}
```

### Key Constants

```
LEFT_ARROW, RIGHT_ARROW, UP_ARROW, DOWN_ARROW
BACKSPACE, DELETE, ENTER, RETURN, TAB, ESCAPE
SHIFT, CONTROL, OPTION, ALT
```

## Touch Events

```javascript
touches   // array of { x, y, id } — all current touches

function touchStarted() {
  // fires on first touch
  return false;  // prevent default (stops scroll on mobile)
}

function touchMoved() {
  // fires on touch drag
  return false;
}

function touchEnded() {
  // fires on touch release
}
```

### Pinch Zoom

```javascript
let prevDist = 0;
let zoomLevel = 1;

function touchMoved() {
  if (touches.length === 2) {
    let d = dist(touches[0].x, touches[0].y, touches[1].x, touches[1].y);
    if (prevDist > 0) {
      zoomLevel *= d / prevDist;
    }
    prevDist = d;
  }
  return false;
}

function touchEnded() {
  prevDist = 0;
}
```

## DOM Elements

### Creating Controls

```javascript
function setup() {
  createCanvas(800, 800);

  // Slider
  let slider = createSlider(0, 255, 100, 1);  // min, max, default, step
  slider.position(10, height + 10);
  slider.input(() => { CONFIG.value = slider.value(); });

  // Button
  let btn = createButton('Reset');
  btn.position(10, height + 40);
  btn.mousePressed(() => { resetSketch(); });

  // Checkbox
  let check = createCheckbox('Show grid', false);
  check.position(10, height + 70);
  check.changed(() => { CONFIG.showGrid = check.checked(); });

  // Select / dropdown
  let sel = createSelect();
  sel.position(10, height + 100);
  sel.option('Mode A');
  sel.option('Mode B');
  sel.changed(() => { CONFIG.mode = sel.value(); });

  // Color picker
  let picker = createColorPicker('#ff0000');
  picker.position(10, height + 130);
  picker.input(() => { CONFIG.color = picker.value(); });

  // Text input
  let inp = createInput('Hello');
  inp.position(10, height + 160);
  inp.input(() => { CONFIG.text = inp.value(); });
}
```

### Styling DOM Elements

```javascript
let slider = createSlider(0, 100, 50);
slider.position(10, 10);
slider.style('width', '200px');
slider.class('my-slider');
slider.parent('controls-div');  // attach to specific DOM element
```

## Audio Input (p5.sound)

Requires `p5.sound.min.js` addon.

```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.11.3/addons/p5.sound.min.js"></script>
```

### Microphone Input

```javascript
let mic, fft, amplitude;

function setup() {
  createCanvas(800, 800);
  userStartAudio();  // required — user gesture to enable audio

  mic = new p5.AudioIn();
  mic.start();

  fft = new p5.FFT(0.8, 256);  // smoothing, bins
  fft.setInput(mic);

  amplitude = new p5.Amplitude();
  amplitude.setInput(mic);
}

function draw() {
  let level = amplitude.getLevel();    // 0.0 to 1.0 (overall volume)
  let spectrum = fft.analyze();         // array of 256 frequency values (0-255)
  let waveform = fft.waveform();        // array of 256 time-domain samples (-1 to 1)

  // Get energy in frequency bands
  let bass = fft.getEnergy('bass');          // 20-140 Hz
  let lowMid = fft.getEnergy('lowMid');      // 140-400 Hz
  let mid = fft.getEnergy('mid');            // 400-2600 Hz
  let highMid = fft.getEnergy('highMid');    // 2600-5200 Hz
  let treble = fft.getEnergy('treble');      // 5200-14000 Hz
  // Each returns 0-255
}
```

### Audio File Playback

```javascript
let song, fft;

function preload() {
  song = loadSound('track.mp3');
}

function setup() {
  createCanvas(800, 800);
  fft = new p5.FFT(0.8, 512);
  fft.setInput(song);
}

function mousePressed() {
  if (song.isPlaying()) {
    song.pause();
  } else {
    song.play();
  }
}
```

### Beat Detection (Simple)

```javascript
let prevBass = 0;
let beatThreshold = 30;
let beatCooldown = 0;

function detectBeat() {
  let bass = fft.getEnergy('bass');
  let isBeat = bass - prevBass > beatThreshold && beatCooldown <= 0;
  prevBass = bass;
  if (isBeat) beatCooldown = 10;  // frames
  beatCooldown--;
  return isBeat;
}
```

## Scroll-Driven Animation

```javascript
let scrollProgress = 0;

function setup() {
  let canvas = createCanvas(windowWidth, windowHeight);
  canvas.style('position', 'fixed');
  // Make page scrollable
  document.body.style.height = '500vh';
}

window.addEventListener('scroll', () => {
  let maxScroll = document.body.scrollHeight - window.innerHeight;
  scrollProgress = window.scrollY / maxScroll;
});

function draw() {
  background(0);
  // Use scrollProgress (0 to 1) to drive animation
  let x = lerp(0, width, scrollProgress);
  ellipse(x, height/2, 50);
}
```

## Responsive Events

```javascript
function windowResized() {
  resizeCanvas(windowWidth, windowHeight);
  // Recreate buffers
  bgLayer = createGraphics(width, height);
  // Recalculate layout
  recalculateLayout();
}

// Visibility change (tab switching)
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    noLoop();  // pause when tab not visible
  } else {
    loop();
  }
});
```



---

## Lampiran: `references/shapes-and-geometry.md`

# Shapes and Geometry

## 2D Primitives

```javascript
point(x, y);
line(z29, y1, z30, y2);
rect(x, y, w, h);            // default: corner mode
rect(x, y, w, h, r);         // rounded corners
rect(x, y, w, h, tl, tr, br, bl);  // per-corner radius
square(x, y, size);
ellipse(x, y, w, h);
circle(x, y, d);             // diameter, not radius
triangle(z29, y1, z30, y2, z31, y3);
quad(z29, y1, z30, y2, z31, y3, z32, y4);
arc(x, y, w, h, start, stop, mode);  // mode: OPEN, CHORD, PIE
```

### Drawing Modes

```javascript
rectMode(CENTER);   // x,y is center (default: CORNER)
rectMode(CORNERS);  // z29,y1 to z30,y2
ellipseMode(CORNER); // x,y is top-left corner
ellipseMode(CENTER); // default — x,y is center
```

## Stroke and Fill

```javascript
fill(r, g, b, a);    // or fill(gray), fill('#hex'), fill(h, s, b) in HSB mode
noFill();
stroke(r, g, b, a);
noStroke();
strokeWeight(2);
strokeCap(ROUND);     // ROUND, SQUARE, PROJECT
strokeJoin(ROUND);    // ROUND, MITER, BEVEL
```

## Custom Shapes with Vertices

### Basic vertex shape

```javascript
beginShape();
  vertex(100, 100);
  vertex(200, 50);
  vertex(300, 100);
  vertex(250, 200);
  vertex(150, 200);
endShape(CLOSE);  // CLOSE connects last vertex to first
```

### Shape modes

```javascript
beginShape();          // default: polygon connecting all vertices
beginShape(POINTS);    // individual points
beginShape(LINES);     // pairs of vertices as lines
beginShape(TRIANGLES); // triplets as triangles
beginShape(TRIANGLE_FAN);
beginShape(TRIANGLE_STRIP);
beginShape(QUADS);     // groups of 4
beginShape(QUAD_STRIP);
```

### Contours (holes in shapes)

```javascript
beginShape();
  // outer shape
  vertex(100, 100);
  vertex(300, 100);
  vertex(300, 300);
  vertex(100, 300);
  // inner hole
  beginContour();
    vertex(150, 150);
    vertex(150, 250);
    vertex(250, 250);
    vertex(250, 150);
  endContour();
endShape(CLOSE);
```

## Bezier Curves

### Cubic Bezier

```javascript
bezier(z29, y1, cx1, cy1, cx2, cy2, z30, y2);
// z29,y1 = start point
// cx1,cy1 = first control point
// cx2,cy2 = second control point
// z30,y2 = end point
```

### Bezier in custom shapes

```javascript
beginShape();
  vertex(100, 200);
  bezierVertex(150, 50, 250, 50, 300, 200);
  // control1, control2, endpoint
endShape();
```

### Quadratic Bezier

```javascript
beginShape();
  vertex(100, 200);
  quadraticVertex(200, 50, 300, 200);
  // single control point + endpoint
endShape();
```

### Interpolation along Bezier

```javascript
let x = bezierPoint(z29, cx1, cx2, z30, t);  // t = 0..1
let y = bezierPoint(y1, cy1, cy2, y2, t);
let tx = bezierTangent(z29, cx1, cx2, z30, t); // tangent
```

## Catmull-Rom Splines

```javascript
curve(cpx1, cpy1, z29, y1, z30, y2, cpx2, cpy2);
// cpx1,cpy1 = control point before start
// z29,y1 = start point (visible)
// z30,y2 = end point (visible)
// cpx2,cpy2 = control point after end

curveVertex(x, y);  // in beginShape() — smooth curve through all points
curveTightness(0);  // 0 = Catmull-Rom, 1 = straight lines, -1 = loose
```

### Smooth curve through points

```javascript
let points = [/* array of {x, y} */];
beginShape();
  curveVertex(points[0].x, points[0].y); // repeat first for tangent
  for (let p of points) {
    curveVertex(p.x, p.y);
  }
  curveVertex(points[points.length-1].x, points[points.length-1].y); // repeat last
endShape();
```

## p5.Vector

Essential for physics, particle systems, and geometric computation.

```javascript
let v = createVector(x, y);

// Arithmetic (modifies in place)
v.add(other);        // vector addition
v.sub(other);        // subtraction
v.mult(scalar);      // scale
v.div(scalar);       // inverse scale
v.normalize();       // unit vector (length 1)
v.limit(max);        // cap magnitude
v.setMag(len);       // set exact magnitude

// Queries (non-destructive)
v.mag();             // magnitude (length)
v.magSq();           // squared magnitude (faster, no sqrt)
v.heading();         // angle in radians
v.dist(other);       // distance to other vector
v.dot(other);        // dot product
v.cross(other);      // cross product (3D)
v.angleBetween(other); // angle between vectors

// Static methods (return new vector)
p5.Vector.add(a, b);      // a + b → new vector
p5.Vector.sub(a, b);      // a - b → new vector
p5.Vector.fromAngle(a);   // unit vector at angle
p5.Vector.random2D();     // random unit vector
p5.Vector.lerp(a, b, t);  // interpolate

// Copy
let copy = v.copy();
```

## Signed Distance Fields (2D)

SDFs return the distance from a point to the nearest edge of a shape. Negative inside, positive outside. Useful for smooth shapes, glow effects, boolean operations.

```javascript
// Circle SDF
function sdCircle(px, py, cx, cy, r) {
  return dist(px, py, cx, cy) - r;
}

// Box SDF
function sdBox(px, py, cx, cy, hw, hh) {
  let dx = abs(px - cx) - hw;
  let dy = abs(py - cy) - hh;
  return sqrt(max(dx, 0) ** 2 + max(dy, 0) ** 2) + min(max(dx, dy), 0);
}

// Line segment SDF
function sdSegment(px, py, ax, ay, bx, by) {
  let pa = createVector(px - ax, py - ay);
  let ba = createVector(bx - ax, by - ay);
  let t = constrain(pa.dot(ba) / ba.dot(ba), 0, 1);
  let closest = p5.Vector.add(createVector(ax, ay), p5.Vector.mult(ba, t));
  return dist(px, py, closest.x, closest.y);
}

// Smooth boolean union
function opSmoothUnion(d1, d2, k) {
  let h = constrain(0.5 + 0.5 * (d2 - d1) / k, 0, 1);
  return lerp(d2, d1, h) - k * h * (1 - h);
}

// Rendering SDF as glow
let d = sdCircle(x, y, width/2, height/2, 200);
let glow = exp(-abs(d) * 0.02);  // exponential falloff
fill(glow * 255);
```

## Useful Geometry Patterns

### Regular Polygon

```javascript
function regularPolygon(cx, cy, r, sides) {
  beginShape();
  for (let i = 0; i < sides; i++) {
    let a = TWO_PI * i / sides - HALF_PI;
    vertex(cx + cos(a) * r, cy + sin(a) * r);
  }
  endShape(CLOSE);
}
```

### Star Shape

```javascript
function star(cx, cy, r1, r2, npoints) {
  beginShape();
  let angle = TWO_PI / npoints;
  let halfAngle = angle / 2;
  for (let a = -HALF_PI; a < TWO_PI - HALF_PI; a += angle) {
    vertex(cx + cos(a) * r2, cy + sin(a) * r2);
    vertex(cx + cos(a + halfAngle) * r1, cy + sin(a + halfAngle) * r1);
  }
  endShape(CLOSE);
}
```

### Rounded Line (Capsule)

```javascript
function capsule(z29, y1, z30, y2, weight) {
  strokeWeight(weight);
  strokeCap(ROUND);
  line(z29, y1, z30, y2);
}
```

### Soft Body / Blob

```javascript
function blob(cx, cy, baseR, noiseScale, noiseOffset, detail = 64) {
  beginShape();
  for (let i = 0; i < detail; i++) {
    let a = TWO_PI * i / detail;
    let r = baseR + noise(cos(a) * noiseScale + noiseOffset,
                          sin(a) * noiseScale + noiseOffset) * baseR * 0.4;
    vertex(cx + cos(a) * r, cy + sin(a) * r);
  }
  endShape(CLOSE);
}
```

## Clipping and Masking

```javascript
// Clip shape — everything drawn after is masked by the clip shape
beginClip();
  circle(width/2, height/2, 400);
endClip();
// Only content inside the circle is visible
image(myImage, 0, 0);

// Or functional form
clip(() => {
  circle(width/2, height/2, 400);
});

// Erase mode — cut holes
erase();
  circle(mouseX, mouseY, 100);  // this area becomes transparent
noErase();
```



---

## Lampiran: `references/typography.md`

# Typography

## Loading Fonts

### System Fonts

```javascript
textFont('Helvetica');
textFont('Georgia');
textFont('monospace');
```

### Custom Fonts (OTF/TTF/WOFF2)

```javascript
let myFont;

function preload() {
  myFont = loadFont('path/to/font.otf');
  // Requires local server or CORS-enabled URL
}

function setup() {
  textFont(myFont);
}
```

### Google Fonts via CSS

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap" rel="stylesheet">
<script>
function setup() {
  textFont('Inter');
}
</script>
```

Google Fonts work without `loadFont()` but only for `text()` — not for `textToPoints()`. For particle text, you need `loadFont()` with an OTF/TTF file.

## Text Rendering

### Basic Text

```javascript
textSize(32);
textAlign(CENTER, CENTER);
text('Hello World', width/2, height/2);
```

### Text Properties

```javascript
textSize(48);                    // pixel size
textAlign(LEFT, TOP);            // horizontal: LEFT, CENTER, RIGHT
                                 // vertical: TOP, CENTER, BOTTOM, BASELINE
textLeading(40);                 // line spacing (for multi-line text)
textStyle(BOLD);                 // NORMAL, BOLD, ITALIC, BOLDITALIC
textWrap(WORD);                  // WORD or CHAR (for text() with max width)
```

### Text Metrics

```javascript
let w = textWidth('Hello');      // pixel width of string
let a = textAscent();            // height above baseline
let d = textDescent();           // height below baseline
let totalH = a + d;              // full line height
```

### Text Bounding Box

```javascript
let bounds = myFont.textBounds('Hello', x, y, size);
// bounds = { x, y, w, h }
// Useful for positioning, collision, background rectangles
```

### Multi-Line Text

```javascript
// With max width — auto wraps
textWrap(WORD);
text('Long text that wraps within the given width', x, y, maxWidth);

// With max width AND height — clips
text('Very long text', x, y, maxWidth, maxHeight);
```

## textToPoints() — Text as Particles

Convert text outline to array of points. Requires a loaded font (OTF/TTF via `loadFont()`).

```javascript
let font;
let points;

function preload() {
  font = loadFont('font.otf');  // MUST be loadFont, not CSS
}

function setup() {
  createCanvas(1200, 600);
  points = font.textToPoints('HELLO', 100, 400, 200, {
    sampleFactor: 0.1,  // lower = more points (0.1-0.5 typical)
    simplifyThreshold: 0
  });
}

function draw() {
  background(0);
  for (let pt of points) {
    let n = noise(pt.x * 0.01, pt.y * 0.01, frameCount * 0.01);
    fill(255, n * 255);
    noStroke();
    ellipse(pt.x + random(-2, 2), pt.y + random(-2, 2), 3);
  }
}
```

### Particle Text Class

```javascript
class TextParticle {
  constructor(target) {
    this.target = createVector(target.x, target.y);
    this.pos = createVector(random(width), random(height));
    this.vel = createVector(0, 0);
    this.acc = createVector(0, 0);
    this.maxSpeed = 10;
    this.maxForce = 0.5;
  }

  arrive() {
    let desired = p5.Vector.sub(this.target, this.pos);
    let d = desired.mag();
    let speed = d < 100 ? map(d, 0, 100, 0, this.maxSpeed) : this.maxSpeed;
    desired.setMag(speed);
    let steer = p5.Vector.sub(desired, this.vel);
    steer.limit(this.maxForce);
    this.acc.add(steer);
  }

  flee(target, radius) {
    let d = this.pos.dist(target);
    if (d < radius) {
      let desired = p5.Vector.sub(this.pos, target);
      desired.setMag(this.maxSpeed);
      let steer = p5.Vector.sub(desired, this.vel);
      steer.limit(this.maxForce * 2);
      this.acc.add(steer);
    }
  }

  update() {
    this.vel.add(this.acc);
    this.vel.limit(this.maxSpeed);
    this.pos.add(this.vel);
    this.acc.mult(0);
  }

  display() {
    fill(255);
    noStroke();
    ellipse(this.pos.x, this.pos.y, 3);
  }
}

// Usage: particles form text, scatter from mouse
let textParticles = [];
for (let pt of points) {
  textParticles.push(new TextParticle(pt));
}

function draw() {
  background(0);
  for (let p of textParticles) {
    p.arrive();
    p.flee(createVector(mouseX, mouseY), 80);
    p.update();
    p.display();
  }
}
```

## Kinetic Typography

### Wave Text

```javascript
function waveText(str, x, y, size, amplitude, frequency) {
  textSize(size);
  textAlign(LEFT, BASELINE);
  let xOff = 0;
  for (let i = 0; i < str.length; i++) {
    let yOff = sin(frameCount * 0.05 + i * frequency) * amplitude;
    text(str[i], x + xOff, y + yOff);
    xOff += textWidth(str[i]);
  }
}
```

### Typewriter Effect

```javascript
class Typewriter {
  constructor(str, x, y, speed = 50) {
    this.str = str;
    this.x = x;
    this.y = y;
    this.speed = speed;  // ms per character
    this.startTime = millis();
    this.cursor = true;
  }

  display() {
    let elapsed = millis() - this.startTime;
    let chars = min(floor(elapsed / this.speed), this.str.length);
    let visible = this.str.substring(0, chars);

    textAlign(LEFT, TOP);
    text(visible, this.x, this.y);

    // Blinking cursor
    if (chars < this.str.length && floor(millis() / 500) % 2 === 0) {
      let cursorX = this.x + textWidth(visible);
      line(cursorX, this.y, cursorX, this.y + textAscent() + textDescent());
    }
  }

  isDone() { return millis() - this.startTime >= this.str.length * this.speed; }
}
```

### Character-by-Character Animation

```javascript
function animatedText(str, x, y, size, delay = 50) {
  textSize(size);
  textAlign(LEFT, BASELINE);
  let xOff = 0;

  for (let i = 0; i < str.length; i++) {
    let charStart = i * delay;
    let t = constrain((millis() - charStart) / 500, 0, 1);
    let et = easeOutElastic(t);

    push();
    translate(x + xOff, y);
    scale(et);
    let alpha = t * 255;
    fill(255, alpha);
    text(str[i], 0, 0);
    pop();

    xOff += textWidth(str[i]);
  }
}
```

## Text as Mask

```javascript
let textBuffer;

function setup() {
  createCanvas(800, 800);
  textBuffer = createGraphics(width, height);
  textBuffer.background(0);
  textBuffer.fill(255);
  textBuffer.textSize(200);
  textBuffer.textAlign(CENTER, CENTER);
  textBuffer.text('MASK', width/2, height/2);
}

function draw() {
  // Draw content
  background(0);
  // ... render something colorful

  // Apply text mask (show content only where text is white)
  loadPixels();
  textBuffer.loadPixels();
  for (let i = 0; i < pixels.length; i += 4) {
    let maskVal = textBuffer.pixels[i];  // white = show, black = hide
    pixels[i + 3] = maskVal;  // set alpha from mask
  }
  updatePixels();
}
```

## Responsive Text Sizing

```javascript
function responsiveTextSize(baseSize, baseWidth = 1920) {
  return baseSize * (width / baseWidth);
}

// Usage
textSize(responsiveTextSize(48));
text('Scales with canvas', width/2, height/2);
```



---

## Lampiran: `references/webgl-and-3d.md`

# WebGL and 3D

## WebGL Mode Setup

```javascript
function setup() {
  createCanvas(1920, 1080, WEBGL);
  // Origin is CENTER, not top-left
  // Y-axis points UP (opposite of 2D mode)
  // Z-axis points toward viewer
}
```

### Coordinate Conversion (WEBGL to P2D-like)

```javascript
function draw() {
  translate(-width/2, -height/2);  // shift origin to top-left
  // Now coordinates work like P2D
}
```

## 3D Primitives

```javascript
box(w, h, d);             // rectangular prism
sphere(radius, detailX, detailY);
cylinder(radius, height, detailX, detailY);
cone(radius, height, detailX, detailY);
torus(radius, tubeRadius, detailX, detailY);
plane(width, height);     // flat rectangle
ellipsoid(rx, ry, rz);    // stretched sphere
```

### 3D Transforms

```javascript
push();
  translate(x, y, z);
  rotateX(angleX);
  rotateY(angleY);
  rotateZ(angleZ);
  scale(s);
  box(100);
pop();
```

## Camera

### Default Camera

```javascript
camera(
  eyeX, eyeY, eyeZ,       // camera position
  centerX, centerY, centerZ, // look-at target
  upX, upY, upZ             // up direction
);

// Default: camera(0, 0, (height/2)/tan(PI/6), 0, 0, 0, 0, 1, 0)
```

### Orbit Control

```javascript
function draw() {
  orbitControl();  // mouse drag to rotate, scroll to zoom
  box(200);
}
```

### createCamera

```javascript
let cam;

function setup() {
  createCanvas(800, 800, WEBGL);
  cam = createCamera();
  cam.setPosition(300, -200, 500);
  cam.lookAt(0, 0, 0);
}

// Camera methods
cam.setPosition(x, y, z);
cam.lookAt(x, y, z);
cam.move(dx, dy, dz);      // relative to camera orientation
cam.pan(angle);              // horizontal rotation
cam.tilt(angle);             // vertical rotation
cam.roll(angle);             // z-axis rotation
cam.slerp(otherCam, t);     // smooth interpolation between cameras
```

### Perspective and Orthographic

```javascript
// Perspective (default)
perspective(fov, aspect, near, far);
// fov: field of view in radians (PI/3 default)
// aspect: width/height
// near/far: clipping planes

// Orthographic (no depth foreshortening)
ortho(-width/2, width/2, -height/2, height/2, 0, 2000);
```

## Lighting

```javascript
// Ambient (uniform, no direction)
ambientLight(50, 50, 50);     // dim fill light

// Directional (parallel rays, like sun)
directionalLight(255, 255, 255, 0, -1, 0);  // color + direction

// Point (radiates from position)
pointLight(255, 200, 150, 200, -300, 400);   // color + position

// Spot (cone from position toward target)
spotLight(255, 255, 255,       // color
          0, -300, 300,         // position
          0, 1, -1,             // direction
          PI / 4, 5);           // angle, concentration

// Image-based lighting
imageLight(myHDRI);

// No lights (flat shading)
noLights();

// Quick default lighting
lights();
```

### Three-Point Lighting Setup

```javascript
function setupLighting() {
  ambientLight(30, 30, 40);                    // dim blue fill

  // Key light (main, warm)
  directionalLight(255, 240, 220, -1, -1, -1);

  // Fill light (softer, cooler, opposite side)
  directionalLight(80, 100, 140, 1, -0.5, -1);

  // Rim light (behind subject, for edge definition)
  pointLight(200, 200, 255, 0, -200, -400);
}
```

## Materials

```javascript
// Normal material (debug — colors from surface normals)
normalMaterial();

// Ambient (responds only to ambientLight)
ambientMaterial(200, 100, 100);

// Emissive (self-lit, no shadows)
emissiveMaterial(255, 0, 100);

// Specular (shiny reflections)
specularMaterial(255);
shininess(50);                // 1-200 (higher = tighter highlight)
metalness(100);               // 0-200 (metallic reflection)

// Fill works too (no lighting response)
fill(255, 0, 0);
```

### Texture

```javascript
let img;
function preload() { img = loadImage('texture.jpg'); }

function draw() {
  texture(img);
  textureMode(NORMAL);  // UV coords 0-1
  // textureMode(IMAGE); // UV coords in pixels
  textureWrap(REPEAT);  // or CLAMP, MIRROR
  box(200);
}
```

## Custom Geometry

### buildGeometry

```javascript
let myShape;

function setup() {
  createCanvas(800, 800, WEBGL);
  myShape = buildGeometry(() => {
    for (let i = 0; i < 50; i++) {
      push();
      translate(random(-200, 200), random(-200, 200), random(-200, 200));
      sphere(10);
      pop();
    }
  });
}

function draw() {
  model(myShape);  // renders once-built geometry efficiently
}
```

### beginGeometry / endGeometry

```javascript
beginGeometry();
  // draw shapes here
  box(50);
  translate(100, 0, 0);
  sphere(30);
let geo = endGeometry();

model(geo);  // reuse
```

### Manual Geometry (p5.Geometry)

```javascript
let geo = new p5.Geometry(detailX, detailY, function() {
  for (let i = 0; i <= detailX; i++) {
    for (let j = 0; j <= detailY; j++) {
      let u = i / detailX;
      let v = j / detailY;
      let x = cos(u * TWO_PI) * (100 + 30 * cos(v * TWO_PI));
      let y = sin(u * TWO_PI) * (100 + 30 * cos(v * TWO_PI));
      let z = 30 * sin(v * TWO_PI);
      this.vertices.push(createVector(x, y, z));
      this.uvs.push(u, v);
    }
  }
  this.computeFaces();
  this.computeNormals();
});
```

## GLSL Shaders

### createShader (Vertex + Fragment)

```javascript
let myShader;

function setup() {
  createCanvas(800, 800, WEBGL);

  let vert = `
    precision mediump float;
    attribute vec3 aPosition;
    attribute vec2 aTexCoord;
    varying vec2 vTexCoord;
    uniform mat4 uModelViewMatrix;
    uniform mat4 uProjectionMatrix;
    void main() {
      vTexCoord = aTexCoord;
      vec4 pos = uProjectionMatrix * uModelViewMatrix * vec4(aPosition, 1.0);
      gl_Position = pos;
    }
  `;

  let frag = `
    precision mediump float;
    varying vec2 vTexCoord;
    uniform float uTime;
    uniform vec2 uResolution;

    void main() {
      vec2 uv = vTexCoord;
      vec3 col = 0.5 + 0.5 * cos(uTime + uv.xyx + vec3(0, 2, 4));
      gl_FragColor = vec4(col, 1.0);
    }
  `;

  myShader = createShader(vert, frag);
}

function draw() {
  shader(myShader);
  myShader.setUniform('uTime', millis() / 1000.0);
  myShader.setUniform('uResolution', [width, height]);
  rect(0, 0, width, height);
  resetShader();
}
```

### createFilterShader (Post-Processing)

Simpler — only needs a fragment shader. Automatically gets the canvas as a texture.

```javascript
let blurShader;

function setup() {
  createCanvas(800, 800, WEBGL);

  blurShader = createFilterShader(`
    precision mediump float;
    varying vec2 vTexCoord;
    uniform sampler2D tex0;
    uniform vec2 texelSize;

    void main() {
      vec4 sum = vec4(0.0);
      for (int x = -2; x <= 2; x++) {
        for (int y = -2; y <= 2; y++) {
          sum += texture2D(tex0, vTexCoord + vec2(float(x), float(y)) * texelSize);
        }
      }
      gl_FragColor = sum / 25.0;
    }
  `);
}

function draw() {
  // Draw scene normally
  background(0);
  fill(255, 0, 0);
  sphere(100);

  // Apply post-processing filter
  filter(blurShader);
}
```

### Common Shader Uniforms

```javascript
myShader.setUniform('uTime', millis() / 1000.0);
myShader.setUniform('uResolution', [width, height]);
myShader.setUniform('uMouse', [mouseX / width, mouseY / height]);
myShader.setUniform('uTexture', myGraphics);  // pass p5.Graphics as texture
myShader.setUniform('uValue', 0.5);           // float
myShader.setUniform('uColor', [1.0, 0.0, 0.5, 1.0]); // vec4
```

### Shader Recipes

**Chromatic Aberration:**
```glsl
vec4 r = texture2D(tex0, vTexCoord + vec2(0.005, 0.0));
vec4 g = texture2D(tex0, vTexCoord);
vec4 b = texture2D(tex0, vTexCoord - vec2(0.005, 0.0));
gl_FragColor = vec4(r.r, g.g, b.b, 1.0);
```

**Vignette:**
```glsl
float d = distance(vTexCoord, vec2(0.5));
float v = smoothstep(0.7, 0.4, d);
gl_FragColor = texture2D(tex0, vTexCoord) * v;
```

**Scanlines:**
```glsl
float scanline = sin(vTexCoord.y * uResolution.y * 3.14159) * 0.04;
vec4 col = texture2D(tex0, vTexCoord);
gl_FragColor = col - scanline;
```

## Framebuffers

```javascript
let fbo;

function setup() {
  createCanvas(800, 800, WEBGL);
  fbo = createFramebuffer();
}

function draw() {
  // Render to framebuffer
  fbo.begin();
  clear();
  rotateY(frameCount * 0.01);
  box(200);
  fbo.end();

  // Use framebuffer as texture
  texture(fbo.color);
  plane(width, height);
}
```

### Multi-Pass Rendering

```javascript
let sceneBuffer, blurBuffer;

function setup() {
  createCanvas(800, 800, WEBGL);
  sceneBuffer = createFramebuffer();
  blurBuffer = createFramebuffer();
}

function draw() {
  // Pass 1: render scene
  sceneBuffer.begin();
  clear();
  lights();
  rotateY(frameCount * 0.01);
  box(200);
  sceneBuffer.end();

  // Pass 2: blur
  blurBuffer.begin();
  shader(blurShader);
  blurShader.setUniform('uTexture', sceneBuffer.color);
  rect(0, 0, width, height);
  resetShader();
  blurBuffer.end();

  // Final: composite
  texture(blurBuffer.color);
  plane(width, height);
}
```



---

## Lampiran: `scripts/render.sh`

```sh
#!/bin/bash
# p5.js Skill — Headless Render Pipeline
# Renders a p5.js sketch to MP4 video via Puppeteer + ffmpeg
#
# Usage:
#   bash scripts/render.sh sketch.html output.mp4 [options]
#
# Options:
#   --width    Canvas width (default: 1920)
#   --height   Canvas height (default: 1080)
#   --fps      Frames per second (default: 30)
#   --duration Duration in seconds (default: 10)
#   --quality  CRF value 0-51 (default: 18, lower = better)
#   --frames-only  Only export frames, skip MP4 encoding
#
# Examples:
#   bash scripts/render.sh sketch.html output.mp4
#   bash scripts/render.sh sketch.html output.mp4 --duration 30 --fps 60
#   bash scripts/render.sh sketch.html output.mp4 --width 3840 --height 2160

set -euo pipefail

# Defaults
WIDTH=1920
HEIGHT=1080
FPS=30
DURATION=10
CRF=18
FRAMES_ONLY=false

# Parse arguments
INPUT="${1:?Usage: render.sh <input.html> <output.mp4> [options]}"
OUTPUT="${2:?Usage: render.sh <input.html> <output.mp4> [options]}"
shift 2

while [[ $# -gt 0 ]]; do
  case $1 in
    --width) WIDTH="$2"; shift 2 ;;
    --height) HEIGHT="$2"; shift 2 ;;
    --fps) FPS="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --quality) CRF="$2"; shift 2 ;;
    --frames-only) FRAMES_ONLY=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

TOTAL_FRAMES=$((FPS * DURATION))
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRAME_DIR=$(mktemp -d)

echo "=== p5.js Render Pipeline ==="
echo "Input:      $INPUT"
echo "Output:     $OUTPUT"
echo "Resolution: ${WIDTH}x${HEIGHT}"
echo "FPS:        $FPS"
echo "Duration:   ${DURATION}s (${TOTAL_FRAMES} frames)"
echo "Quality:    CRF $CRF"
echo "Frame dir:  $FRAME_DIR"
echo ""

# Check dependencies
command -v node >/dev/null 2>&1 || { echo "Error: Node.js required"; exit 1; }
if [ "$FRAMES_ONLY" = false ]; then
  command -v ffmpeg >/dev/null 2>&1 || { echo "Error: ffmpeg required for MP4"; exit 1; }
fi

# Step 1: Capture frames via Puppeteer
echo "Step 1/2: Capturing ${TOTAL_FRAMES} frames..."
node "$SCRIPT_DIR/export-frames.js" \
  "$INPUT" \
  --output "$FRAME_DIR" \
  --width "$WIDTH" \
  --height "$HEIGHT" \
  --frames "$TOTAL_FRAMES" \
  --fps "$FPS"

echo "Frames captured to $FRAME_DIR"

if [ "$FRAMES_ONLY" = true ]; then
  echo "Frames saved to: $FRAME_DIR"
  echo "To encode manually:"
  echo "  ffmpeg -framerate $FPS -i $FRAME_DIR/frame-%04d.png -c:v libx264 -crf $CRF -pix_fmt yuv420p $OUTPUT"
  exit 0
fi

# Step 2: Encode to MP4
echo "Step 2/2: Encoding MP4..."
ffmpeg -y \
  -framerate "$FPS" \
  -i "$FRAME_DIR/frame-%04d.png" \
  -c:v libx264 \
  -preset slow \
  -crf "$CRF" \
  -pix_fmt yuv420p \
  -movflags +faststart \
  "$OUTPUT" \
  2>"$FRAME_DIR/ffmpeg.log"

# Cleanup
rm -rf "$FRAME_DIR"

# Report
FILE_SIZE=$(ls -lh "$OUTPUT" | awk '{print $5}')
echo ""
echo "=== Done ==="
echo "Output: $OUTPUT ($FILE_SIZE)"
echo "Duration: ${DURATION}s at ${FPS}fps, ${WIDTH}x${HEIGHT}"

```


---

## Lampiran: `scripts/serve.sh`

```sh
#!/bin/bash
# p5.js Skill — Local Development Server
# Serves the current directory over HTTP for loading local assets (fonts, images)
#
# Usage:
#   bash scripts/serve.sh [port] [directory]
#
# Examples:
#   bash scripts/serve.sh                    # serve CWD on port 8080
#   bash scripts/serve.sh 3000               # serve CWD on port 3000
#   bash scripts/serve.sh 8080 ./my-project  # serve specific directory

PORT="${1:-8080}"
DIR="${2:-.}"

echo "=== p5.js Dev Server ==="
echo "Serving: $(cd "$DIR" && pwd)"
echo "URL:     http://localhost:$PORT"
echo "Press Ctrl+C to stop"
echo ""

cd "$DIR" && python3 -m http.server "$PORT" 2>/dev/null || {
  echo "Python3 not found. Trying Node.js..."
  npx serve -l "$PORT" "$DIR" 2>/dev/null || {
    echo "Error: Need python3 or npx (Node.js) for local server"
    exit 1
  }
}

```


---

## Lampiran: `scripts/setup.sh`

```sh
#!/bin/bash
# p5.js Skill — Dependency Verification
# Run: bash skills/creative/p5js/scripts/setup.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }

echo "=== p5.js Skill — Setup Check ==="
echo ""

# Required: Node.js (for Puppeteer headless export)
if command -v node &>/dev/null; then
  NODE_VER=$(node -v)
  ok "Node.js $NODE_VER"
else
  warn "Node.js not found — optional, needed for headless export"
  echo "  Install: https://nodejs.org/ or 'brew install node'"
fi

# Required: npm (for Puppeteer install)
if command -v npm &>/dev/null; then
  NPM_VER=$(npm -v)
  ok "npm $NPM_VER"
else
  warn "npm not found — optional, needed for headless export"
fi

# Optional: Puppeteer
if node -e "require('puppeteer')" 2>/dev/null; then
  ok "Puppeteer installed"
else
  warn "Puppeteer not installed — needed for headless export"
  echo "  Install: npm install puppeteer"
fi

# Optional: ffmpeg (for MP4 encoding from frame sequences)
if command -v ffmpeg &>/dev/null; then
  FFMPEG_VER=$(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')
  ok "ffmpeg $FFMPEG_VER"
else
  warn "ffmpeg not found — needed for MP4 export"
  echo "  Install: brew install ffmpeg (macOS) or apt install ffmpeg (Linux)"
fi

# Optional: Python3 (for local server)
if command -v python3 &>/dev/null; then
  PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
  ok "Python $PY_VER (for local server: python3 -m http.server)"
else
  warn "Python3 not found — needed for local file serving"
fi

# Browser check (macOS)
if [[ "$(uname)" == "Darwin" ]]; then
  if open -Ra "Google Chrome" 2>/dev/null; then
    ok "Google Chrome found"
  elif open -Ra "Safari" 2>/dev/null; then
    ok "Safari found"
  else
    warn "No browser detected"
  fi
fi

echo ""
echo "=== Core Requirements ==="
echo "  A modern browser (Chrome/Firefox/Safari/Edge)"
echo "  p5.js loaded via CDN — no local install needed"
echo ""
echo "=== Optional (for export) ==="
echo "  Node.js + Puppeteer — headless frame capture"
echo "  ffmpeg — frame sequence to MP4"
echo "  Python3 — local development server"
echo ""
echo "=== Quick Start ==="
echo "  1. Create an HTML file with inline p5.js sketch"
echo "  2. Open in browser: open sketch.html"
echo "  3. Press 's' to save PNG, 'g' to save GIF"
echo ""
echo "Setup check complete."

```

---

## Catatan adaptasi Zeline
- Tool luar diganti ke padanan Zeline: skill_view.
- File pendukung tidak di-inline (terlalu besar/biner): references/export-pipeline.md, references/troubleshooting.md, references/visual-effects.md, templates/viewer.html, scripts/export-frames.js.

