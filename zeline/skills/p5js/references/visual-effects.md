# Visual Effects

Noise, flow fields, particle systems, pixel manipulation, and texture generation.

Everything here assumes a seeded generator when the output must be reproducible —
`randomSeed()` and `noiseSeed()` together, or an explicit PRNG. One unseeded
`random()` call anywhere in the chain and the same seed renders a different image.

## Noise

### Perlin (built in)

`noise()` returns 0..1 and is *smooth*: nearby inputs give nearby outputs, which is
what makes it usable for organic motion. `random()` has no such continuity.

```javascript
noiseSeed(42);
noiseDetail(4, 0.5);   // octaves, falloff — see below

// 1D: a wandering value over time
const y = noise(frameCount * 0.01) * height;

// 2D: a field over the canvas
const value = noise(x * 0.005, y * 0.005);

// 3D: a field that also evolves — the usual choice for animated texture
const value3 = noise(x * 0.005, y * 0.005, frameCount * 0.01);
```

The multiplier on the coordinates *is* the feature size. Roughly:

| Multiplier | Character |
|---|---|
| `0.001–0.003` | broad, continent-scale gradients |
| `0.005–0.01` | organic clouds, the default useful range |
| `0.02–0.05` | tight ripples |
| `> 0.1` | close to noise-as-static; use `random()` instead |

### Octaves and falloff

`noiseDetail(octaves, falloff)` sums progressively finer, weaker copies. More
octaves add detail and cost; falloff controls roughness.

```javascript
noiseDetail(1, 0.5);   // smooth blobs
noiseDetail(4, 0.5);   // p5 default, balanced
noiseDetail(8, 0.65);  // rough, ridged, expensive
```

### Fractal noise by hand

Do it manually when you need per-layer control (different seeds, warped layers, or
ridged forms), because `noiseDetail()` is global state and applies to every call.

```javascript
function fbm(x, y, octaves = 4, lacunarity = 2, gain = 0.5) {
  let sum = 0, amplitude = 1, frequency = 1, norm = 0;
  for (let i = 0; i < octaves; i++) {
    sum += amplitude * noise(x * frequency, y * frequency);
    norm += amplitude;
    amplitude *= gain;
    frequency *= lacunarity;
  }
  return sum / norm;          // normalise, or brightness drifts with octaves
}

// Ridged: fold the field to get creases instead of blobs
function ridged(x, y) {
  return 1 - Math.abs(fbm(x, y) * 2 - 1);
}
```

### Domain warping

Feed noise output back in as noise *input*. This is the cheapest route to flowing,
marbled, organic forms — the single highest-value trick in this file.

```javascript
function warped(x, y, strength = 60) {
  const qx = noise(x * 0.004, y * 0.004);
  const qy = noise(x * 0.004 + 5.2, y * 0.004 + 1.3);   // offset = decorrelate
  return fbm(x * 0.004 + strength * qx * 0.004,
             y * 0.004 + strength * qy * 0.004);
}
```

Warp twice for a marbling effect. Offsetting the second sample (`+5.2`, `+1.3`) is
required: sampling the same coordinates twice gives identical values and the warp
collapses to a diagonal stretch.

### Curl noise

Divergence-free flow — particles circulate instead of piling into sinks, which is
what makes smoke and ink look right.

```javascript
function curl(x, y, eps = 1) {
  const n1 = noise(x, y + eps), n2 = noise(x, y - eps);
  const n3 = noise(x + eps, y), n4 = noise(x - eps, y);
  return createVector((n1 - n2) / (2 * eps), -(n3 - n4) / (2 * eps)).normalize();
}
```

## Flow Fields

A flow field is an angle per grid cell; agents read the nearest cell and steer.
Precompute it — sampling `noise()` per agent per frame is the usual reason a sketch
drops below 60 fps.

