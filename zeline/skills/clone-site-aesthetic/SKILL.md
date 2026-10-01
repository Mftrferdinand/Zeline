---
name: clone-site-aesthetic
description: Rebuild a website to match a reference site's look by scraping its shipped/compiled assets — extract the palette from the CSS bundle, the content structure from the JS bundle, and the fonts, then reconstruct it in your own framework (Astro/React/static HTML).
version: 1.0.0
author: Zeline Agent
license: MIT
platforms: [linux, android, web]
tags: [web-design, portfolio, landing-page, astro, css-extraction, reverse-engineering, branding]
metadata:
  zeline:
    created_by: agent
---

# Clone a Site's Aesthetic From Its Shipped Assets

Use this when the user says "bikin web persis kaya gitu" / "rombak jadi seperti [URL]" / "make it look like this site" and gives a reference URL. The goal is to match the *visual language and structure* (palette, fonts, layout rhythm, section pattern) of a live site, then rebuild it — usually inside an existing project (an Astro landing page, a React app, or plain HTML) rather than from scratch.

Do NOT eyeball the design from a screenshot and guess hex values. Modern sites ship a compiled CSS bundle and (for SPAs) a JS bundle that contain the exact palette, fonts, and even the copy/section structure. Scrape those first.

## Step 1 — Fetch the HTML and identify the stack

```bash
cd ~ && curl -sL "https://REFERENCE.com" -H "User-Agent: Mozilla/5.0" -m 20 -o ref.html
```
Then `read_file ref.html`. Look at `<head>`:
- `<meta name="theme-color">` — the intended base background color, a strong palette hint.
- `og:*` / `twitter:*` / JSON-LD (`application/ld+json`) — gives you the person/brand, job titles, real copy, and `sameAs` links. This is gold for reconstructing an *about/portfolio* site's content.
- `<link rel="stylesheet" href="/assets/index-XXXX.css">` and `<script src="/assets/index-YYYY.js">` — the bundle paths to fetch next.
- Framework tells: `__NEXT_DATA__`/`_next` (Next.js), `data-astro`/`astro` (Astro), a lone `<div id="root">` + module script (Vite React SPA).

## Step 2 — Extract the exact palette from the CSS bundle

```bash
curl -sL "https://REFERENCE.com/assets/index-XXXX.css" -H "User-Agent: Mozilla/5.0" -m 20 -o ref.css
# dominant colors by frequency — the top ~8 ARE the palette
grep -oiE '#[0-9a-f]{6}' ref.css | sort | uniq -c | sort -rn | head -15
# fonts actually used
grep -oiE "font-family:[^;}]+" ref.css | sort -u
grep -oiE 'family=[A-Za-z+]+' ref.html   # google fonts if linked in HTML
```
The highest-frequency hexes are the real design tokens. A typical warm-editorial result looked like: `#17161a` (ink), `#f4f1ea` (cream bg), `#c0392b` (brick-red accent), `#6b6874` (muted), `#1a8f3c` (green). Use those verbatim as your CSS variables — matching the exact hexes is what makes the clone read as "persis" (identical) rather than "kinda similar."

## Step 3 — Extract content + section structure from the JS bundle (SPAs)

For a React/Vite SPA the visible text lives as quoted strings in the JS bundle:
```bash
curl -sL "https://REFERENCE.com/assets/index-YYYY.js" -H "User-Agent: Mozilla/5.0" -m 25 -o ref.js
python3 -c "
import re
js=open('ref.js',encoding='utf-8',errors='ignore').read()
strs=re.findall(r'\"([A-Z][A-Za-z0-9 ,.:&/\'-]{5,90})\"', js)
seen=[]
for s in strs:
    s=s.strip()
    if s and s not in seen and not s.startswith('http') and ' ' in s: seen.append(s)
for s in seen[:180]: print(s)
"
```
This dumps section titles, taglines, package/pricing labels, project names, timeline entries — enough to reconstruct the *information architecture* (Hero → Stats → Work → About → Skills → Experience → Contact) and mirror the section rhythm. Adapt the copy to the user's own profile; don't copy the reference person's identity.

## Step 4 — Rebuild inside the existing project

- If rebranding an existing Astro/landing project, replace the page's main `index.astro` (or the relevant component) wholesale. Put all styling in one `<style is:global>` block with the extracted palette as `:root` CSS variables — easier than fighting the old Tailwind config.
- Load the detected fonts via Google Fonts `<link>` in `<head>` (e.g. `Fraunces` serif for editorial display + `Inter` for body). A serif display face on a warm bg is the single biggest driver of a "premium editorial" feel.
- Reproduce the section pattern: numbered section indices (`01`, `02`…), large serif section titles, generous vertical padding (`clamp(60px,12vw,130px)`), a sticky blurred nav, and a scroll-reveal on each block.
- **Scroll reveal without a library**: add `.reveal { opacity:0; transform:translateY(18px); transition:.7s cubic-bezier(.22,1,.36,1) }` + `.reveal.in { opacity:1; transform:none }`, then a tiny inline `IntersectionObserver` that adds `.in` on intersect. No GSAP/motion dependency needed for the basic effect.

