# Recolor a raster logo/wordmark & make it non-downloadable

Use when the user hands a PNG/JPG logo that's a faint or dark mark on a solid background and wants it (a) on a transparent background and (b) recolored to fit either a dark or light UI, without redrawing it.

## Derive alpha from luminance (PIL)

The logo is dark/gray strokes on a solid (usually white/black) field. Convert luminance → alpha so the field disappears and the strokes stay, then paint the strokes a flat color. Produce two variants: white (for dark/blue bg) and ink (for light/paper bg).

```python
from PIL import Image
src = Image.open("logo-src.png").convert("RGBA")
w,h = src.size; px = src.load()

def make(color):                     # color = (r,g,b)
    out = Image.new("RGBA",(w,h)); op = out.load()
    for y in range(h):
        for x in range(w):
            r,g,b,a = px[x,y]
            lum = 0.2126*r + 0.7152*g + 0.0722*b
            # strokes darker than field on a WHITE field → alpha = 255-lum
            alpha = int(max(0, 255-lum))
            # boost faint strokes so they read
            alpha = min(255, int(alpha*3.2))
            op[x,y] = (color[0], color[1], color[2], alpha)
    return out

make((255,255,255)).save("wordmark-white.png")   # for dark bg
make((23,22,26)).save("wordmark-ink.png")         # for light/paper bg
```

Notes:
- If the source ALREADY has an alpha channel (strokes have alpha, field is transparent), skip luminance math — just recolor RGB and keep the existing `a`.
- Verify: `Image.open(out).getpixel((3,3))` should have alpha≈0 at a corner; a stroke pixel alpha≈255.
- A quick alternative for a white-on-anything mark in pure CSS: `filter:brightness(0) invert(1)` (white) — used it for the footer wordmark. But that only makes it pure white/black; the PIL route gives arbitrary color + clean alpha.

## Non-downloadable markup

```html
<span class="brand"><img src="wordmark-white.png" alt="Zerolinear"
     draggable="false" oncontextmenu="return false"></span>
```
```css
.brand img{pointer-events:none;-webkit-user-drag:none;-webkit-touch-callout:none;user-select:none}
```
Optional extra guard for a hero image: an absolutely-positioned transparent `.shield` layer over it so long-press "save image" hits the shield, not the img. This is deterrence, not real DRM — say so if asked.

## Sizing to match a sibling control

To make a wordmark line up with, say, a menu button of height 40px: set `.brand{height:40px}` and `.brand img{height:40px;width:auto;object-fit:contain}`. For a wordmark that grows as a shade is pulled, drive width off a CSS var: `width:calc(200px + var(--shadeP,0) * (96vw - 200px))`.
