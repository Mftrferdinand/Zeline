# Building a wordmark logo from scratch (Pillow)

When the header art is a **text wordmark** (not organic illustration/mascot), the
user may explicitly ask you to *recreate it from scratch* in a new color/style —
and that is achievable and welcome. This is the exception to the "animate the
original, don't redraw it" rule in SKILL.md: that rule protects complex/organic
artwork whose character is lost on redraw. A blocky text wordmark can be rebuilt
cleanly with Pillow and the user often wants exactly that (new palette, 3D, motion).

Proven on the Zerolinear `ZELINE AGENTIC AI` logo (1536×260, blue #1D4ED8 + white,
3D extrude, electric-spark animation, transparent APNG).

## Workflow (vision-check-driven loop)

1. **Inspect the original** with `vision_analyze` — get exact wordmark text, font
   style (weight, blocky/gaming vs clean sans), colors, layout, effects. Also
   `PIL.Image.open().size/.mode` for exact dimensions to match.
2. **Source a font.** Termux/fresh envs usually have NO system TTFs (`fc-list`
   empty, no `~/.fonts`). Download Google Fonts TTFs via raw GitHub:
   `https://github.com/google/fonts/raw/main/ofl/<family>/<File>.ttf`.
   For a heavy blocky/arcade/gaming wordmark → **Russo One** (`russoone/RussoOne-Regular.ttf`)
   is the best chunky match. Others tried: Orbitron (too thin/sci-fi), Audiowide
   (rounded retro), Chakra Petch (industrial, lighter). Render a **contact sheet**
   of candidates and vision-check "which is boldest/blockiest" before committing.
3. **Render → vision_analyze → fix specific critiques → re-render.** Do NOT ship
   on first render. Each vision pass returns concrete issues (jagged edges, flat
   extrude, glow too gray, uneven animation spacing) — fix exactly those and loop.
   This iteration loop is what gets from "4/10 clip-art" to shippable.
4. Present **2–3 variants** (e.g. outline vs no-outline, gradient direction) on
   BOTH white and dark backgrounds and let the user pick. Gradient direction
   matters: white→blue (light top, dark bottom) stays legible on white; blue→white
   nearly vanishes at the bottom on white. A dark-blue outline makes a gradient
   wordmark legible on ANY background.

## Core Pillow recipe

- **Crisp edges = supersample.** Render at `SS=3` (BW=W*3, BH=H*3), do all drawing
  there, then `.resize((W,H), Image.LANCZOS)` at the end. Direct-size text is soft.
- **Fit font to width** with a shrink loop: start big, `font.getbbox(TEXT)`, shrink
  by 3px until `tw <= BW*0.90 and th <= BH*0.64` (leave room for extrude+glow).
- **Vertical gradient fill:** build a 1×H `RGB` image, set each row by lerping
  top→bottom color, `.resize((w,h))`, then `canvas.paste(grad, (0,0), text_mask)`
  where text_mask is an `L` image of the glyphs.
- **3D extrude:** paste dark-navy copies of the text mask offset diagonally
  `for d in range(DEPTH,0,-1)` at `(X+d, Y+d)`; lerp the extrude color dark→lighter
  across depth so the side reads as lit, not flat-pasted. `DEPTH ≈ 7*SS`.
- **Glow:** paste a solid glow-color through a dilated mask (`stroke_width=2*SS`),
  `GaussianBlur(9*SS)`, scale its alpha down (~0.4–0.6) so it's a halo not a fog.
- Composite order: glow → extrude → gradient face → (shine/lightning) → (rim light).

## Electric / lightning spark animation

- **Jagged bolt = recursive midpoint displacement.** `jagged(rnd,p0,p1,disp,depth)`:
  midpoint pushed along the perpendicular by `rnd.uniform(-disp,disp)`, recurse
  left+right with `disp/2`, `depth≈5-6`. Add occasional forks off a random midpoint.