## Step 5 — Serve & verify

- Astro dev server (`npx astro dev --port PORT --host`) hot-reloads on file save; just `curl` the port and grep for a new hero string to confirm the rebuild is live. `--host` also exposes it on the LAN.
- Verify the served HTML actually contains your new title/hero copy, not the old template's.
- For custom static documentation rebuilds, mobile drawers, bilingual EN/ID pages, platform selectors, FAQ accordions, route/docroot verification, and no-cache release gates, follow `references/static-docs-rebuild-and-verification.md`. This is mandatory when the user says changes are not visible: diagnose and replace the exact served root instead of creating more hidden versioned routes.

## Mobile dashboard/menu overlay and theme

For compact mobile dashboard menus with a sharp glass panel over a blurred blue-white page, plus persistent blue-gray dark mode, follow `references/mobile-dashboard-menu-and-theme.md`. It covers correct layer ordering, a 30px visual trigger with a 44px invisible touch target, scroll locking, backdrop-close behavior, `<details>` state synchronization, no-flash theme initialization, and computed-style/screenshot verification.

`references/mobile-glass-navigation.md` contains the older menu-only treatment; prefer the combined dashboard/theme reference for new work.

## Scroll-snap vertical swipe from local source

When the reference is a **local project** on the same device (not a live URL to scrape), read the local HTML source directly instead of scraping. The scroll-snap mechanic (`scroll-snap-type: y mandatory`, `100svh` sections, `requestAnimationFrame`-throttled blur/opacity transition) lives in the inline `<style>` and `<script>`. See `references/scroll-snap-music-player.md` for the full pattern, including building a Spotify-like playlist player with album art, progress bar, and seek — covers the MP3 metadata pitfall (Android files often have empty tags) and the orphaned-process kill workaround.

## Matching button / CTA icon treatment

When the user asks "icon font tombol-tombolnya samakan" / "match the buttons," the reference's buttons almost always pair a label with a small **inline SVG stroke icon** (lucide/feather style: `stroke="currentColor"`, `stroke-width~2.2`, `stroke-linecap="round"`), NOT a text glyph like `→`. Reproduce that look:
- Right-arrow: `<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>`
- Arrow-up-right (external/view): `<path d="M7 7h10v10"/><path d="M7 17 17 7"/>`
- Envelope (email): `<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>`
- Size the icon ~17px, give it `transition: transform .22s` and a hover nudge (`.btn:hover .ic { transform: translate(3px,-3px) }`) — the diagonal slide on hover is a signature premium micro-interaction.
- Grep the reference JS bundle for icon component names to confirm which set they use: `grep -oE '[".](ArrowRight|ArrowUpRight|Mail|Send|Phone|ExternalLink)["\, (]' ref.js`. Also grep for the real CTA labels (`Get in touch`, `View work`) and reuse them verbatim.

## Astro dev server: restart on circular-dependency / stale render

Editing the same `index.astro` repeatedly on a running `astro dev` can throw `[vite] The dependency module is not yet fully initialized due to circular dependency` and then serve stale/broken output even though the file is correct. Fix: `pkill -f "astro dev"`, wait 2s, relaunch `npx astro dev --port PORT --host`, wait ~8-10s for `ready in`, then re-`curl` and grep for the new hero string. A hard restart clears the SSR module cache; don't keep editing against the wedged server.

**Restart-noise is expected, not a failure.** Each `pkill` + relaunch fires a delayed "Background process ... exited (exit code 143, SIGTERM)" / `tcsetattr: Permission denied` notification for the OLD pid. That's the process you intentionally killed — acknowledge it briefly ("that's the old server I restarted, new one is healthy") and move on; do NOT treat it as a crash or try to debug it. Confirm health with `curl -o /dev/null -w "%{http_code}" http://localhost:PORT/` → 200 instead.

## Font sizing: editorial ≠ giant (frequent user correction)

A very common complaint after the first pass is "font gede banget, perkecil" (fonts way too big, shrink them). Editorial/premium does NOT mean huge — the reference site's restraint is part of what makes it look premium. Ship conservative `clamp()` maxes from the start:
- Hero `<h1>`: `clamp(38px, 6.5vw, 68px)` — NOT 96px+.
- Section titles: `clamp(26px, 4.2vw, 40px)` — NOT 54px.
- Contact/CTA headline: `clamp(32px, 5.5vw, 58px)`.
- About lead paragraphs (serif): `clamp(17px, 2vw, 21px)`.
- Body/lead text: `clamp(15px, 1.8vw, 18px)`.
If unsure, measure the reference: `grep -oE 'font-size:clamp\([^)]+\)' ref.css | sort -u` and match its maxes rather than inventing larger ones.

## Footer: build a real multi-column nav footer, not a one-line credit

