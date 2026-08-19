"""Animate a static reference artwork into a looping README banner GIF.

Preserves the source pixels and layers subtle, phase-driven motion on top
(breathing zoom + halo/eye glow + diagonal blue shimmer). Edit the four vars
under CONFIG, then run. See references/animate-static-artwork-banner.md.

No external deps beyond Pillow.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageEnhance
import math

# ---- CONFIG ---------------------------------------------------------------
SRC = Path('REPLACE_ME_source_artwork.jpg')     # the user's reference image
OUT = Path('assets/banner.gif')                  # relative to repo root
TW = 760           # target width; biggest size lever (760-960)
FRAMES = 24        # 24-36; fewer = smaller file
COLORS = 64        # 64 ok for silhouette+flat text; bump to 128 if banding
HALO_REL = (0.5, 0.34)  # (x,y) fraction of the head/glow center in the art
# ---------------------------------------------------------------------------

base = Image.open(SRC).convert('RGB')
TH = int(base.height * TW / base.width)
base = base.resize((TW, TH), Image.LANCZOS)
W, H = base.size
halo_cx, halo_cy = int(W * HALO_REL[0]), int(H * HALO_REL[1])

frames = []
for i in range(FRAMES):
    phase = 2 * math.pi * i / FRAMES

    # 1) breathing zoom (<1.5%), center-cropped back to canvas
    scale = 1.0 + 0.014 * (1 - math.cos(phase)) / 2
    zw, zh = int(W * scale), int(H * scale)
    zoomed = base.resize((zw, zh), Image.LANCZOS)
    left, top = (zw - W) // 2, (zh - H) // 2
    frame = zoomed.crop((left, top, left + W, top + H))

    # 2) halo / eye glow pulse (warm)
    glow = Image.new('L', (W, H), 0)
    ImageDraw.Draw(glow).ellipse(
        (halo_cx - int(H*0.30), halo_cy - int(H*0.30),
         halo_cx + int(H*0.30), halo_cy + int(H*0.30)),
        fill=int(38 + 30 * (0.5 + 0.5 * math.sin(phase))))
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    warm = Image.new('RGB', (W, H), (255, 214, 130))
    frame = ImageChops.screen(frame, Image.composite(warm, Image.new('RGB', (W, H), 'black'), glow))

    # 3) diagonal blue shimmer sweep
    band = Image.new('L', (W, H), 0)
    bd = ImageDraw.Draw(band)
    bx = int(-W * 0.4 + (W * 1.8) * i / FRAMES)
    bw = int(W * 0.16)
    for off in range(-bw, bw):
        a = int(70 * (1 - abs(off) / bw))
        if a > 0:
            x = bx + off
            bd.line((x + int(H * 0.5), 0, x - int(H * 0.5), H), fill=a, width=2)
    band = band.filter(ImageFilter.GaussianBlur(6))
    blue = Image.new('RGB', (W, H), (60, 140, 255))
    frame = ImageChops.screen(frame, Image.composite(blue, Image.new('RGB', (W, H), 'black'), band))

    frame = ImageEnhance.Brightness(frame).enhance(1.0 + 0.02 * (0.5 + 0.5 * math.sin(phase)))
    frames.append(frame.convert('P', palette=Image.Palette.ADAPTIVE, colors=COLORS))

OUT.parent.mkdir(parents=True, exist_ok=True)
frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=110, loop=0, optimize=True, disposal=2)

# verify
chk = Image.open(OUT)
print('size', chk.size, 'frames', chk.n_frames, 'animated', chk.is_animated,
      'MB', round(OUT.stat().st_size / 1048576, 2))
