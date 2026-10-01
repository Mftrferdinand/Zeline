---
name: landing-page-ui-patterns
description: "Reusable patterns for single-file static landing/marketing pages (pure HTML/CSS/JS, mobile-first, Termux python http.server): layered scroll backgrounds, hero image fading into content bg, image-as-logo recoloring to transparent PNG, non-downloadable assets, and drag-to-open notification-shade nav. Includes Aes's iterative-UI correction habits."
---

# Landing Page UI Patterns

Class-level patterns for building one-file static landing pages the way Aes iterates on them (zerolinear-site, twenty3ph, web3addicter-site). Pure HTML/CSS/JS, no framework, served by `python3 -m http.server PORT --bind 127.0.0.1`. Mobile-first, verified with `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:PORT/`.

## Working with Aes on landing pages (behavior)

- Corrections arrive in rapid short bursts ("perkecil", "di tengah", "hapus", "background salah"). Treat each as one atomic edit → verify HTTP 200 + grep the marker → ask user to refresh. Don't batch-guess.
- **He reverses himself often.** He'll ask for a feature (animated logo, Linear-style zoom terminal, swipe shade), then say "gausah", "ilangin", "balikin seperti semula". When told to remove, delete the markup AND its CSS/JS fully (not `display:none`), and grep to confirm no leftover markers.
- Font size on Android is almost always "too big" — expect repeated "perkecil lagi". Use `clamp()` and a dedicated `@media(max-width:640px)` block; shrink aggressively there.
- Over-engineering is disliked. He killed a Three.js hero, a multi-step typing terminal, and a Linear-style diff editor as "ribet". Prefer the simplest thing that reads well. Don't add animations he didn't ask for.
- "presisikan di tengah" / "jangan ketinggian" = center + adequate vertical padding, not mepet-atas.
- When he says "gak ada perubahan", suspect browser cache first (hard-refresh) before assuming the CSS is wrong.

## Server lifecycle on Termux

Background `http.server` dies between sessions / on `/stop`. When user says "run localhost:PORT", first `curl` to check; if down, relaunch with `terminal(background=true)` from the site dir. Verify 200 before reporting.

## Two-layer scroll background

Main page bg = one color; a second full-width "canvas" layer with different bg starts at a mid-page section (e.g. About). Wrap the lower sections in `<div class="canvas2"><div class="canvas2-inner">…`; give `.canvas2` its own bg + a top box-shadow so it reads as a raised panel. Text colors flip via `.canvas2 h2,.canvas2 .lede{…}`. Square corners (`border-radius:0`) is this user's default.

**"Potong-potong" (sliced slabs) vs one solid panel — read the exact ask.** He asked to slice the blue canvas into separate cobalt slabs (one blue block per section with paper gaps between: `.canvas2-inner{display:flex;flex-direction:column;gap:14px}` + `.canvas2-inner>section{background:#1a2eb0;box-shadow:…}`), then immediately reverted with "balikin aja kaya td cm pisahin bagian paling bawah" — meaning: restore the single solid blue panel, and ONLY move the footer out. To separate just the bottom: move the `</div></div><!-- /canvas2 -->` close tag to BEFORE `<footer>` so the footer sits on the main paper bg. When the footer leaves the blue canvas, flip its colors back to paper (`background:var(--paper)`, ink links, `--line` borders) and swap `wordmark-white.png`→`wordmark-ink.png` (drop the `filter:brightness(0) invert(1)`). Lesson: his "kepotong/dibagi-bagi" often overshoots what he wants — be ready to revert to the solid panel in one edit and re-split at a single seam instead.