When the user says "buat menu-menu baru ada tombol-tombol rapih di bawahnya" they want a proper site footer like the reference's, not a `© 2026` one-liner. The standard editorial-portfolio footer shape:
- **Left column (brand):** wordmark + one-line tagline + a row of square social icon buttons (Telegram/GitHub/Email as bordered rounded-12px tiles, `hover` inverts to ink bg + cream fg + `translateY(-2px)`).
- **Right column(s):** two link stacks — "Menu" (Work / About / Experience / Contact) and "Connect" (Telegram / GitHub / Email), each with an uppercase letter-spaced `<h5>` label.
- **Bottom bar:** `border-top`, copyright left + "Back to top ↑" link right.
Grid `1.4fr 1fr` collapsing to single column under 640px.

## Remove the framework's own dev/branding chrome

Astro (and similar) inject dev-only UI the user will call useless ("tombol astro gaguna, hilangkan"):
- Disable the dev toolbar in `astro.config.mjs`: `devToolbar: { enabled: false }`.
- Delete/omit template components like `github-corner.astro`, `starfield.astro`, `theme-switcher.astro`, `splash.astro`, `compatibility.astro` from the page imports when rebranding — the default landing template ships several that have nothing to do with the user's site.
- Verify no template branding survives: `grep -ciE 'github-corner|Built with Astro' live.html` should return 0.

## "Buat lebih hidup / premium" — dependency-free live micro-interactions

When the user asks to make the site "lebih hidup" (more alive), "fitur 3d hidup," "premium animasi bergerak," or "bubble liquid glass ala iphone yang hidup," add these vanilla effects inside the existing `<style is:global>` + one inline `<script>` — no GSAP/motion/three.js needed:

- **Floating ambient orbs** (background depth): 2-4 big `border-radius:50%` divs, `filter: blur(58px)`, low opacity (~0.4), each with a multi-keyframe `@keyframes orbFloat { 0%,100%{translate+scale} 33%{...} 66%{...} }` on a long 16-26s loop. Multi-stage keyframes (not just 0/50/100) read as organic morphing rather than a plain slide.
- **Liquid-glass bubbles ala iPhone**: JS-generate ~8-14 `<span>` bubbles into a fixed layer. Each: `background: linear-gradient(145deg, rgba(255,255,255,0.5), rgba(255,255,255,0.08))`, `backdrop-filter: blur(4px)`, `border:1px solid rgba(255,255,255,0.35)`, and glossy `box-shadow: inset 0 2px 6px rgba(255,255,255,0.6)`. Animate `@keyframes bubbleRise` drifting bottom→top with fade in/out; randomize each bubble's size, `left`, `animation-duration`, and negative `animation-delay` so they're staggered.
- **3D pointer tilt on cards**: `pointermove` handler computes cursor offset within the card rect and sets `transform: perspective(800px) rotateY(px*7deg) rotateX(-py*7deg) translateY(-4px)`; reset to `""` on `pointerleave`. Give cards `transform-style: preserve-3d`. This is the Apple/Linear "live premium" hover.
- **Stat count-up**: IntersectionObserver at threshold 0.5; parse the stat text with `/([\d.]+)(.*)$/` to keep suffixes like `+`, `K+`, `%`; increment over ~32 steps then restore the exact original string.
- **Animated gradient accent text**: on the highlighted words, `background: linear-gradient(90deg,...); background-size:300% 100%; -webkit-background-clip:text; -webkit-text-fill-color:transparent;` + a slow `@keyframes` shifting `background-position`. Cheap, eye-catching, on-brand.
- Always guard motion with `@media (prefers-reduced-motion: reduce) { animation: none }`.

### The live-effects reversal (expect it — don't over-commit up front)
The same user who asks for "lebih hidup / fitur 3d / bubble liquid glass" very often reverses it a turn or two later: **"hilangkan live 3d norak bubble yang bikin lag"** / "hilangkan emoji yang bergerak." On Android/mobile WebView the heavy effects are the culprits:
- **Generated liquid-glass bubbles** (many `backdrop-filter: blur` layers animating simultaneously) are the #1 lag source on WebView — cheap on desktop, janky on a phone. Treat them as opt-in and be ready to rip them out; when removing, delete the DOM node, the generator JS loop, AND the CSS (set `.bubbles{display:none}` isn't enough long-term — remove the keyframes too so they don't bloat).
- **Per-pointer 3D tilt** (`pointermove` → `perspective rotateX/Y` on every card) also stutters on low-end devices and reads as "norak" to some users. Fall back to a plain `translateY(-4px)` hover lift.
- **Bobbing/rotating emoji** (medal 🥇 with a `@keyframes` bob) — users call this childish; keep emoji static.
Safer default for "premium & alive" that survives the reversal: keep only the slow ambient orbs (long 26-38s loops, low opacity, no scale-morph), scroll-reveal, stat count-up, gradient accent text, and button ripple/squash. Lead with THOSE; offer bubbles/tilt only if explicitly asked, and expect to remove them.

