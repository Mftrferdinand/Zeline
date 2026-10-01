# Blue "research-lab" landing: two-layer bg, image hero, transparent-logo, and the nav over-engineering trap

Session-derived patterns from building an AI-research-lab landing (protagolabs-style
palette + Zeline-style deep-blue) that recur for this user. Dependency-free, mobile-first.

## Two-layer background: deep-blue zone + inset cream "paper" canvas
The user's preferred structure for a lab/product landing:
- **Layer 1 — `.blue-zone`**: deep blue gradient (`radial-gradient(...rgba(120,150,255,.35)...) , linear-gradient(180deg,#1a2ec9,#0f1c8f)`). Used for the top (nav + hero) and the footer.
- **Layer 2 — `.paper`**: a cream canvas (`#f4f1ea`) that starts at the first content section and is **inset from the sides** (`margin:0 16px`) so the blue shows around its edges — reads as a page sitting on top of a blue backdrop. Add `box-shadow:0 -30px 80px -20px rgba(0,0,0,.45)` at its top edge for lift, and a subtle newspaper grain (`radial-gradient(rgba(23,22,26,.025) 1px,transparent 1px); background-size:4px 4px`).
- **Corners**: this user wants **square** (`border-radius:0`) on the paper, cards, buttons, code blocks, pills — not rounded. When they say "ujungnya kotak saja," sweep every component's radius to 0 in one pass.
- Paper entrance: `opacity:0; transform:translateY(40px)` → `.in` via IntersectionObserver for a smooth reveal as it scrolls in.

## Image-as-hero-background fading into the paper
When the user drops a hero image ("jadikan background di paling atas, bawahnya gradasi ke putih, teks di bawahnya"):
- `.hero-art { height:clamp(340px,62vh,600px); background:url('assets/hero.png') center top/cover }`.
- Fade the bottom of the image into the page bg with a pseudo-element: `.hero-art::after { position:absolute; left:0;right:0;bottom:0; height:55%; background:linear-gradient(to bottom,transparent,var(--paper) 92%) }`.
- Put the hero **copy in a separate `.hero-copy` block on the paper bg** directly under the image (not overlaid on it) — text stays legible on solid cream, image is pure atmosphere. Flip text colors to ink/accent since it's now on light bg.
- Copy the image into `assets/` with a stable name; never reference the transient `~/.zeline/cache/images/img_*` path.

## Transparent PNG from a black-background logo (PIL, reusable)
User frequently supplies a white-on-black (or faint-gray-on-white) logo/wordmark PNG and wants it transparent + recolored to sit on a colored bg, plus non-downloadable. Convert luminance → alpha:
```python
from PIL import Image
img = Image.open(src).convert("RGBA"); w,h=img.size; px=img.load()
out = Image.new("RGBA",(w,h)); op=out.load()
for y in range(h):
    for x in range(w):
        r,g,b,a = px[x,y]
        lum = 0.2126*r+0.7152*g+0.0722*b
        alpha = min(255, int(max(0,255-lum)*3.2))   # black bg->0, strokes->opaque; *k boosts faint strokes
        op[x,y] = (255,255,255,alpha)                # recolor strokes to pure white for a blue bg
out.save(dst)
```
- If the source ALREADY has an alpha channel (faint gray strokes with alpha), just recolor keeping `a`: `op[x,y]=(255,255,255,a)` for a white version, `(23,22,26,a)` for an ink version. Emit both white + ink variants so the same wordmark works on blue and on paper.
- Verify: corner pixel alpha≈0 (transparent), stroke pixel alpha≈255 (solid).
- **Non-downloadable**: `<img draggable="false" oncontextmenu="return false">` + CSS `pointer-events:none; -webkit-user-select:none; -webkit-touch-callout:none`. To make the wordmark strictly decorative (not a link), use a `<span class="brand"><img></span>`, not an `<a>`.

## The NAV over-engineering trap (hard-won — mirrors the elaborate-hero reversal)
The user asked for progressively fancier header nav, then reverted the whole thing. The arc, all in one session:
plain menu button → centered wordmark + "−" pull handle → chevron "⌄" that opens a drawer → **notification-shade** you drag down (wordmark grows with pull, gradient darkens, velocity-driven motion blur, rAF-throttled drag) → "kembalikan seperti semula pake tombol menu aja" → "hilangkan tombol menu" (just a centered wordmark).

Lesson: **do not sink turns polishing novel nav interactions.** Swipe-shades, drag-to-open with motion blur, chevron-float hints, wordmark-scales-with-drag — all got built and then thrown away. Lead with the simplest nav that works and treat anything fancier as opt-in you're ready to delete:
- **Simplest that survives:** sticky header, centered CSS/`<img>` wordmark, optional single hamburger button toggling a `translateY(-120%)→0` slide-down drawer. That's the stable end-state this user returns to.
- When they DO ask for a swipe/notification-shade nav, the working recipe (before they revert it): a `.shade` layer translated up by its own height, dragged via a small grip; drag handler throttled through `requestAnimationFrame` (raw touchmove stutters); a CSS var `--shadeP` (0→1) drives both a darkening `.shade-bg` opacity and the wordmark size; motion blur = velocity (`Δy/Δt`) mapped to a small `filter:blur()` capped ~2.5px on the *panel only* (blurring the wordmark annoys); snap open/closed on release at 40% threshold. Keep blur subtle — "perbaiki motion blur agar tidak mengganggu" came fast.
- **Auto-hide header on scroll**: hide on scroll-down past the hero, show on scroll-up, always show near top. A one-shot "hide permanently past About" reads as "the banner disappeared 🤣" — use directional show/hide, not a permanent trigger.

## Fake terminal / install UI belongs in DOCS, not the lab landing
Built a typing-terminal, then a Linear-style diff code-editor (zoomed, breaking out of frame, blue vignette), iterated many times — then the user cut it entirely: "hilangkan terminal karena kurang cocok di labs nanti aja di zeline docs." For a *research-lab* landing, keep install/terminal/`curl … | bash` out; point to the docs instead. Terminal chrome (traffic-light dots, typing animation, fixed height so it doesn't grow/jump, tiny "Copy" link instead of a big button) is docs/product-page material. Same over-engineering reversal as nav and hero.

## Mobile Android sizing is a repeated correction — ship small from the start
"font masih kegedean di HP, perkecil lagi" recurs every few turns. Add a `@media(max-width:640px)` block up front that shrinks display headings to ~24-30px (not 40px+), body to ~14px, section padding to ~46-52px, and makes buttons full-width. A second `@media(max-width:380px)` step-down. Measure/verify on-device rather than guessing twice.
