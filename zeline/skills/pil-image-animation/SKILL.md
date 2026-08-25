---
name: pil-image-animation
description: "Generate raster logos, wordmarks, badges, and looping animations (PNG/APNG/GIF) with Python Pillow — 3D extrude, gradient fills, glow/bloom, lightning, shine sweeps, transparency, and a vision-verify iteration loop."
version: 1.0.0
author: Zeline Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  zeline:
    tags: [Pillow, PIL, Image-Generation, Animation, APNG, Logo, Badge, Vision-Verify]
    related_skills: [github-pr-workflow, ascii-art]
---

# Programmatic Image & Animation Generation (Pillow)

Build logos, wordmarks, decorated badges, and looping animations as raster assets
(PNG / animated APNG / GIF) with Python Pillow. Covers the full loop: render at
supersample → composite effect layers → verify with `vision_analyze` → optimize
size → ship. This is how the Zeline/Zerolinear README logo + animated badges were built.

## When to use
- "Make a logo / wordmark that looks like <image>" — recreate a text mark in a chosen font + palette.
- Add a light animation (shine sweep, glow breathing, electric/lightning) with transparent background.
- Animate shields.io-style badges with a synchronized effect.
- Any generated image the user will judge visually and iterate on several times.

## Core render recipe (crisp edges)
1. **Supersample**: render everything at `SS` = 3–4× target size, downscale with `Image.LANCZOS` at the end. This is the single biggest quality win — never draw text/shapes at final size.
2. **Fit font to width**: loop shrinking font size until `font.getbbox(TEXT)` fits ~90% width / ~65% height of the canvas. Russo One (Google Fonts) is a solid heavy blocky/gaming wordmark face; download the TTF into a work dir and load with `ImageFont.truetype`.
3. **Layer with alpha**: build each effect on its own transparent `RGBA` layer, combine with `Image.alpha_composite`. Clip effects to the glyph shape by `ImageChops.multiply(effect_alpha, text_mask)`.
4. **Vertical gradient fill**: build a 1-px-wide column gradient then `.resize((w,h))`; paste through the text mask.

See `references/pil-effects-cookbook.md` for copy-paste code: gradient, 3D extrude, glow, lightning (recursive midpoint displacement + multi-layer bloom), and diagonal shine sweep.

## Animation patterns
- **Loop cleanly**: `N` frames, constant `duration` ms; drive motion off `p = i/N`.
- **Shine sweep**: a thin diagonal white band travels at *constant velocity* across `0..WAVE` fraction of the loop, then a quiet gap before repeat. Fixed band width + constant peak alpha or it looks stuttery. Clip to glyph/badge mask.
- **Glow breathing**: modulate a blurred glow layer's alpha with an independent gentle sine `0.42 + 0.24*(0.5+0.5*sin(2πp))` so it doesn't sync with the sweep.
- **Connected sweep across SEPARATE images** (e.g. a row of badges each a standalone clickable link): drive every element from ONE shared global clock and offset each element's glint by its x-position in the row (`local = global_glint_x - element_x`; only draw when the band overlaps that element). Result: the highlight hands off element-to-element as one continuous wave while each stays an independent file.

## Transparency — always verify, never assume
- Save APNG: `frames[0].save(path, save_all=True, append_images=frames[1:], duration=D, loop=0, disposal=1, blend=0)`.
- **Verify alpha is real**: check `img.split()[3].getextrema()` == `(0,255)` and that the four corner pixels have alpha 0. Then composite over a checkerboard AND over solid white AND solid dark and eyeball each — a transparent-looking file can still be matted onto a dark box.
- GIF only supports 1-bit alpha → semi-transparent anti-aliased corners become opaque → dark fringe on light backgrounds. For clean rounded corners on any theme, use **APNG (soft alpha preserved)**, not GIF.

## Size optimization
- APNG can balloon (8–9 MB at 40–60 frames full-res). Shrink by: fewer frames (40 vs 60), and per-frame color quantization that KEEPS alpha: quantize RGB only (`fr.convert("RGB").quantize(colors=64-96, method=Image.FASTOCTREE)`), then re-attach the original alpha channel via `Image.merge("RGBA",(r,g,b,a))`. 96 colors keeps blue gradients + glow smooth; drops a ~9 MB file to ~4–5 MB.

## The vision-verify loop (critical for visual work)
The user judges by eye and will iterate many times. Bake this in:
1. Render the asset (or a montage of stacked frames, or a diff-from-base to isolate the moving element).
2. Call `vision_analyze` with a SPECIFIC question ("is the core blown-out white?", "does the glint move one position per frame?", "are corners clean on white?").
3. Act on the critique, re-render, re-verify. Repeat until it passes — don't ship on first render.
- For animations, prove motion with a **stacked montage** of evenly-spaced frames, or a `ImageChops.difference(frame, base)` amplified image to isolate where the effect is each frame. A programmatic check (peak-brightness column per frame should climb monotonically) is stronger evidence than eyeballing.

## Pitfalls learned
- **Don't rasterize shields.io badges from their SVG** with cairosvg/rsvg — the badge font isn't embedded, so text renders garbled. Fetch the badge as **PNG** (`img.shields.io/badge/...` returns PNG; text is crisp) and animate that.
- Tiny assets (20px-tall badges) go soft when re-rastered as animation frames. It's a real trade-off vs. static shields sharpness — flag it to the user and offer to revert to static.
- 1-bit alpha (GIF or hard-thresholded PNG) = dark halo on light backgrounds. Keep soft alpha (APNG).
- White-to-color gradient washes out on white backgrounds; for a mark that must read on white, keep the top of the gradient saturated (light *blue*), not pure white, or add an outline.
- Lightning that's just thin lines looks fake. Real look = bright blown-out white CORE + soft colored bloom in layers (wide halo blur + outer glow + mid + sharp core), branching with tapering sub-branches, and reactive rim-light on nearby surfaces.
- `random` inside a helper: pass a seeded `random.Random(seed)` instance as an arg; don't reassign the global `random` (SyntaxError: used-prior-to-global-declaration, and it corrupts other draws).
- APNG optimizers collapse identical trailing (quiet) frames into one long-duration frame; when re-reading for verification, expand by `round(frame.duration/base_duration)` to restore the uniform timeline.

## Shipping
Generated assets for a repo README go through the standard PR loop (see `github-pr-workflow`): branch → copy asset in → point README `<img>` at it → push → wait CI green → squash merge → verify the asset is live. When one asset (e.g. `assets/logo.png`) is referenced by multiple READMEs, replacing that one file updates all of them.
