# Pillow Effects Cookbook

Copy-paste building blocks. All assume supersample `SS`, canvas `BW,BH = W*SS, H*SS`,
and a text mask. Downscale to `(W,H)` with `Image.LANCZOS` as the final step.

## Setup: font fit + text mask
```python
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
SS=3; W,H=1536,260; BW,BH=W*SS,H*SS
def fit_font(FONT, TEXT):
    size=200*SS
    for _ in range(400):
        f=ImageFont.truetype(FONT,size); bb=f.getbbox(TEXT)
        if bb[2]-bb[0]<=BW*0.90 and bb[3]-bb[1]<=BH*0.64: return f,bb
        size-=3
    return f,bb
def tmask(f,TEXT,x,y,sw=0):
    m=Image.new("L",(BW,BH),0)
    ImageDraw.Draw(m).text((x,y),TEXT,font=f,fill=255,stroke_width=sw,stroke_fill=255)
    return m
```
Download a wordmark font (Russo One = heavy blocky/gaming):
`curl -sL -o RussoOne.ttf https://github.com/google/fonts/raw/main/ofl/russoone/RussoOne-Regular.ttf`

## Vertical gradient fill (top color -> bottom color)
```python
def vgrad(w,h,top,bottom):
    g=Image.new("RGB",(1,h))
    for yy in range(h):
        t=yy/(h-1)
        g.putpixel((0,yy),tuple(int(top[i]+(bottom[i]-top[i])*t) for i in range(3)))
    return g.resize((w,h)).convert("RGBA")
face=Image.new("RGBA",(BW,BH),(0,0,0,0))
face.paste(vgrad(BW,BH,BLUE_TOP,BLUE),(0,0),face_mask)   # paste THROUGH the mask
```

## 3D extrude (dark, with a slight gradient across depth so sides look lit)
```python
DEPTH=7*SS
extrude=Image.new("RGBA",(BW,BH),(0,0,0,0))
for d in range(DEPTH,0,-1):
    t=d/DEPTH
    col=tuple(int(DARK[i]+(NEAR[i]-DARK[i])*(1-t)) for i in range(3))
    m=tmask(f,TEXT,X+d,Y+d)
    step=Image.new("RGBA",(BW,BH),(0,0,0,0))
    step.paste(Image.new("RGBA",(BW,BH),col+(255,)),(0,0),m)
    extrude=Image.alpha_composite(extrude,step)
# nudge face up by DEPTH//2 (Y=... -DEPTH//2) so the extrude fits in-canvas
```

## Soft glow around glyphs
```python
def base_glow(glow_mask, color, intensity):
    g=Image.new("RGBA",(BW,BH),(0,0,0,0))
    g.paste(Image.new("RGBA",(BW,BH),color+(255,)),(0,0),glow_mask)  # glow_mask = tmask(sw=2*SS)
    g=g.filter(ImageFilter.GaussianBlur(9*SS))
    r,gg,b,a=g.split(); a=a.point(lambda v:int(v*intensity))
    return Image.merge("RGBA",(r,gg,b,a))
```

## Realistic lightning (blown-out core + layered bloom + branching)
```python
def jagged(rnd,p0,p1,disp,depth):        # recursive midpoint displacement
    if depth==0: return [p0,p1]
    mx=(p0[0]+p1[0])/2; my=(p0[1]+p1[1])/2
    dx=p1[0]-p0[0]; dy=p1[1]-p0[1]; ln=math.hypot(dx,dy) or 1
    nx=-dy/ln; ny=dx/ln; off=rnd.uniform(-disp,disp)
    mx+=nx*off; my+=ny*off
    return jagged(rnd,p0,(mx,my),disp*0.55,depth-1)[:-1]+jagged(rnd,(mx,my),p1,disp*0.55,depth-1)
# build (points, thickness_scale) list: 3-5 main arcs anchored on the glyph bbox,
# each with 2-4 tapering branches (scale 0.7) and optional sub-branches (0.45).
# Draw FOUR layers per path, widest+most-blurred first:
#   halo  : ARC_OUT, width ~18*SS*sc, GaussianBlur(18*SS), alpha*0.7
#   outer : ARC_OUT, width ~9*SS*sc,  GaussianBlur(8*SS)
#   mid   : ARC_MID, width ~5*SS*sc,  GaussianBlur(2.2*SS)
#   core  : WHITE,   width ~2.6*SS*sc, tiny blur; composite core over a slightly-blurred
#           copy of itself => pure-white blown-out center
# reactive rim-light: multiply the halo alpha by the face_mask, tint white ~0.9, composite
#   UNDER the bolts so nearby glyph edges catch the flash.
```
Colors that read as electric: `ARC_OUT=(170,210,255)`, `ARC_MID=(240,248,255)`, core `(255,255,255)`.

## Diagonal shine sweep (thin, constant velocity)
```python
def shine(cx_frac, face_mask, PEAK=62, BAND_HW=8):
    band=Image.new("L",(BW,BH),0); d=ImageDraw.Draw(band)
    cx=int(cx_frac*BW); bw=int(BAND_HW*SS)
    for off in range(-bw,bw+1):
        a=int(PEAK*(1-abs(off)/bw))
        if a<=0: continue
        x0=cx+off
        d.line([(x0+int(BH*0.34),0),(x0-int(BH*0.34),BH)],fill=a,width=SS)  # diagonal
    band=band.filter(ImageFilter.GaussianBlur(int(1.2*SS)))
    band=ImageChops.multiply(band,face_mask)                # clip to glyph/badge shape
    sh=Image.new("RGBA",(BW,BH),(255,255,255,255)); sh.putalpha(band)
    return sh
# per frame p=i/N: if p<WAVE: cx=-0.18+(p/WAVE)*1.36 else None (quiet gap)
```

## Row-synchronized sweep across N independent images
```python
# xpos[name] = cumulative x of each element in the visual row (incl. gaps)
# TOTAL = full row width. For element `name` at frame i:
g = -MARGIN + (p/WAVE)*(TOTAL + 2*MARGIN)   # ONE global glint x for the whole row
local = g - xpos[name]                       # position within THIS element
if -BAND_HW < local < w+BAND_HW:             # only draw when band overlaps element
    ... shine(local / w ...) clipped to element mask ...
# => highlight hands off element-to-element as one connected wave; each stays a separate file.
```

## Save APNG + size-optimized quantize (keep alpha)
```python
def quant_keepalpha(fr, colors=64):
    a=fr.split()[3]
    rgb=fr.convert("RGB").quantize(colors=colors, method=Image.FASTOCTREE).convert("RGBA")
    r,g,b,_=rgb.split(); return Image.merge("RGBA",(r,g,b,a))
q=[quant_keepalpha(f) for f in frames]
q[0].save(out, save_all=True, append_images=q[1:], duration=70, loop=0, disposal=1, blend=0, optimize=False)
```

## Verification snippets
```python
# alpha real?
assert img.split()[3].getextrema()==(0,255)
assert img.getpixel((0,0))[3]==0            # transparent corner
# expand APNG collapsed quiet frames back to uniform timeline
slots=[]
for f in ImageSequence.Iterator(Image.open(path)):
    slots += [f.convert("RGBA")]*max(1,round((f.info.get('duration',D) or D)/D))
# prove sweep moves: peak-brightness column per frame should climb monotonically
d=ImageChops.difference(row(i).convert("RGB"), base).convert("L")
# checkerboard matte to eyeball transparency
```
