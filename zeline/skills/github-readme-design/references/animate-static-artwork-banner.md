# Animate a static reference artwork into a README banner GIF

When a user sends existing artwork ("make the banner look like THIS") the win is
to **animate the original image, not redraw it**. Redrawing from scratch (text +
shapes in Pillow) usually loses the character of the art and the user rejects it.
Instead: preserve the source pixels and layer subtle motion on top.

## Recipe (Pillow, no external deps)

Load the reference, downscale to a GitHub-friendly width, then per frame apply a
few gentle, phase-driven effects and screen-blend them:

- **Breathing zoom**: `scale = 1 + 0.014*(1-cos(phase))/2`, resize then
  center-crop back to canvas. Keep it under ~1.5% — anything bigger looks jittery.
- **Halo / eye glow**: draw a filled ellipse on an `L` mask over the head/torso
  region, `GaussianBlur(~60)`, composite a warm color `(255,214,130)` through it,
  `ImageChops.screen` onto the frame. Pulse strength with `sin(phase)`.
- **Shimmer band**: a diagonal soft gradient line sweeping left→right across the
  frame, blurred, screen-blended in a blue `(60,140,255)`.
- Optional overall brightness breathing `ImageEnhance.Brightness(1.0 + 0.02*...)`.

`phase = 2*pi*i/FRAMES`. Loop the GIF forever (`loop=0`), `disposal=2`,
`optimize=True`.

A working generator lives at `scripts/animate_artwork_banner.py` — copy and edit
the SRC path, halo center, and palette.

## Size budget is the real constraint (GitHub hard cap 10 MB)

An adaptive-palette GIF from a photographic gradient balloons fast. Iterate these
three knobs downward until under ~3–5 MB (comfortable for mobile):
- **width** (TW): 760–960. Biggest lever.
- **FRAMES**: 24–36. Fewer frames = smaller file, slower motion.
- **palette colors**: `convert('P', palette=ADAPTIVE, colors=N)` — 64 is usually
  enough for a silhouette + flat title text; drop from 128→80→64 to shrink.

Example convergence from a real session: 960×617 / 36f / 128c = **9.9 MB** (too
big) → 820×527 / 28f / 80c = 4.6 MB → **760×489 / 24f / 64c = 3.07 MB** (shipped).

Banding risk: a smooth blue→white gradient at 64 colors is the danger zone.
ADAPTIVE + implicit dithering handled it here; if you see hard bands, bump colors
back to 128 rather than fighting it.

## Verify before AND after push

- Reopen the GIF: assert `im.size`, `im.n_frames > 1`, `im.is_animated == True`,
  and file size under budget.
- Extract a mid frame (`im.seek(n); im.convert('RGB').save(png)`) and run
  `vision_analyze` on it — confirm figure + title still sharp, no severe banding,
  motion effects look natural (not "pasted on").
- After push, read the asset back through the API and `curl -sL` the
  `download_url` for `HTTP 200 | image/gif` so you know it's actually public.

## Pitfalls

- `vision_analyze` on the multi-MB GIF itself can time out — analyze an extracted
  PNG frame instead.
- Termux has no `/bin/file`; don't shell out to `file` to check the type, use
  Pillow (`Image.open(...).size / n_frames / is_animated`).