```javascript
let field = [], cols, rows;
const RES = 20;

function buildField() {
  cols = ceil(width / RES); rows = ceil(height / RES);
  field = new Array(cols * rows);
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      const angle = noise(x * 0.08, y * 0.08) * TWO_PI * 2;
      field[y * cols + x] = p5.Vector.fromAngle(angle);
    }
  }
}

function fieldAt(px, py) {
  const x = constrain(floor(px / RES), 0, cols - 1);
  const y = constrain(floor(py / RES), 0, rows - 1);
  return field[y * cols + x];
}
```

Draw trails by *not* clearing the canvas: a translucent `background()` each frame
fades old paths and reads as motion blur.

```javascript
background(14, 15, 19, 12);   // low alpha = long trails
```

## Particle Systems

### Physics base

```javascript
class Particle {
  constructor(x, y) {
    this.pos = createVector(x, y);
    this.vel = createVector();
    this.acc = createVector();
    this.maxSpeed = 3;
    this.life = 1;
  }
  applyForce(force) { this.acc.add(force); }   // ignoring mass; add /mass if needed
  update() {
    this.vel.add(this.acc).limit(this.maxSpeed);
    this.pos.add(this.vel);
    this.acc.mult(0);                          // forces are per-frame, not cumulative
    this.life -= 0.005;
  }
  get dead() { return this.life <= 0; }
}
```

Zeroing `acc` every frame is not optional. Forget it and every force integrates
forever, so particles accelerate off-canvas within a second.

### Flocking

Three steering rules over neighbours: separation, alignment, cohesion. Naive
implementation is O(n²) — fine to ~300 agents, then bin agents into a spatial grid.

```javascript
function flock(agent, others, radius = 50) {
  const sep = createVector(), ali = createVector(), coh = createVector();
  let count = 0;
  for (const other of others) {
    const d = p5.Vector.dist(agent.pos, other.pos);
    if (other === agent || d > radius) continue;
    sep.add(p5.Vector.sub(agent.pos, other.pos).div(max(d * d, 0.01)));
    ali.add(other.vel);
    coh.add(other.pos);
    count++;
  }
  if (!count) return;
  ali.div(count); coh.div(count).sub(agent.pos);
  agent.applyForce(sep.setMag(1.5));
  agent.applyForce(ali.setMag(1.0));
  agent.applyForce(coh.setMag(0.8));
}
```

### Attractors

```javascript
function attract(particle, centre, strength = 200) {
  const force = p5.Vector.sub(centre, particle.pos);
  const distance = constrain(force.mag(), 8, 120);   // clamp both ends
  force.setMag(strength / (distance * distance));
  particle.applyForce(force);
}
```

Clamping distance matters at both ends: unclamped, a particle at the centre gets
near-infinite force and teleports; far away the force underflows to nothing.

## Pixel Manipulation

`loadPixels()` / `updatePixels()` bracket direct access. The array is flat RGBA,
and it is scaled by `pixelDensity()` — index arithmetic that ignores density reads
the wrong pixels on every retina display.

```javascript
loadPixels();
const d = pixelDensity();
for (let y = 0; y < height; y++) {
  for (let x = 0; x < width; x++) {
    for (let dy = 0; dy < d; dy++) {
      for (let dx = 0; dx < d; dx++) {
        const i = 4 * ((y * d + dy) * width * d + (x * d + dx));
        pixels[i]     = 255 - pixels[i];      // R
        pixels[i + 1] = 255 - pixels[i + 1];  // G
        pixels[i + 2] = 255 - pixels[i + 2];  // B
      }
    }
  }
}
updatePixels();
```

Call `pixelDensity(1)` first when the effect does not need retina resolution: it
cuts the work by 4× on most phones.

### Mosaic / pointillism

```javascript
// Mosaic: average each block, draw one rect
image.loadPixels();
for (let y = 0; y < image.height; y += block) {
  for (let x = 0; x < image.width; x += block) {
    const c = image.get(x + block / 2, y + block / 2);   // sample centre
    fill(c); noStroke(); rect(x, y, block, block);
  }
}

// Pointillism: sample random points, draw soft dots
for (let i = 0; i < 4000; i++) {
  const x = random(image.width), y = random(image.height);
  fill(image.get(x, y)); noStroke();
  circle(x, y, random(3, 9));
}
```

