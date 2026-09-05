# Export Pipeline

Getting a sketch out as a PNG, a GIF, or a video — and getting the frame timing
right, which is where almost all the failures are.

## Stills

```javascript
saveCanvas('artwork', 'png');            // canvas as-is
save('artwork.png');                     // same, shorter
```

For print resolution, render into an offscreen buffer at the target size instead of
resizing the display canvas — resizing resamples and softens every edge.

```javascript
function exportHighRes(scale = 4) {
  const big = createGraphics(width * scale, height * scale);
  big.pixelDensity(1);          // the buffer IS the resolution; density would multiply again
  drawSketch(big, scale);       // the sketch must accept a target + scale
  big.save('artwork-4x.png');
  big.remove();                 // free it, or repeated exports leak GPU memory
}
```

Writing the sketch to take a target (`p`) rather than using globals is what makes
this possible at all. A sketch that calls bare `circle()` can only ever draw to the
main canvas.

## GIF

```javascript
saveGif('loop', 6);                       // 6 seconds at the current frame rate
saveGif('loop', 120, { units: 'frames' });
```

Built in since p5 1.5. It blocks the main thread while encoding — a 10-second GIF
at 1080² will appear to hang the tab for a while. Keep loops short and small; GIF
tops out at 256 colours per frame, so gradients band badly. For anything longer or
smoother, export frames and encode with ffmpeg.

## Frame sequences

```javascript
saveFrames('frame', 'png', 5, 60);       // 5 seconds at 60 fps, from the browser
```

Browser-side `saveFrames()` triggers one download per frame — a few hundred
downloads is unusable in practice. Use it for a handful of frames; use the headless
route below for anything real.

## Deterministic Capture

The requirement: output frame N must correspond to the sketch's own frame N. A
screenshot of a *running* animation captures whatever the browser happened to paint
when the shot landed, so the result stutters differently on every machine.

Three things make it exact:

1. the sketch calls `noLoop()` — nothing advances on its own;
2. the sketch sets `window._p5Ready = true` at the end of `setup()`, *after* fonts,
   shaders and images have loaded (constructed is not loaded);
3. the capture script calls `redraw()` exactly once per frame.

```javascript
let frame = 0;

function setup() {
  createCanvas(1080, 1080);
  noLoop();
  loadFont('font.ttf', () => { window._p5Ready = true; });   // ready AFTER the load
}

function draw() {
  // Drive everything from an explicit counter, never millis() or frameCount,
  // because a headless run advances neither on a wall clock.
  const t = frame / 300;
  background(14);
  // ...draw at time t...
  frame++;
}
```

Then:

```bash
node scripts/export-frames.js sketch.html --frames 300 --width 1080 --height 1080
ffmpeg -framerate 60 -i frames/frame-0%04d.png -c:v libx264 -pix_fmt yuv420p out.mp4
```

`scripts/export-frames.js` waits for `_p5Ready` rather than sleeping a fixed
interval: fonts and shaders load asynchronously, so a fixed delay either wastes
seconds per run or captures a half-built first frame. It also warns when the sketch
is still looping, since that silently breaks frame correspondence.

## ffmpeg

```bash
# Frames to MP4 (H.264, widely compatible)
ffmpeg -framerate 60 -i frames/frame-%05d.png \
       -c:v libx264 -pix_fmt yuv420p -crf 18 out.mp4

# Loop a short clip to a target length
ffmpeg -stream_loop 9 -i clip.mp4 -c copy looped.mp4

# MP4 to high-quality GIF via a palette (two passes)
ffmpeg -i out.mp4 -vf "fps=24,scale=640:-1:flags=lanczos,palettegen" palette.png
ffmpeg -i out.mp4 -i palette.png \
       -lavfi "fps=24,scale=640:-1:flags=lanczos[x];[x][1:v]paletteuse" out.gif
```

`-pix_fmt yuv420p` is not optional if the video must play on iOS, Safari, or in
Telegram: the default `yuv444p` from PNG input is rejected by those decoders and you
get audio-only or a black frame. Odd pixel dimensions fail H.264 encoding outright —
pad with `-vf "pad=ceil(iw/2)*2:ceil(ih/2)*2"`.

## CCapture.js

In-browser capture with real frame locking, for when a headless run is not
available.

```html
<script src="https://cdn.jsdelivr.net/npm/ccapture.js@1.1.0/build/CCapture.all.min.js"></script>
```

```javascript
const capturer = new CCapture({ format: 'webm', framerate: 60 });
function keyPressed() {
  if (key === 'r') { capturer.start(); }
  if (key === 's') { capturer.stop(); capturer.save(); }
}
function draw() {
  // ...
  capturer.capture(document.querySelector('canvas'));
}
```

It overrides `requestAnimationFrame` so the page renders in lockstep with capture —
the tab will feel frozen; that is correct behaviour, not a hang.

## SVG

```html
<script src="https://cdn.jsdelivr.net/npm/p5.js-svg@1.5.1/dist/p5.js-svg.min.js"></script>
```

```javascript
function setup() { createCanvas(800, 800, SVG); }
function keyPressed() { if (key === 's') save('artwork.svg'); }
```

Vector output for plotters and print. WEBGL, `filter()`, `blendMode()` and per-pixel
work are unavailable in SVG mode — check the renderer before reaching for them:

```javascript
const isSvg = typeof SVG !== 'undefined' && drawingContext instanceof SVGElement;
```

## Per-Clip Architecture

For multi-scene videos, render one HTML per scene and stitch. One long sketch with
internal scene switching is worse in every dimension: a mistake in scene 4 forces a
re-render of 1–3, and browser memory grows across a long capture until frames start
dropping.

```bash
for scene in scene-*.html; do
  node scripts/export-frames.js "$scene" --frames 180 --out "frames-${scene%.html}"
  ffmpeg -framerate 60 -i "frames-${scene%.html}/frame-%04d.png" \
         -c:v libx264 -pix_fmt yuv420p "${scene%.html}.mp4"
done
printf "file '%s'\n" scene-*.mp4 > list.txt
ffmpeg -f concat -safe 0 -i list.txt -c copy final.mp4
```

`-c copy` in the concat step requires identical codec, resolution and frame rate
across clips; otherwise re-encode. Keep one shared constants file so every scene
agrees on dimensions.

## Platform Export (fxhash and similar)

Generative-token platforms require deterministic output from a supplied hash:

```javascript
// The platform injects $fx.hash / fxhash. Seed EVERYTHING from it.
const seed = parseInt(String($fx.hash).slice(2, 10), 16);
randomSeed(seed);
noiseSeed(seed);

// Declare the traits the platform will index
$fx.features({ palette: paletteName, density: densityTier });

// Signal that the still frame is ready to be captured
$fx.preview();
```

One unseeded `Math.random()` anywhere and the same token renders differently on
every view, which is a hard rejection. Grep the sketch for `Math.random` and bare
`random(` before submitting.

## Video Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Video plays black / audio only | `yuv444p` from PNG input | `-pix_fmt yuv420p` |
| ffmpeg refuses to encode | odd width or height | pad to even dimensions |
| Motion judders | captured while looping | `noLoop()` + one `redraw()` per frame |
| First frames empty | captured before assets loaded | set `_p5Ready` after load callbacks |
| Frames drift out of sync | timing from `millis()`/`frameCount` | drive from an explicit counter |
| Colours shift vs the browser | canvas is Display-P3, video is Rec.709 | render in sRGB |
| GIF bands heavily | 256-colour limit | two-pass `palettegen`/`paletteuse` |
| Memory grows over a long capture | buffers not freed | `remove()` offscreen graphics; render per clip |