### Unified glass-transparency pass ("semua bubble 70% transparan, shadow tepian tipis aja")
After the layout settles, users converge on ONE consistent glass recipe for every card (About, Work, Skill, Award, Community, Tool pills) rather than each section having its own opacity/shadow. When they say "buat semua bubble [N]% transparan, shadow tepianya tipis aja," standardize every card to the SAME tokens in one sweep:
- `background: rgba(255,255,255,0.3)` (0.3 alpha = "70% transparan"), `backdrop-filter: blur(16px) saturate(150%)`, `border: 1px solid rgba(255,255,255,0.4)`.
- **Thin edge shadow only**: replace any chunky drop shadow with `box-shadow: 0 1px 2px rgba(60,45,30,0.05)` — the user explicitly does NOT want a heavy `0 10px 30px` shadow, just a hairline edge. Drop the `inset ... rgba(255,255,255,...)` highlight too when they want it minimal.
- Do it as a single find-replace batch across all card classes (execute_code with a list of old→new pairs is efficient) so they end up truly identical. Leaving one section on the old higher-opacity/heavier-shadow recipe is the thing they'll notice.
- Real transparency (low alpha + blur) lets the ambient orbs show through faintly — that's the intended liquid-glass depth. If cards look flat/opaque, the alpha is too high.

### Caption/badge overflowing its frame ("... terlihat keluar dari foto, perkecil lagi")
A portrait badge (`Founder · Web3 Addicter`) pinned with `left:8px; right:8px` or a long label at 9-10px can overflow/clip the rounded photo frame. Fix: shrink font to ~7-8px, center it (`left:50%; transform:translateX(-50%)`), and cap width with `max-width: calc(100% - 12px); overflow:hidden; text-overflow:ellipsis; white-space:nowrap`. Simpler/smaller is what "buat simpel profesional" means here — don't let the badge dominate the photo.
- **"terlihat terpotong di HP" recurs** — when the label still clips on a phone, don't just shrink font again blindly: shorten the TEXT itself (Founder · Web3 Addicter → Owner Web3 Addicter, drop separators/titles) AND switch to the premium micro-label recipe below. A shorter string is the most reliable fix.
- **Premium micro-label recipe** (makes a tiny badge look intentional, not cramped): font-size:6px; text-transform:uppercase; letter-spacing:0.06em; color:rgba(255,255,255,0.92); backdrop-filter:blur(12px) saturate(1.2); border:1px solid rgba(255,255,255,0.16); box-shadow:0 2px 10px rgba(0,0,0,0.18). Uppercase + wide tracking at a small size reads as a designed caption chip rather than shrunken body text — this is the move when the user says "perkecil ... buat lebih premium".

### About typography clean-up ("rapihkan about jadi clean, font/ukuran premium")
For a polished About block: make the FIRST paragraph a serif highlight (`font-family: serif; font-size:17px; font-weight:500; color:ink`) and the remaining paragraphs plain sans (`Inter; 14.5px; line-height:1.7; color:ink-2`). The serif→sans hierarchy on the opening line reads editorial/premium; uniform serif for all paragraphs looks heavy. Generous line-height (1.7) on body copy is a big part of "clean."
- **Serif-italic keyword accents = the cheapest "lebih premium" win.** When the user says "buat lebih premium di about," don't just tweak spacing — restyle the inline emphasized words (already wrapped in `<em>`, e.g. *Artificial Intelligence, Web3, automation*) to render as the display serif in italic + the brand accent color: `.about-body em { font-family: var(--serif); font-style: italic; font-weight:500; color: var(--accent); letter-spacing:0 }`. A serif-italic brick-red accent inside a sans paragraph is an editorial magazine move that instantly lifts perceived quality. Also bump the lead paragraph to full ink color + slightly larger, and nudge line-height/paragraph-gap up. This "premium = serif-italic accent on keywords, NOT decorative flourish" is a durable taste for this user.

### Pill/tag consistency ("satukan, ukurannya samakan seperti yang lain")
When one group of chips is styled differently from the rest (e.g. a bespoke Languages row with flag emoji + two-line pills while every other section uses simple tag chips), the user will say "satukan aja ... ukurannya samakan seperti bubble yang lain." Fix = fold the odd-one-out into the SAME component/CSS class as the others (make Languages just another `tool-block` with plain `tool-tag` chips like "Indonesian — Native"), and drop the bespoke styles. Reuse one chip class everywhere rather than per-section variants.

## Buttons must feel "hidup" when clicked (ripple + squash)

"Buat tombol-tombolnya hidup ketika di klik" = tactile press feedback:
- **Ripple burst**: on `pointerdown`, append a `.ripple` span positioned at the click point (`e.clientX - rect.left - size/2`), sized to the button's max dimension, animating `transform: scale(0)→scale(3.2)` + fade over .6s, then `setTimeout(remove, 600)`. Button needs `position:relative; overflow:hidden` and label/icon at `z-index:1` so the ripple sits behind.
- **Squash**: `.btn:active { transform: scale(0.93) }` with a springy `transition: transform .18s cubic-bezier(.34,1.56,.64,1)` so it bounces back. Apply the active-squash to every clickable pill (CTAs, community links) for consistency.

## Color-clash correction: kill accents that fight the palette

The user will call out an animated element whose color doesn't belong ("hilangkan animasi ... yang berwarna hijau"). When adding decorative orbs/gradients, keep them WITHIN the extracted palette family (e.g. for a warm cream+brick-red site use red / gold / coral orbs, not a green one). A stray off-palette accent reads as noise. Recolor to a palette-adjacent hue rather than deleting the effect.