`get()` per pixel is slow (it allocates); for large loops read `pixels` directly.

### Pixel sorting

```javascript
loadPixels();
const brightnessAt = (i) => pixels[i] * 0.299 + pixels[i+1] * 0.587 + pixels[i+2] * 0.114;
for (let y = 0; y < height; y++) {
  const row = [];
  for (let x = 0; x < width; x++) {
    const i = 4 * (y * width + x);
    row.push([pixels[i], pixels[i+1], pixels[i+2], pixels[i+3]]);
  }
  row.sort((a, b) =>
    (a[0]*0.299 + a[1]*0.587 + a[2]*0.114) - (b[0]*0.299 + b[1]*0.587 + b[2]*0.114));
  row.forEach(([r, g, b, a], x) => {
    const i = 4 * (y * width + x);
    pixels[i] = r; pixels[i+1] = g; pixels[i+2] = b; pixels[i+3] = a;
  });
}
updatePixels();
```

Sort only rows whose brightness falls inside a threshold band for the familiar
glitch look; sorting everything just produces gradients.

## Texture Generation

### Stippling

Density conveys tone. Rejection-sample against a target darkness so dark regions
receive more dots.

```javascript
for (let i = 0; i < 20000; i++) {
  const x = random(width), y = random(height);
  const darkness = 1 - brightness(source.get(x, y)) / 100;
  if (random() < darkness) { point(x, y); }
}
```

### Hatching

```javascript
function hatch(x, y, w, h, angle, spacing) {
  push(); translate(x, y); rotate(angle);
  const diagonal = dist(0, 0, w, h);
  for (let offset = -diagonal; offset < diagonal; offset += spacing) {
    line(offset, -diagonal, offset, diagonal);
  }
  pop();
}
```

Cross-hatch by calling it twice at different angles; spacing carries the tone.

### Halftone

```javascript
for (let y = 0; y < height; y += cell) {
  for (let x = 0; x < width; x += cell) {
    const tone = 1 - brightness(source.get(x, y)) / 100;
    circle(x + cell / 2, y + cell / 2, tone * cell * 1.4);
  }
}
```

Rotate the sampling grid ~15–45° for the print look; an axis-aligned grid reads as
a screen artifact.

### Feedback loops

Draw the previous frame back into the canvas, slightly transformed. Powerful and
easy to blow out — the transform must be a contraction (scale < 1) or the image
saturates within a few dozen frames.

```javascript
let buffer;
function setup() { createCanvas(800, 800); buffer = createGraphics(800, 800); }
function draw() {
  buffer.push();
  buffer.translate(width / 2, height / 2);
  buffer.rotate(0.004);
  buffer.scale(0.998);              // < 1: contraction
  buffer.image(buffer, -width / 2, -height / 2);
  buffer.pop();
  // ...draw new content into buffer...
  image(buffer, 0, 0);
}
```

### Reaction–diffusion (Gray–Scott)

Two chemicals, one feeding on the other. Expensive: run it on a downscaled grid
(200×200 is plenty) and upscale for display.

```javascript
const F = 0.055, K = 0.062, DA = 1.0, DB = 0.5;
// laplacian with the standard 3x3 kernel: centre -1, edges 0.2, corners 0.05
// a' = a + (DA*lapA - a*b*b + F*(1-a))
// b' = b + (DB*lapB + a*b*b - (K+F)*b)
```

Small changes to `F`/`K` switch the regime entirely (spots, stripes, worms), so
expose them as parameters rather than tuning constants in code.

## Performance

| Technique | Budget guide |
|---|---|
| `noise()` calls | ~200k/frame at 60 fps on desktop; ~30k on a phone |
| Particles (no neighbours) | 5–10k |
| Flocking, O(n²) | ~300 without a spatial grid |
| Per-pixel loop at 1080² | ~1.1M iterations — use `pixelDensity(1)` |
| Reaction–diffusion | keep the grid ≤ 250² and step it 1–2× per frame |

Profile before optimising: `console.log(frameRate())` once a second, not every
frame (see `references/troubleshooting.md` § Performance).
