# Troubleshooting

Diagnosing p5.js sketches: performance, the mistakes that actually recur, and the
platform traps.

## Performance

### Measure before changing anything

```javascript
// Once a second, not every frame — logging in draw() is itself a bottleneck.
if (frameCount % 60 === 0) console.log(`fps ${frameRate().toFixed(1)}`);
```

To find *where* the time goes, bracket suspects rather than guessing:

```javascript
const t0 = performance.now();
drawParticles();
const t1 = performance.now();
if (frameCount % 60 === 0) console.log(`particles ${(t1 - t0).toFixed(1)}ms`);
```

A 60 fps budget is 16.7 ms per frame, and the browser needs part of it to composite.
Treat ~12 ms of sketch work as the ceiling.

### Per-pixel budgets

| Canvas | Pixels at density 1 | At density 2 |
|---|---|---|
| 400² | 160k | 640k |
| 800² | 640k | 2.6M |
| 1080² | 1.2M | 4.7M |
| 1920×1080 | 2.1M | 8.3M |

A per-pixel loop at 1080² and density 2 is 4.7 million iterations *per frame*. That
cannot hold 60 fps in JavaScript. Options, in order of preference: drop
`pixelDensity(1)`, work in a smaller offscreen buffer and upscale, or move the effect
to a shader.

### The usual culprits

| Cost | Cheaper alternative |
|---|---|
| `get()` / `set()` per pixel | `loadPixels()` once, index `pixels` directly |
| `noise()` per agent per frame | precompute a flow field grid |
| O(n²) neighbour search | spatial hash / grid bins |
| `createGraphics()` inside `draw()` | create once in `setup()`, reuse |
| `loadFont()` / `loadImage()` in `draw()` | preload, keep the handle |
| `text()` with a custom font per frame | render once to a buffer, blit it |
| `filter()` on the main canvas each frame | apply to a smaller buffer |
| Shadows via many overlapping shapes | one blurred offscreen pass |

### Object churn

```javascript
// Allocates two vectors per particle per frame; the GC pauses show as stutter.
const force = p5.Vector.sub(target, particle.pos).normalize().mult(0.5);

// Reuse a scratch vector instead.
scratch.set(target.x - particle.pos.x, target.y - particle.pos.y);
scratch.setMag(0.5);
particle.applyForce(scratch);
```

## Common Mistakes

**Forces accumulating.** `acc.mult(0)` at the end of every `update()`. Without it
each force integrates forever and particles leave the canvas in about a second.

**`random()` inside `draw()` for static content.** Values change every frame, so the
"still" image shimmers. Generate once in `setup()` and store, or seed with
`randomSeed()` and use `noLoop()`.

**`randomSeed()` without `noiseSeed()`.** Both must be set for a reproducible frame;
seeding one leaves the other free-running.

**`pixelDensity()` ignored in pixel maths.** The `pixels` array is
`width * density * height * density * 4`. Index arithmetic that assumes density 1
reads the wrong pixels on every retina display — the effect looks fine on a desktop
monitor and broken on a phone.

**Missing `pop()`.** An unmatched `push()` leaks transform state into the next
frame; the drawing drifts or rotates cumulatively. One `pop()` per `push()`, every
path through the function.

**Colour created per frame.** `color()` allocates. Build palettes in `setup()`.

**`textAlign()`/`textSize()` set once, expected to persist across a `pop()`.** Text
state is part of the style stack and is restored on `pop()` like everything else.

**Mutating a vector you did not intend to own.** `p5.Vector.add()` is static and
returns a new vector; `vector.add()` mutates in place. Mixing them up corrupts
shared state — usually seen as every particle sharing one position.

**`console.log()` inside `draw()`.** At 60 fps that is 60 DOM writes per second and
it alone can halve the frame rate. Never in `draw()`; gate on `frameCount`.

**DOM manipulation inside `draw()`.** Same reason: layout and style recalculation
per frame. Update DOM on events only.

## Browser Compatibility

| Feature | Note |
|---|---|
| `saveGif()` | p5 ≥ 1.5; blocks the main thread while encoding |
| WEBGL2 / instancing | not on older iOS Safari; feature-detect |
| `OffscreenCanvas` | uneven support; `createGraphics()` is the portable choice |
| Audio input | requires a user gesture *and* HTTPS (localhost exempt) |
| `saveCanvas()` on iOS | may open a share sheet instead of downloading |
| Display-P3 canvases | Safari renders wider gamut; colours shift vs Chrome |
| Touch events | `mousePressed` fires for touch, but `touches[]` gives multitouch |

## WebGL Debugging

**Blank canvas.** Check `background()` is called — WEBGL does not clear by default in
every path, and the default camera looks at the origin, so geometry drawn at
`(width/2, height/2)` as in 2D mode is offscreen. In WEBGL the origin is the centre.

**Shader compiles but nothing renders.** Verify the varying names match exactly
between vertex and fragment shaders, and that the fragment shader has a `precision`
qualifier:

```glsl
precision mediump float;   // required on mobile GPUs; absent = silent failure
```

**Texture is black.** The image must be fully loaded before `shader()`/`texture()`.
Use the `loadImage()` callback, not a timer.

**Headless render is blank.** Headless Chrome has no GPU by default; launch with
`--use-gl=swiftshader` (already done by `scripts/export-frames.js`).

**Framebuffer feedback saturates.** Every feedback transform must be a contraction
(`scale` < 1) or the image blows out within a few dozen frames.

**Lost context after a resize.** Recreate shaders and framebuffers on
`windowResized()`; they do not survive a canvas resize.

## Font Loading

```javascript
let font;
function preload() { font = loadFont('font.ttf'); }   // preload blocks setup, which is what you want
function setup() { textFont(font); }
```

Using `loadFont()` in `setup()` without a callback means `textFont()` runs against an
unloaded handle and the first frames render in the fallback font — which is exactly
the "first frames look wrong" report in headless captures. Set `window._p5Ready` in
the load callback, not at the end of `setup()`.

`textToPoints()` needs an OpenType font (`.ttf`/`.otf`); it returns nothing for
web-safe font names.

## Pixel Density Traps

```javascript
pixelDensity(1);   // predictable pixel maths, 4× less work on retina
```

- `width`/`height` are CSS pixels; the backing store is `width * pixelDensity()`.
- `get()`/`set()` work in CSS pixels; `pixels[]` works in device pixels.
- `createGraphics()` inherits the main canvas density unless you set it — a mismatch
  makes `image()` blit at half or double size.
- Screenshots taken by a headless browser are in device pixels; `deviceScaleFactor`
  must match what the sketch expects.

## Memory Leaks

| Leak | Fix |
|---|---|
| `createGraphics()` in a loop | create once; `remove()` when done |
| Framebuffers recreated per frame | allocate in `setup()` |
| Particle array that only grows | remove dead particles (`splice` or filter) |
| Event listeners re-added on resize | remove before re-adding |
| Retained image references | null them out after use |

Watch the tab's memory over a minute of running. Steady growth that never drops
after GC is a real leak; sawtooth is normal.

## CORS

Loading an image or font from another origin taints the canvas, and
`saveCanvas()`/`loadPixels()` then throw a security error.

```javascript
// Serve assets from the same origin, or ensure the host sends
// Access-Control-Allow-Origin and request it explicitly:
loadImage('https://example.com/pic.png', img => { /* ... */ });
```

`file://` is its own origin and blocks nearly everything. Serve locally instead:

```bash
python3 -m http.server 8000     # then open http://localhost:8000/sketch.html
```

Audio input additionally requires HTTPS or localhost — no exceptions.