## Density correction: "lebih padat, jangan banyak spasi kosong"

Alongside the font-size complaint, users often want tighter vertical rhythm. When they say "lebih padat" / "jangan banyak space kosong": roughly halve section padding (`clamp(56px,10vw,110px)` → `clamp(38px,6vw,68px)`), cut `section-head` margin (~40px → ~28px), tighten card padding and inter-item gaps by a few px each, and drop base `font-size` 16px → 15px. Do the density pass and the font-shrink pass together — they're usually requested in the same breath.

## Iterative content growth: drive sections from frontmatter arrays

Portfolio builds are almost never one-shot — the user drips in more content over many turns ("here's my Tools list", "add Awards", "here are more highlights"). Structure the page so adding a section is cheap:
- Keep every section's data as a `const` array in the Astro frontmatter (`const awards = [...]`, `const tools = [...]`, `const stats = [...]`), then `.map()` it in the body. Adding an item = editing one array entry; adding a section = one array + one `<section>` block + its CSS.
- Each new section the user sends (Awards, Tools of the Trade, Hero Highlights, Languages) maps to: a data array, a `<section id="...">` with a `.map()`, a nav `<a href="#id">` link, and a small CSS block reusing the existing glass-card / hover-lift classes for visual consistency.
- **Section-index renumbering pitfall**: sections carry numbered badges (`01`, `02`…). Inserting a new section mid-page means every downstream badge shifts by one. After inserting, re-grep and fix them in order: `search_files pattern='section-index">0[0-9]'` then patch each. Miss one and you get duplicate `05`s. Do the renumber in the same turn as the insert.
- Stat grids: when the highlight count changes (4 → 6), also fix the grid column count (`repeat(4,1fr)` → `repeat(3,1fr)`) so the layout stays balanced.

## Hero portrait: user sends their own photo ("ini foto gua, gunakan di web")

When the user drops a headshot to feature, put it in the hero as a two-column layout, not a full-bleed banner:
- **Copy the asset into the project** (`public/portrait.png`) — never reference the `~/.zeline/cache/...` path, it's transient. Astro serves `public/` at web root, so `src="/portrait.png"`.
- Hero becomes `.hero-inner { display:grid; grid-template-columns: 1.15fr 0.85fr; gap:clamp(24px,5vw,56px); align-items:center }`; under ~780px collapse to one column and `order:-1` the portrait so it sits on top, capped `max-width:260px`.
- **Framed portrait treatment** (premium): `aspect-ratio: 4/5`, `border-radius:24px`, `overflow:hidden`, cream border, deep `box-shadow: 0 24px 60px rgba(...,0.18)`, a `::after` bottom gradient overlay (`transparent 55% → rgba(ink,0.22)`) so a caption badge stays legible, plus a subtle hover `img{transform:scale(1.04)}` zoom.
- Add a small glass **badge** pinned bottom-left (`Founder · Web3 Addicter`): dark translucent bg + `backdrop-filter:blur`, rounded-999px.
- Verify the image serves: `curl -o /dev/null -w "%{http_code}" http://localhost:PORT/portrait.png` → 200.