**Reverting textures:** Aes iterates hard on the layer-2 blue texture and often says "balikin seperti semula". Keep the plain fallback handy — a clean cobalt `linear-gradient(180deg,#1c34d6,#12228f,#0c1568)` + faint 1px radial-dot grain — so you can restore it in one edit. When he wants a MATCHED texture from a reference image, tiru it with CSS (don't embed his photo): stacked `radial-gradient` white blotches for "smoky/kotoran putih" specks, or an inline SVG `feTurbulence` (`type='turbulence'` for marbled veins, `fractalNoise` for grain) blended `screen`/`overlay`. To make white flecks actually visible, push `feFuncA` contrast (slope ↑, negative intercept) and opacity ≥.7 — his complaint "kurang keliatan" almost always means the noise opacity/contrast was too timid.

## Alternating section themes (paper ↔ blue) — anti-monotony

He complained an all-blue lower half was monotonous ("jangan semua section biru → selingi off-white"). Preferred architecture is NOT one big `.canvas2` blue panel wrapping everything — it's **per-section theme classes that alternate**. Give each `<section>` either `theme-paper` or `theme-blue` and alternate them (About paper → Zeline blue → Skills paper → Vision blue → Build paper → Roadmap blue → CTA paper). Recipe:
```
.theme-paper{position:relative;background:var(--paper);color:var(--ink)}
.theme-paper + .theme-paper{background:var(--paper-2)}   /* subtle tone shift if two paper sections adjoin */
.theme-blue{position:relative;color:#eef1ff;background:#16279e;overflow:hidden}
.theme-blue::before{…faint 1px radial-dot grain…}
.theme-blue::after{…scroll-driven radial glow (see below)…}
.theme-blue > .wrap{position:relative;z-index:1}
```
Make type colors theme-aware instead of scoping to one wrapper: `.theme-blue h2/.lede/.eyebrow{…}` vs `.theme-paper .lede/.eyebrow{…}`, and every hairline border (`.bullet`,`.card`,`.step`) gets `.theme-blue …{border-color:rgba(255,255,255,.14)}` vs `.theme-paper …{border-color:var(--line)}`. When migrating away from the old single `.canvas2`, delete the `<div class="canvas2">` wrappers AND the `.divider-top` borders (theme background change is the separator now).

## De-box repeated cards — hairline rows, not bordered boxes

He dislikes "terlalu banyak card dengan desain sama" (too many identical bordered boxes). Convert card/feature/step grids from bordered filled boxes to **borderless rows with a single hairline top rule**: `.card{border:0;background:none;padding:0;border-top:1px solid;padding-top:16px}`, drop hover-lift (`transform:none;box-shadow:none`). Same for the Zeline overview block (`.zeline{background:none;border:0;padding:0}`) and platform chips (`.plat span{border:0;padding:0;background:none}` — just a mono text row). This is his general anti-clutter aesthetic (square corners, minimal borders).

## Full-width 3:2 art band between text blocks

He inserts generated images mid-section ("→ GAMBAR DI SINI, full-width 3:2"). Pattern: a `.art-band` placed BETWEEN the intro paragraph and the following bullets/steps.
```
.art-band{position:relative;width:100%;margin:clamp(26px,4vw,44px) 0;line-height:0}
.art-band img{display:block;width:100%;aspect-ratio:3/2;object-fit:cover;pointer-events:none;-webkit-user-drag:none}
```
Critical corrections he made: (1) **must sit inside `.wrap`**, not full-bleed to the screen edge — "jangan full sampai ke samping, ukuranya samakan sama garis putih dibawahnya" (match the content/divider width). (2) **jarak ke teks jangan terlalu lebar** — keep margins moderate (~26–44px), not the 30–52px he first rejected. Each art band still carries `reveal`/`reveal-late` and `?v=N` cache-bust. He iterates the same image slot many times ("ubah jadi ini" repeatedly) — overwrite the asset filename, bump `?v=N`, verify 200. He also asks to "samakan style semua gambar" — but image style depends on the source images HE generates; make the CONTAINER identical (all `.art-band` 3:2, same fade into section) and tell him to resend off-style images rather than trying to restyle rasters in CSS.

## Text shortening on request

"Pendekin teks 15–25%" = trim lede/card/step copy: drop redundant clauses, cut filler ("an independent AI research lab. We study…" → "We study…"), shorten headings ("designed to run anywhere" → "runs anywhere"). Keep meaning, cut ~20% of words.

## Section spacing for a premium feel

"Rapikan spacing antar-section, kasih ruang lebih lega" → bump `.section-pad` roomier. He associates generous vertical rhythm with "premium/research-lab".

**But don't overshoot — `132px` was too much.** After bumping to `clamp(76px,11vw,132px)` he came back with "spacing paragraf antar judul terlalu lebar, perbaiki agar tidak terlalu lebar". Two lessons: (1) the more common culprit is NOT section padding but the **two-col gap** between the heading column and the paragraph column — that's what reads as "jarak antar judul kelebaran". Shrink it hardest: `.two-col{gap:clamp(20px,3vw,40px)}` (down from `clamp(32px,6vw,72px)`), mobile `gap:14px`. (2) Pull `.section-pad` back to a moderate `clamp(56px,8vw,96px)` — lega but not cavernous. Land on moderate first; only go bigger if he explicitly asks again.

## Hero image fading into the next section

Put the hero image in its own `<img>` (not CSS `background` — that caused text to get buried behind an overlay). Add a `::after` gradient `linear-gradient(to bottom,transparent,<next-bg-color>)` over the bottom ~45% so the photo dissolves into the content area. Copy block below sits on solid content bg with `z-index:2`.

**Desktop hero for a tall/portrait statue image:** Aes's preferred final = keep the ORIGINAL full-width banner style (do NOT split into a 2-column layout — he rejected that), just make it taller so the lower body shows, keep it centered, and add a white vignette. Recipe: `.hero-art img{height:clamp(520px,74vh,760px);object-fit:cover;object-position:center 30%}`; bottom fade `::after{height:55%;background:linear-gradient(to bottom,transparent 0%,rgba(paper,.35) 45%,var(--paper) 100%)}`; soften edges with a radial side vignette `::before{background:radial-gradient(130% 100% at 50% 38%,transparent 55%,rgba(paper,.5) 100%)}`. `object-fit:cover` on a portrait image at full width crops head+feet — raise it with `object-position` and give it more height rather than switching layout.

- **Scope the desktop hero block at `@media(min-width:641px)`, NOT 900px.** He views on a laptop whose viewport lands in the 641–899px gap; at 900px that width fell back to the short default and he complained the statue \"ga keliatan sampe bawah\". 641px = everything above the `max-width:640px` mobile block gets the tall statue.
- **Do NOT \"fix\" the landscape crop by switching to `object-fit:contain`.** When he rotates a phone in desktop-mode, `cover` crops the statue to ~half — the technically-correct fix (`contain` + letterbox paper bg) he rejected instantly as \"malah jelek\". Keep `cover`; the crop on rotated phones is an accepted tradeoff. Revert to the exact `cover`/`74vh`/`center 30%` recipe above if you ever touch it.

## Desktop vs mobile sizing — ALWAYS use two separate media blocks

Aes checks on both a wide screen and Android. When he says "perbesar di desktop", scope it to the desktop block ONLY — do not touch the base rule, or mobile inherits the bigger size and he immediately comes back with "di mobile jadi gede, perkecil". Keep banner wordmark / shade padding / header-spacer / chevron sizes defined in BOTH the desktop block (larger, e.g. wordmark 28px) and the `@media(max-width:640px)` block (smaller). The `.header-spacer` height must track the bar height per breakpoint so content isn't hidden under the fixed header.

- **Mobile sizing lands via a two-step correction, not one shot.** First too big (40px) → he says "perkecil" → you overshoot small (18px) → he says "ga besar ga kecil, di tengah-tengah". Land on the MIDPOINT (~23px wordmark, shade-bar padding 8px, header-spacer 40px, chevron 25px). Expect the "middle ground" ask; don't camp at either extreme.
- One media-query breakpoint governs the whole tier: `≤640px` = mobile (wordmark ~23px), `≥641px` = laptop/desktop (wordmark 28px, tall statue). Media queries read viewport WIDTH not device type, so "desktop-mode on a phone" == laptop view by definition — explain this if he's confused why they match.

## Scroll-driven animation on the layer-2 (blue) canvas

Aes asks for the second background to "buat animasi pada saat scroll". Cheapest robust approach: a moving radial glow whose vertical position tracks scroll progress through `.canvas2`, driven by a CSS var updated in the existing `onScroll` handler (reuse the same scroll listener that pushes the header — don't add a second one).

- CSS: `.canvas2::after{content:"";position:absolute;inset:0;z-index:0;pointer-events:none;background:radial-gradient(58% 42% at 50% var(--gy,16%),rgba(120,150,255,.30),transparent 62%);transition:background .15s linear}` and `@media(prefers-reduced-motion:reduce){.canvas2::after{transition:none}}`. Keep `.canvas2-inner` at `z-index:1` so content stays above the glow, alongside the existing `::before` grain.
- JS inside `onScroll`: `const r=canvas.getBoundingClientRect(); const p=Math.max(0,Math.min(1,(innerHeight-r.top)/(innerHeight+r.height))); canvas.style.setProperty('--gy',(8+p*84).toFixed(1)+'%');` — glow slides top→bottom (8%→92%) as the panel travels through the viewport. Deterministic from scroll position (no rAF loop, no flicker), same lesson as the header-push model.
- **After migrating to per-section `theme-blue` (see alternating-themes section), drive the glow on EACH blue section, not one canvas.** Collect `const blues=[...document.querySelectorAll('.theme-blue')]` once, then in `onScroll` loop `blues.forEach(sec=>{const r=sec.getBoundingClientRect(); const p=…; sec.style.setProperty('--gy',(10+p*80).toFixed(1)+'%');})`. Also repoint the header-push element from `.canvas2` to the first content section (`#about`) since `.canvas2` no longer exists.

## Scroll-reveal animation style — 3D pop-up, NOT blur

Aes's settled preference for the `.reveal` entrance: a smooth 3D pop-up. He explicitly asked to **remove a blur-based reveal** ("hapus effect blur, ubah dengan effect 3d pop up yang smooth"). Blur-in (`filter:blur(6px)→0`) was rejected. Use a Z-translate + slight rotateX + scale that "pops" forward:
```
.reveal{opacity:0;transform:perspective(900px) translateY(34px) translateZ(-60px) rotateX(8deg) scale(.97);
  transform-origin:center bottom;
  transition:opacity .6s ease,transform .85s cubic-bezier(.19,1,.22,1);
  will-change:opacity,transform}
.reveal.in{opacity:1;transform:perspective(900px) translateY(0) translateZ(0) rotateX(0) scale(1)}
.reveal-late{transition-delay:.09s}.reveal-later{transition-delay:.18s}
@media(prefers-reduced-motion:reduce){.reveal,.paper{transition:none;opacity:1;transform:none}}
```
Stagger via `.reveal-late`/`.reveal-later` delays. Keep the `prefers-reduced-motion` off-switch. `cubic-bezier(.19,1,.22,1)` gives the smooth "landing" feel he wants.

## Reveal ONLY after scroll (not on load)

When Aes says an element (e.g. hero CTA buttons) "tetep muncul pas awal, buat pas discroll baru muncul", a plain `.reveal` + IntersectionObserver is WRONG: above-the-fold elements are already intersecting at page load, so they animate in immediately. He wants them hidden on load and revealed only once the user actually scrolls.

Fix: give those elements a `scroll-only` marker class, EXCLUDE them from the default observer (`querySelectorAll('.reveal:not(.scroll-only)')`), and arm a second observer only after the first real scroll:
```
(function(){
  const items=[...document.querySelectorAll('.reveal.scroll-only')];
  if(!items.length) return;
  let armed=false;
  const so=new IntersectionObserver((e)=>{e.forEach(x=>{if(armed&&x.isIntersecting)x.target.classList.add('in')})},{threshold:.14});
  function arm(){ if(armed)return; if(scrollY>4){armed=true; items.forEach(i=>so.observe(i)); removeEventListener('scroll',arm);} }
  addEventListener('scroll',arm,{passive:true});
})();
```
The element keeps the base `.reveal` hidden state (`opacity:0;transform:translateY(16px)`), so it stays invisible until the armed observer adds `.in`.

## Flat vs gradient backgrounds

He often wants the layer-2 blue as ONE flat solid color top-to-bottom (`background:#12228f`) — "jangan gradasi". Keep the faint grain `::before` and edge box-shadow so it isn't dead-flat, but drop the `linear-gradient`/`radial-gradient` glow when he asks. (This flips the other way sometimes — see the "reverting textures" note above. Read the exact request.)

## Button color conventions (this palette)

- Buttons on the WHITE hero: solid primary = cobalt `--blue-deep` (#0f1c8f) fill + white text, hover to brighter `--blue`. He explicitly rejects the light-blue `--accent` (#0A84FF) for hero buttons AND rejects plain black/ink fills — "jangan hitam, biru seperti canvas, jangan biru muda".
- Buttons on the BLUE canvas: primary = white fill + `--blue-deep` text (`.btn-primary`); secondary/ghost = transparent + white outline + white text (`.btn-light-ghost` / `.btn-ghost`), hover fills white. He wants matching buttons to look identical across sections (e.g. "View on GitHub" should match "Join the community" — both white ghost).

## Recolor a raster wordmark/logo to white or ink (transparent bg)

Faint/dark logo on solid bg → derive alpha from luminance so it works on any background. See `references/image-recolor-and-protect.md` for the PIL recipe (produces wordmark-white.png / wordmark-ink.png) and the non-downloadable markup.

## Drag-to-open notification-shade nav

The header (wordmark + a bare chevron "V") is a single `.shade` unit that slides down; the menu panel sits ABOVE it and is hidden above the viewport when closed. Dragging the bar pulls the whole thing down like an Android notification shade — wordmark + V get dragged down together. Full recipe (CSS transform var, touch/mouse drag with snap threshold, rAF smoothing) in `references/notification-shade-menu.md`.

**Grip glyph churn (expect it):** the drag handle started as an animated 3-chevron "V" (breathing keyframe, right-aligned). Aes then asked to replace it with a plain single dash "-" centered UNDER the logo, and to make it DEAD STILL ("jangan gerak"). When he says the grip should stop moving, delete the `@keyframes`/`animation` entirely — don't just slow it. Simplest static grip: `<span class="pull-v"><i class="dash"></i></span>` + `.pull-v{position:absolute;left:50%;bottom:3px;transform:translateX(-50%);pointer-events:none}` and `.dash{width:22px;height:2px;background:var(--ink)}`. When swapping the glyph, remove the OLD svg/chevron CSS (the `.pull-v svg`, rotate-on-open, keyframes) so no dead rules linger. Critical constraint he repeats: changing the grip must NOT alter banner size or logo size/position — only the grip element.

**Header + layer-2 canvas interaction:** when Aes wants the fixed header to react to the blue `.canvas2` scrolling into it, use the deterministic PUSH model (header shoved up by exactly the overlap), NOT a `hidden` class toggle. Computing collision from the header's own moving rect causes a feedback loop → "random banget" flicker on Android. Full corrected recipe + failure analysis in `references/notification-shade-menu.md` under "Header pushed up by the layer-2 (blue) canvas".

## Swapping the hero (or any) image asset

Aes drops a new image and says "ubah gambar jadi ini" / "ubah jadi ini aja". Workflow: back up the current asset once (`cp hero-statue.png hero-statue.bak.png`), copy the new file OVER the same asset name (so no markup path changes), then **bump the `?v=N` cache-bust query in the `<img src>`** (v2→v3…). Verify BOTH the asset and page return 200: `curl -s -o /dev/null -w "img:%{http_code}" "http://127.0.0.1:PORT/assets/hero-statue.png?v=3"`. He iterates on the image repeatedly ("ubah jadi ini aja" again) — just overwrite + bump the version each time. Images he sends land under `~/.zeline/cache/images/img_*.png`.

## Remove ornamental glyphs from buttons/links

Part of his anti-clutter habit: he'll say "hilangkan semua panah ↗ di setiap tombol". Grep the whole file for the glyph (`↗ → ↘ ↑ ←`) and strip every `<span class="ic">↗</span>` (and any bare glyph in link text) across ALL buttons/nav links in one pass. Ignore matches inside JS comments (e.g. `→` in a code comment) — those don't render.

## Verification

HTTP 200 alone doesn't prove the browser sees the new version (304 cache). Grep the served HTML for the exact new marker/class, count elements, and confirm removed markers are gone. Render/inspect at mobile width for overflow and element collisions.