- **Convincing electric look needs 3 stacked passes**, not a thin line (a single
  thin white stroke reads as a "scratch", vision-rated 4/10):
  - OUTER bloom: thick line in bloom-blue, `GaussianBlur(8*SS)`, alpha ~0.8
  - MID glow: medium line in cyan `#93C5FD`, `GaussianBlur(3*SS)`
  - CORE: thin line in near-white `#F0F9FF`, sharp
- **Reactive rim-light:** clip the outer-bloom alpha to the face mask
  (`ImageChops.multiply(bloom_alpha, face_mask)`) and composite a tinted copy
  UNDER the bolts so letters near a strike visibly brighten. Missing this makes
  bolts and text look like two unrelated layers.
- **Random flicker:** seed a per-frame RNG (`random.Random(i)`); ~75% of frames
  draw 1–3 bolts at random anchor points on the text bbox edges, ~25% draw none.
  Positions differ every frame → looks alive, not a rigid loop.
- **Smooth non-lightning motion** (shine sweep / glow breathe): move a fixed-width
  band at CONSTANT speed with EVEN per-frame spacing (linear across 0..70% of the
  loop, rest off-screen 30%), and drive glow breathing on an INDEPENDENT sine so it
  doesn't flicker in sync with the sweep. Vision flagged uneven spacing + a fading
  band as the glitch to avoid.

## Output format & transparency

- **Animation with transparency → APNG** (`.png`, `save_all=True, disposal=2`).
  GIF cannot do soft alpha — bake it onto a dark bg (`#0D1117`, GitHub dark) as a
  smaller preview/fallback only. GitHub README renders APNG animation.
- **Verify transparency for real**, don't trust the eye: alpha `getextrema()`
  should include 0, corner pixels `(0,0,0,0)`, and composite over a checkerboard
  then vision-check that the squares show through. A white-looking bg in a viewer
  can be either transparency or a solid white fill.
- Keep APNG under GitHub's 10 MB cap: fewer frames / smaller width / fewer palette
  colors. ~60 frames at 1536×260 landed ~9 MB (APNG) — trim frames if over.
- **Biggest single size win = RGBA-preserving palette quantize per frame.** Quantize
  only the RGB, keep the original alpha, remerge — halves the file with no visible
  banding on a 2-tone gradient + glow:
  ```python
  def quant(fr):  # fr = RGBA frame
      rgb = fr.convert("RGB").quantize(colors=96, method=Image.FASTOCTREE).convert("RGBA")
      r, g, b, _ = rgb.split(); a = fr.split()[3]
      return Image.merge("RGBA", (r, g, b, a))
  q = [quant(f) for f in frames]
  q[0].save(out, save_all=True, append_images=q[1:], duration=65, loop=0, disposal=2)
  ```
  Measured live: 8.9 MB → 4.6 MB at 96 colors, gradient/glow still smooth (vision-checked).
  Dropping frame count 60→40→48 also helps linearly. Note thicker/more-frequent
  effects re-inflate size (punchier lightning pushed 5.6 MB → 7.2 MB).

## Choosing the RIGHT motion (design taste, not just mechanics)

Literal **lightning/electric sparks read as flashy but "clip-art" for a corporate
tech/AI wordmark** — vision consistently rated bare bolts 4–6.5/10 even after the
3-pass bloom + reactive-rim polish, and the user ultimately asked to *remove the
lightning entirely* and "buat animasi lain yang lebih cocok". The replacement that
landed clean and professional (and lighter, ~4.2 MB): a **shine-sweep + glow-breathe**
combo — a fixed-width white glint sweeping the face at constant velocity plus an
independent gentle glow sine. When a user wants a "bagus"/premium tech logo, LEAD
with (or at least offer) the subtle shine-sweep option; treat literal lightning as
the high-energy/gamer variant, not the default. Either way, present it and let them
choose — this asset went through ~8 iteration PRs of palette/effect tweaks.

## Performance note

60 supersampled frames with multi-pass blur on Termux ran ~40–80s. Fine, but
don't render at SS higher than 3 for animation or it balloons. numpy is often
absent in the execute_code sandbox — use `PIL.ImageStat` for brightness metrics.