### Photo placement reversal: hero → About, and the float-wrap fix for empty space
Expect the user to move the portrait after seeing it ("pindahkan foto ke bagian about"). When they also say "perkecil, tulisan di samping foto dan di bawahnya, jangan ada space kosong gegara foto," a two-column grid is WRONG — a grid leaves a tall empty gap below a short photo when the text column is longer. Use a **CSS float** so the text wraps beside the image and then continues underneath it:
- `.about-portrait { float: left; width: 200px; margin: 4px 26px 12px 0 }` (shrink to ~150px on mobile).
- `.about-body { display: block }` with `p { margin-bottom: 14px }` (NOT flex/grid — flex won't wrap around a float).
- Clear the float on the container: `.about-layout::after { content:''; display:block; clear:both }`.
This gives the magazine-style text-wrapping-around-photo look with zero dead space, which is exactly what "jangan ada space kosong" means. Remove the old hero-portrait grid column when moving it.

### "Kasih bubble termasuk foto dan deskripsi jadi 1 bubble"
Users often then want the whole About (photo + all paragraphs) wrapped in ONE glass card, not loose on the page. Put the floated portrait AND the `.about-body` paragraphs inside a single `.about-card` styled like the other glass bubbles (`background: linear-gradient(150deg, rgba(255,255,255,0.9), rgba(252,251,248,0.72)); border:1px solid line; border-radius:24px; padding:26px; box-shadow: 0 10px 30px + inset highlight`). Keep the float-wrap INSIDE that card (float still needs the `::after{clear:both}` on `.about-card`) so text wraps beside then below the photo — a two-column grid inside the card reintroduces the empty-gap problem.

## Premium achievements/awards: serif rank numbers, NOT emoji medals

Users equate emoji with "not premium" and will explicitly say "jangan pakai emoji hilangkan dan ganti yang lebih premium." When building an Awards/Recognition section, do NOT lead each item with 🥇🥈🛡️🎤. Instead:
- Give each award a `rank: "01".."04"` field and render it as a **large serif number** (`font-family: serif; font-size:28px; color:accent`) separated from the body by a `border-right: 1px solid line; padding-right:16px` divider — mirrors the numbered section-index treatment and reads editorial/premium.
- Same principle applies broadly: for a "premium" feel this user wants typographic/serif treatment over emoji anywhere (stat labels, list bullets, medals). Static emoji at most; never animated/bobbing emoji.

## Official brand logos in a tech-stack / "Tools of the Trade" section

When the user asks for "pakai logo resmi"/"use official logos" on a stack list, pull them from **Simple Icons CDN** — no downloads, auto brand colors:
- `https://cdn.simpleicons.org/<slug>` returns a colored SVG. Slug = lowercase brand name, dots spelled out: `nextdotjs`, `nodedotjs`, `googlegemini`, `tailwindcss`. Render as `<img class="tool-logo" src=... width="16" height="16" loading="lazy">`.
- **Verify every slug before shipping** — loop a `curl -s -o /dev/null -w "%{http_code}"` over the list; 404 means wrong slug or the brand pulled its icon.
- **OpenAI 404s** (brand-policy takedown, so do `chatgpt`, some others). For those, embed the logo as an **inline `<svg fill="currentColor">`** with the known path data instead of the CDN img. Keep a sentinel slug like `"openai-inline"` and branch on it in the `.map()`.
- **"Kalau ga nemu gausah dipasang"**: if a tool has no official logo (Zeline, VPS, Cursor, VS Code, REST API), DROP it from the list entirely rather than showing a bare dot — the user wants a clean logo wall, not placeholders.
- **"Gabungkan aja jangan ada AI/Frontend/Backend, urutkan A-Z"**: users often want the grouped stack flattened into one **A-Z sorted** flat pill row (`display:flex; flex-wrap:wrap; gap:8px`), each pill = logo + name, rounded-999px, hover lift (NOT invert — invert hides the colored logo). Flatten the grouped data array into a single sorted list.
- **Truncation pitfall — use flex-wrap, NOT a fixed-width grid, for the pill row.** A grid with `repeat(auto-fill, minmax(104px,1fr))` forces every pill to a fixed width, so long labels (WalletConnect, PostgreSQL, Tailwind CSS) get clipped/ellipsised — the user WILL say "banyak tulisan tidak cukup terlihat terpotong". Fix: `.tools-flat { display:flex; flex-wrap:wrap; gap:8px }` + `.tool-tag { white-space:nowrap }` so each bubble sizes to its own content and wraps naturally. If the user separately demands exactly N-per-row on mobile, that conflicts with no-truncation — prefer no-truncation (flex-wrap) and let the count float unless every label is short.

## Static-site deployment: cache-bust assets and prove the visual result

When restyling an existing static site, editing `style.css` alone does not prove that the user sees the result. Browser/WebView caches often keep the old stylesheet while its URL remains unchanged.

1. **Version shared CSS/JS URLs** after a visual pass, for example `static/assets/css/style.css?v=20260731-1`. Make the generator/template emit the versioned URL, then regenerate every page; do not patch only the home page.
2. **Verify source and live delivery separately:** confirm a design sentinel/token exists in the local stylesheet *and* in `curl http://localhost:PORT/static/assets/css/style.css?v=...`.
3. **Render an actual browser screenshot** with headless Chromium at the relevant viewport and inspect it with vision before saying the redesign is visible. HTTP `200` and text grep only validate delivery, not the visual layout.
4. If a user says they see no change, provide a versioned page URL such as `http://localhost:PORT/?v=...`, then report only what the screenshot and served source establish. Do not state browser cache as the definite cause without evidence.
5. After a global rebrand, verify exact brand strings in the served HTML/DOM. Visual OCR can be unreliable for words in a logo/nav.

## Documentation-route redesigns: make the changed page unavoidable

When a user asks to turn an existing guide into a reference-style technical tutorial, do **not** limit the work to CSS tokens or a hidden secondary page. They may report "ga ada perubahan" even if the stylesheet changed, because the browser is still showing the legacy route.

1. **Identify the route the user opens first** (usually `/`) and make it visibly lead to or render the rebuilt tutorial. Use a redirect only when the user wants the old landing removed; otherwise rebuild the landing with an unmistakable tutorial CTA.
2. **Make a substantial first-screen change:** tutorial redesign means a step sidebar, compact article column, real command blocks, and Copy controls—not merely a palette swap. Mirror the reference information architecture, never its identity/copy.
3. **Treat commands as product documentation:** verify them against the official product source/skill. Do not leave or route toward legacy pages containing invented install commands merely because they already exist.
4. **Cache-bust the exact rendered asset** (`style.css?v=<release>`), update the route URL to that release, and fetch both the page and versioned CSS to prove delivery.
5. **Render the target route in headless Chromium and inspect the screenshot before saying the user can see the change.** HTTP 200 and CSS greps are not visual evidence.
6. **Audit legibility in the screenshot.** Dark code blocks carry the key instructions: explicitly give code a high-contrast foreground and ensure Copy buttons are readable/tappable.
7. **Compact means measured density, not tiny text.** Reduce oversized hero/card padding and sidebar width first; retain readable body/code text on Android. For the user's 8081-style reference, use blue-gray `#D8E0E7`, accent `#0A84FF`, subtle glass panels, and minimal transform-only motion.

## User sent brand assets → USE them, do NOT recreate or animate (hard-won)

When the user drops their own logo / wordmark / emblem / concept art (one or
several images) and asks to build a site with them, the job is to **place those
exact images**, not to reverse-engineer them into hand-coded SVG/CSS and
certainly not to animate them. Trying to "faithfully rebuild" a logo as SVG math
(tapered needles, 3D-projected orbital rings, draw-on strokes) is a rabbit hole
that reliably ends in escalating frustration — real quotes from one session:
"jauh banget dr logo aslinya", "muternya aneh banget ga 3d", "gausah ada animasi
logo da ribet lu mah oon", "pake gambar gambar gua aja plenger". Every animation
iteration was wasted effort.

Do this instead, immediately:
1. **Copy every provided image into the project** (`public/` or `assets/`) with
   stable descriptive names — never reference the transient
   `~/.zeline/cache/images/img_*.{png,jpg}` path in HTML; it disappears.
   ```bash
   mkdir -p assets && cp ~/.zeline/cache/images/img_XXXX.png assets/wordmark.png
   ```
2. **Drop them straight into `<img>` tags** at the right spots (wordmark → nav +
   hero + footer; logo mark → hero centerpiece; concept art → section visuals /
   CTA background). Add only lightweight, non-destructive polish around them:
   `drop-shadow` glow, `object-fit:cover` framing, a hover `scale(1.04)`, a
   scroll-reveal fade. That's it.
3. **Verify each asset serves 200**: loop `curl -o /dev/null -w "%{http_code}"`
   over every `assets/<name>` URL before declaring done.

Only build a logo from code when the user has NO image and explicitly asks you
to design one. If they gave you the picture, the picture IS the deliverable.
Ambient/background animation of the *page* is fine; animating the *logo itself*
is almost always unwanted — don't offer it, and if you did, expect "hilangkan".

### The elaborate-hero reversal → plain text (expect it)
Even a full Three.js 3D hero with animated brand text — the thing the user
explicitly asked for ("bikin full 3d ... tulisan brand juga hidup") — frequently
gets reverted a few turns later to bare typography: "hapus semua gambar, jangan
ada logo, di samping tombol menu cm tulisan ZEROLINEAR". Lead with a restrained
text-first hero (wordmark rendered as CSS type — `font-weight:300;
letter-spacing:.34em; text-transform:uppercase` reads premium) and treat heavy
3D/canvas as opt-in you're ready to rip out. Don't sink turns polishing a 3D
scene before the layout/content is settled.

### CDN-font 404 silently kills an entire ES-module `<script type=module>` (debugging path)
Symptom: user reports "gada animasi apa apa" / blank canvas where a Three.js (or
any `type="module"`) hero should be. Cause: one failed `import` or a hard-coded
asset URL that 404s throws at module-eval time and aborts the WHOLE module — no
partial execution, no visible error unless the console is open. In one session a
troika-three-text font pinned to `https://fonts.gstatic.com/s/jost/v15/....woff`
returned 404 and killed the entire scene. Diagnose and fix:
- `curl -s -o /dev/null -w "%{http_code}" "<each CDN/font/lib URL>"` — verify
  every external URL the module pulls (three.module.js, addons, troika, font
  files) returns 200 before blaming the code. A single 404 is enough.
- Prefer **zero external font dependency for 3D text**: render the wordmark to a
  `<canvas>` with the page's already-loaded webfont (fallback Arial) and use it
  as a `THREE.CanvasTexture` on a plane — no troika, no remote `.woff` that can
  404.
- **Wrap the whole init in try/catch** and surface `err.message` into the loader
  element so a future failure shows text instead of a silent blank.
- Module 3D heroes need live internet (CDN). Note this to the user; for
  production, bundle the libs locally so an offline/CDN-down load doesn't blank
  the hero.

### Two-layer "inset frame" background (premium framing, dependency-free)
When the user wants a framed look — "background 2 lapis, hitam dan hitam sedikit
putih jadi kaya ada bingkai" — don't use a fixed full-page `::before` overlay
(it traps content behind it and applies to the whole page including the hero).
Instead wrap only the *content band* the user wants framed in a `<div class="framed">`:
`margin:0 14px; background:var(--bg2)` (a hair lighter than the page `--bg`);
`border:1px solid var(--line); border-radius:20px; box-shadow: inset 0 0 0 1px
rgba(255,255,255,0.02), 0 0 70px rgba(0,0,0,.55)`. Page background stays the
darker/purer layer as the outer margin; the panel is the slightly-lighter inner
frame. Users typically want the hero/nav OUTSIDE the frame and only the
mid-page content sections INSIDE it ("bagian atas jangan pakai bingkai, bingkai
masuk di pertengahan yg membahas isi") — so open the `.framed` div after the
hero and close it before the CTA/footer.

### Blue "research-lab" landing + the nav/terminal over-engineering trap
For a deep-blue AI-lab landing (two-layer blue-zone + inset cream "paper" canvas, image-as-hero fading into paper, transparent-PNG-from-black logo conversion, non-downloadable wordmark, mobile sizing), AND the hard-won lesson that fancy nav interactions (swipe/notification-shade, chevron-float, motion-blur drag, drag-to-grow wordmark) and fake terminal/install UI reliably get built-then-reverted — lead simple — see `references/blue-lab-landing-and-nav-overengineering.md`.

### Nav = wordmark text + a single menu button (no logo image)
A recurring end-state for this user's landing pages: kill the nav logo image and
the horizontal link row, leaving just the CSS wordmark on the left and one
hamburger **menu button** on the right that toggles a slide-down drawer. Drawer:
`position:fixed; inset:HEADERH 0 auto 0; transform:translateY(-130%)` →
`.open{transform:translateY(0)}` with a cubic-bezier transition; hamburger spans
animate into an X via `nth-child` rotate; clicking any drawer link closes it.
Show the menu button at ALL widths (not just mobile) when the user says
"jadikan tombol menu".

## Analyze/audit a SAVED HTML for design tokens (read-only, no rebuild)

Sometimes the ask is just "analyze the design system of this saved HTML and report exact tokens I can rebuild with" — an audit, not a rebuild. The file is a local `.html` already on disk (often a Next.js/SSR dump). Work from the file; don't assume network.

- **Token-name-only files are the common case.** Modern Next.js pages reference tokens as `var(--ink)`, `var(--accent)`, `var(--paper)` etc., but the *values* live in an external hashed CSS bundle (`/_next/static/chunks/XXXX.css`) that is NOT in the saved HTML. `grep -oE 'var\(--[a-z0-9-]+\)' file.html | sort | uniq -c | sort -rn` gives you the token *vocabulary* and usage frequency (most-used = most important), but not the hexes.
- **Recover the hexes from what IS in the file first:** inline `style="..."` attributes (`grep -oiE 'style=.[^"]{1,200}'`), literal `#hex`/`rgba()` (`grep -oiE '#[0-9a-f]{6}|rgba?\([0-9., ]+\)'`), and SVG `fill=`/`stroke=` attrs (these reveal which tokens map to ink/muted/accent/paper even without values).
- **Cross-reference sibling files for missing values (key trick).** If the user has earlier drafts/rebuilds of the same design in the same directory (e.g. `l3.html … l28.html`, `live2.html`), those often carry the SAME palette inline in a `:root{}` block. `grep -roE '\-\-(paper|ink|accent|line)\s*:\s*[^;}\n]+' *.html | sort -u` recovers the confirmed hexes (`--accent:#c0392b`, `--ink:#17161a`, cream `#f4f1ea`, `--line:#e2ddd0`). This turned a token-name-only file into fully-specified tokens without any network fetch.
- **If network is available**, fetch the referenced CSS bundle directly (`curl -sL "https://SITE/_next/static/chunks/XXXX.css"`) for the authoritative values incl. `clamp()` font scales. If it returns `HTTP 000`/empty (offline), say so and fall back to sibling cross-reference — do not invent values.
- **Report structure, not just colors.** A useful token report also extracts: class-token frequency (`grep -oE 'class="([^"]+)"'` → component vocabulary like `.service-card`, `.eyebrow`, `.btn-primary`), section order (`<section class=...>`), headings (`h1..h4`), fonts (the `--font-*` vars + `<html class>` font-module names like `inter_tight`/`jetbrains_mono`), button/card CSS rules (grep the sibling files' `.btn-primary{...}` etc. for shadow/border recipes), and effect signals (`.grain`, `.cursor-dot`, `.reveal`, `transition-delay` stagger).
- **Be explicit about confirmed vs interpolated.** State which hexes are verified (found literally) and which are interpolated from the ink ramp (e.g. `--ink-soft`/`--ink-faint` when only the endpoints exist). Give a safe starting hex but label it as interpolated so the user knows the one value that isn't ground truth.
- Long single-line minified HTML makes `grep -oE '<style...>.{0,3000}'` time out — target specific attributes/patterns instead of trying to slurp big spans, or use a short `python3` regex script.

## Pitfalls

- **Don't redraw the reference's logo/wordmark as guesswork** — if they use a specific letterform, either reuse a copied asset (when it's the user's own brand) or build a clean typographic wordmark; don't approximate someone else's mark pixel-by-pixel.
- **Don't animate a user-provided logo.** See the section above — placing their image statically is the deliverable; SVG-recreation + animation loops are a frustration trap.
- **`vision_analyze` on a screenshot is a weak substitute for scraping the CSS** — it guesses approximate colors; the CSS bundle has the exact tokens. Scrape first, use vision only for layout intuition.
- Some sites gate the bundle behind Cloudflare; if `curl` returns a challenge page, fall back to reading the inline `<style>`/`<head>` and screenshot-driven reconstruction.
- Keep the reference `og:`/JSON-LD copy as *structural* guidance only — swap in the user's real name, role, projects, and links so you're not shipping the reference person's identity.
