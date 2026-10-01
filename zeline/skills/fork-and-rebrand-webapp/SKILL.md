---
name: fork-and-rebrand-webapp
description: Fork an existing open-source web app (React/Vite/Next, etc.) from a GitHub URL and turn it into the user's own — clone, sweep every brand string + rename assets, recolor the theme, wire required API keys, then build+preview to prove it runs. Use when the user drops a GitHub repo URL and says "bikin versi gua / rebrand / adaptasi jadi punya gua".
version: 1.0.0
metadata:
  zeline:
    created_by: agent
    tags: [rebrand, fork, react, vite, tmdb, theme-recolor, termux, open-source]
---

# Fork & Rebrand an Open-Source Web App

Trigger: user drops a GitHub URL and wants their OWN version — "bikin versi sendiri", "rebrand", "adaptasi jadi punya lu", "ganti nama/tema/fitur". The deliverable is a running, rebranded app, verified with a real build + preview — not a description.

This is different from `clone-site-aesthetic` (which scrapes a *live site's* compiled CSS/JS to match its look). Here you adopt the whole *repo* and make it the user's.

## Step 0 — Recon the repo before cloning

```bash
curl -sL "https://api.github.com/repos/OWNER/REPO" | python3 -c "import sys,json;d=json.load(sys.stdin);print('lang:',d.get('language'));print('desc:',d.get('description'));print('license:',(d.get('license') or {}).get('spdx_id'));print('default_branch:',d.get('default_branch'))"
curl -sL "https://api.github.com/repos/OWNER/REPO/contents/" | python3 -c "import sys,json;[print(x['name'],x['type']) for x in json.load(sys.stdin)]"
curl -sL "https://raw.githubusercontent.com/OWNER/REPO/BRANCH/package.json"   # stack, scripts, deps
```
Read the README + `package.json`. Identify: framework (Vite/Next/CRA), required env vars (`.env` / `import.meta.env.VITE_*`), external services (TMDB, Firebase, etc.), and which are MANDATORY vs optional. Confirm the license permits reuse (MIT/Apache are fine; note it in the new README).

## Step 1 — Clone clean, strip git history

```bash
cd ~ && rm -rf NEWNAME; git clone --depth 1 <url> NEWNAME && cd NEWNAME && rm -rf .git
```
`rm -rf .git` makes it a fresh project owned by the user (no upstream history/remotes).

## Step 2 — Confirm the new brand name + palette with the user (one short question each)

Ask the brand name if not given. Ask the accent color (offer 3-4 concrete choices). Don't guess — these two decisions drive every downstream edit.

## Step 3 — Sweep EVERY brand string (case-sensitive) + rename assets

Do the whole rebrand in one `execute_code` pass, then verify zero leftovers. Cover code, config, manifest, SEO meta, sitemap, robots, `.firebaserc`, package name, domains, social handles, and asset filenames.

```python
from pathlib import Path
ROOT = Path(Path.home() / "NEWNAME").resolve(strict=True)
# execute_code runs ordinary Python, not an importable tool bridge.
# Exclude generated trees, symlinks and lockfiles; filenames may contain spaces.
excluded = {'.git', 'node_modules', 'dist', 'build', '.venv'}
files = [p for p in ROOT.rglob('*')
         if p.is_file() and not p.is_symlink()
         and not excluded.intersection(p.relative_to(ROOT).parts)
         and p.resolve().is_relative_to(ROOT)
         and (p.suffix in {'.jsx', '.tsx', '.ts', '.js', '.json', '.html', '.css', '.xml', '.txt'}
              or p.name == '.firebaserc')
         and p.name not in {'package-lock.json', 'npm-shrinkwrap.json'}]
def rebrand(t):
    t=t.replace("www.oldbrand.app","newbrand.app").replace("oldbrand.app","newbrand.app")
    t=t.replace("@oldbrandapp","@newbrandapp")
    t=t.replace("OldBrand","NewBrand").replace("oldbrand","newbrand").replace("OLDBRAND","NEWBRAND")  # case order matters: specific → generic
    return t
for p in files:
    raw=open(p,encoding="utf-8").read(); new=rebrand(raw)
    if new!=raw: open(p,"w",encoding="utf-8").write(new)
# rename asset files too: oldbrand.svg → newbrand.svg, oldbrand2.png → ...
# FINAL: grep must return nothing
leftovers = [str(p.relative_to(ROOT)) for p in files if 'oldbrand' in p.read_text(encoding='utf-8').lower()]
print(leftovers or "CLEAN (checked text files only; inspect asset filenames separately)")
```
- **Case-sensitive replaces in specific→generic order** (`OldBrand` before `oldbrand`) so you don't double-process. A logo split across tags (`Old<span>Brand</span>`) needs its own regex.
- **Second sweep for the stragglers**: `.firebaserc` project id, `public/robots.txt`, `public/sitemap.xml` — grep the WHOLE tree (not just `src/`) to catch them, then re-run the leftover grep until it's `CLEAN`.
- **`grep -ril` first, then edit only matched files** — faster and avoids touching binaries/lockfiles.

## Step 4 — Recolor the theme (bulk Tailwind class swap)

For a Tailwind app, the accent is a color family used as `bg-red-600`, `text-red-500`, `ring-red-500`, `from-red-600`, `shadow-red-700/50`, plus raw `rgba(220,38,38,…)` glows and any `primaryColor=hex` in embed URLs. Swap the whole family in one pass:

```python
tw = [("red-400","blue-400"),("red-500","blue-500"),("red-600","blue-600"),
      ("red-700","blue-700"),("red-900","blue-900"),("red-950","blue-950"),("red-300","blue-300")]
rgba = [("rgba(220,38,38","rgba(37,99,235")]      # red-600 → blue-600 rgb
hexes = [("primaryColor=c45454","primaryColor=2563eb"),("#c45454","#2563eb")]
```
- **Verify in the BUILT CSS, not the source** — after `npm run build`, `grep -oE '\.(bg|text|border|ring|from|to|shadow)-blue-[0-9]+' dist/assets/*.css | wc -l` should be >0 and the red count 0. Source greps can miss classes composed at runtime.
- Also update `<meta name="theme-color">` and manifest `theme_color` if the accent is the chrome color.

## Step 5 — Wire required API keys (read the env contract first)

Write a clear `.env.example` (mandatory vs optional, where to get each key) AND the real `.env`. Distinguish key formats — a wrong-format key authenticates in curl but fails in-app.

**TMDB specifically (common in movie apps):**
- **v3 key** = 32-hex string, used as `?api_key=<key>` query param. This is what most TMDB apps expect. Test: `curl "https://api.themoviedb.org/3/movie/550?api_key=KEY"` → returns the movie.
- **v4 token** = long JWT starting `eyJ...` (scope `api_read`), used as `Authorization: Bearer <token>` header. Test: `curl -H "Authorization: Bearer TOKEN" https://api.themoviedb.org/3/movie/550`.
- They are NOT interchangeable: a v4 token in `?api_key=` returns `Invalid API key`. If the user hands a v4 token to a v3-style app, either (a) ask for the v3 key (cleanest), or (b) add a one-line `window.fetch` shim in `main.jsx` that rewrites TMDB requests to drop `api_key` and add the Bearer header. Prefer (a).
- Firebase/auth keys are usually OPTIONAL — leaving them blank keeps browse/read working while login/watchlist go dark. Say so in `.env.example`.
- **Verify the key with the exact endpoints the app calls** (e.g. `/trending/all/week`) before rebuilding, so "blank screen" is never a mystery.

## Step 6 — Build + preview to PROVE it runs (Vite on Termux)

```bash
npm install --no-audit --no-fund        # ~1 min
# Termux: esbuild's postinstall may be skipped by allow-scripts → binary missing
npm approve-scripts esbuild || node node_modules/esbuild/install.js
./node_modules/.bin/esbuild --version   # confirm it runs
npm run build                            # must print "✓ built in Ns"
npm run preview -- --port 4173           # serves dist/
```
- **esbuild postinstall gotcha**: fresh `npm install` on Termux often leaves esbuild's native binary unbuilt (`allow-scripts` warning). Run `node node_modules/esbuild/install.js` (or `npm approve-scripts esbuild`) or `vite build` throws. This is a setup step, not a broken tool.
- **Preview port fallback**: if 4173 is occupied (a previous preview), Vite silently moves to 4174/4175 and prints the real port in `Local:`. Always read the actual port from the process log before giving the user a URL; health-check each candidate: `for p in 4173 4174; do curl -s -o /dev/null -w "$p:%{http_code}\n" http://localhost:$p/; done`.
- Verify the served HTML carries the NEW brand: `curl -s http://localhost:PORT/ | grep -oiE 'newbrand|<title>[^<]*</title>'`.
- **A blank/black screen with a valid build has TWO common causes — check the console before blaming missing data:**
  1. **Missing API DATA** (shell renders, grids empty): content-fetch returns nothing without keys. Benign.
  2. **A crash at import/mount** (ENTIRE app blank, `document.getElementById('root').innerHTML` is empty): an optional service initialized with blank env THROWS during module load and takes the whole React tree down. The classic offender is **Firebase**: `initializeApp({apiKey: undefined})` throws `auth/invalid-api-key` the moment `firebase.js` is imported → black screen even though TMDB data is fine. **Diagnose by rendering headless and reading `browser` console logs + `root` innerHTML length** (0 = crash, not empty-data):
     ```python
     opts.set_capability('goog:loggingPrefs', {'browser':'ALL'})
     # after driver.get + sleep:
     driver.execute_script("return document.getElementById('root').innerHTML.length")  # 0 => crashed
     [e for e in driver.get_log('browser') if e['level']=='SEVERE']  # find the throw
     ```
  **Fix — make the optional service truly optional (don't just leave env blank):**
     - In `firebase.js`, only `initializeApp` when `apiKey` is present; otherwise export `auth=null, db=null, firebaseEnabled=false`.
     - Then GUARD EVERY call site: `onAuthStateChanged(auth, cb)` throws `Cannot read properties of null` if `auth` is null. Add `if (!auth) { setUser(null); /* mark ready */ return; }` before each of the (often 4-5) `onAuthStateChanged(auth, …)` useEffects across `WatchlistContext`, `Sidebar`, `ParentComponent`, `PersonalizedRow`, `ContinueWatchingRow`, etc. `grep -rl "onAuthStateChanged(auth"` to find them all.
     - Rebuild and re-probe: `root` innerHTML should jump from 0 to hundreds of KB and body text from 0 to thousands of chars.

## "Gada perubahan" — the Android stale-cache trap (verify SHIPPED before blaming code)

On this class of work (Termux + user viewing `localhost:PORT` in the Android browser), the single most common false alarm is the user reporting **"gada perubahan / logo ga berubah / penempatan ga berubah"** right after you edited + rebuilt. Nine times out of ten the code IS correct and the phone's Chrome/WebView is serving a **cached** older bundle. Do NOT start re-editing the code — first PROVE which side is stale:

1. **Confirm the server serves the new build** — the built JS/CSS filenames are content-hashed, so a new hash = new bundle: `curl -s http://localhost:PORT/ | grep -oE 'assets/index-[A-Za-z0-9_-]+\.(js|css)'` and confirm it matches the newest file in `dist/assets/`.
2. **Prove the change actually rendered** with a headless probe (incognito, no cache): load the page, then assert the specific thing exists — e.g. count `.brand-sheen` / `.logo-sweep` elements, read a wordmark's `getBoundingClientRect().top` to confirm placement, or grep the served CSS for the new keyframes. If the probe sees it, the code shipped and the problem is 100% the user's browser cache.
3. **Only then** tell the user it's cache and give the fix, in order of reliability: (a) **Chrome Incognito** tab → retype `http://localhost:PORT` (no cache at all — fastest proof); (b) Clear browsing data → "Cached images and files"; (c) close ALL localhost tabs, reopen fresh.
4. **Reduce future recurrence**: add cache-busting meta to `index.html` `<head>` so the HTML entry isn't cached (`<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">`, `Pragma: no-cache`, `Expires: 0`). Note this only stops FUTURE caching; the already-cached copy still needs one manual clear/incognito.

The lesson: when a user insists "nothing changed" after a verified build, the burden is to demonstrate the artifact is correct (hash + headless render) and redirect them to clear cache — not to keep re-editing correct code (that path burns turns and can regress working UI).

## Termux localhost URLs

Write every localhost URL as inline monospace (`http://localhost:4173`) so the user can copy-paste directly (a standing preference for this user). The app runs on the phone itself, so the user opens `http://localhost:PORT` in the Android browser — no tunnel needed for local preview.

## Step 7 — Strip upstream ads, trackers, and third-party badges

Forked repos (especially "free streaming" / content-aggregator templates) frequently ship **injected ad/adware scripts and legal badges baked into `index.html` and footer components**. Sweep them out — they're not yours, they pop ads/redirects, and they clutter the UI. Real examples seen: obfuscated ad loaders pointing at `knownfeel.com` / `untimely-hello.com` (popunder/redirect adware), a `DMCABadgeHelper.min.js` + DMCA badge `<a>`, a `dmca-site-verification` meta, and a "Data by TMDB" footer link.

```bash
# find the junk across the whole tree
grep -rniE "dmca|knownfeel|untimely|popads|propeller|_ad|adsbygoogle" index.html src/ 2>/dev/null
```
- Remove the obfuscated `<script>(function(options){...})({...knownfeel...})</script>` blocks and the `s.src="//untimely-hello.com/..."` injector from `index.html` — delete the whole `<script>…</script>`, not just the URL.
- Remove the DMCA badge `<a class="dmca-badge">…</a>` + its helper `<script>` from the footer component AND `index.html`, plus the `dmca-site-verification` meta.
- **Verify against the BUILT output**: `grep -c "dmca\|knownfeel\|untimely" dist/index.html dist/assets/*.js | grep -v ':0'` must return nothing.
- Google Analytics (`gtag`) is the upstream owner's tracking ID — either remove it or swap for the user's own; don't ship someone else's analytics.

## Attribution & license handling (when the user says "remove the credits")

- **Footer "Developed by X" / logo text**: free to change to the user's name in the UI. If the brand logo is split across tags for a two-tone effect (`Zero<span className="text-blue-500">Stream</span>`) and the user wants it one solid color, replace the whole split with plain text (`ZeroStream`) — `grep -rl 'Old<span'` finds every copy (footer, auth modal, verify/reset pages, movie/TV detail headers).
- **LICENSE (MIT/Apache)**: you MUST keep the original author's copyright line — MIT explicitly requires the notice be retained in all copies. Add the user's line ABOVE it (`Copyright (c) YEAR User (NewBrand)` then `Copyright (c) YEAR OrigAuthor (original)`), never delete the original. Deleting it is a license violation, not a rebrand.
- **TMDB attribution**: TMDB's ToS require crediting them if the app is PUBLICLY deployed. For private/localhost use it's fine to drop the "Data by TMDB" footer; if the user later deploys publicly, put a small TMDB credit back. State this tradeoff plainly when removing it.

## Step 8 — Optional: iOS-style UI polish (icons + nav) when the user asks for "kaya iOS"

A common follow-up after rebrand: "bikin UI-nya kaya iOS / perbagus icon-nya". Cheap, high-impact moves:
- **Swap the icon set to Ionicons (`react-icons/io5`)** — these ARE Apple's SF-style glyphs, so they instantly read as iOS. Replace Boxicons (`bi`) / FontAwesome (`fa`) nav+search icons: `BiHomeAlt→IoHome(Outline)`, `BiMoviePlay→IoFilm`, `BiTv→IoTv`, `BiSearch→IoSearch`, `BiBookmark→IoBookmark`, `FaUserCircle→IoPersonCircleOutline`, `FaSignOutAlt→IoLogOutOutline`.
- **Adaptive outline↔filled**: iOS tab bars show OUTLINE when inactive, SOLID/FILLED when active. Import both (`IoHomeOutline` + `IoHome`) and pick per state: `const Glyph = isActive ? ActiveIcon : Icon;`. Add a subtle scale-up + glow on the active one (`scale-110 drop-shadow-[0_0_8px_rgba(96,165,250,0.5)]`).
- **Floating frosted tab bar** instead of an edge-glued bar: wrap items in an inner `div` with `rounded-[26px] bg-[...]/80 backdrop-blur-2xl border border-white/10 shadow-[0_8px_32px_rgba(0,0,0,0.55)]`, give the outer `<nav>` side padding + `pointer-events-none` and the inner bar `pointer-events-auto`. Add `active:scale-90` on buttons for the tap-shrink feel.
- **Keep icon sets CONSISTENT across the app** — after swapping the nav, grep for stray `BiSearch`/`FaSearch` in search bars (SearchPage, TV episode selector) and swap those to `IoSearch` too, else the mix of icon families breaks the iOS look the user is after. `grep -rnE "BiSearch|FaSearch" src/`.
- **iOS layout convention — Search belongs TOP-RIGHT, not in the bottom tab bar.** This user asked to move Search out of the bottom nav up top. Remove the `search` item from the bottom-nav array, then add a floating top bar overlaying the hero: brand text left, a round frosted Search button right (`fixed top-0 … flex justify-between`, button = `w-10 h-10 rounded-full bg-white/10 backdrop-blur-xl border border-white/15 active:scale-90`, `onClick={() => navigate('/search')}`). Wrap the bar `pointer-events-none` and its children `pointer-events-auto` so it doesn't block hero taps. Matches the iPhone pattern (brand/back left, action icon right).\n- **This user's preferred nav split (iterated to): a single TOP bar, no bottom bar at all.** Left side = PRIMARY sections as TEXT labels (`Home  Movies  Series`), active one white+bold, inactive gray — not icons. Right side = ACTION items as EQUAL-SIZE round icons (`Search · Saved · Profile`, each `w-9 h-9 rounded-full`, same glyph size `text-[21px]`). Put the header in a `fixed top-0 … backdrop-blur-2xl bg-[bg]/85 border-b` bar, and add matching top padding to the page content wrapper (`pt-[calc(env(safe-area-inset-top)+3.25rem)] md:pt-0`) so content clears the fixed header. **Do NOT hide the header when the keyboard opens** — that was tried twice and reverted both times; see the "never hide the nav" pitfall in Step 9. Delete the old bottom `<nav>` entirely.\n- **FINAL iteration: fold BRANDING into that top bar as a TWO-ROW header** (\"kasih branding Zerostream Home Movies Series Pencarian saved profil, cukupin di atas, clean rapih\"). Row 1: brand lockup left (small `w-7 h-7` gradient play squircle + `brand-sheen` wordmark, tappable → home) and the action icons right (`Search·Saved·Profile`, `w-8 h-8`). Row 2: the text nav (`Home Movies Series`) with a `h-[2px] rounded-full bg-blue-500` underline under the active one (iOS-style active indicator). Bump the content wrapper top padding to clear TWO rows (`pt-[calc(env(safe-area-inset-top)+5.25rem)]`). This puts EVERYTHING the user listed in one clean top bar; keep it `backdrop-blur-2xl` frosted. The shimmer logo in the bar reuses the same `logo-sweep`/`brand-sheen` classes.\n- **ONE row — and the brand text comes BACK into it, as plain text.** The two-row header got reverted (`"hilangkan logo zerostream di atas / home Movies series ke atasin aja"`) to a single row: text nav (`Home Movies Series` + blue underline on active) LEFT, action icons RIGHT, no wordmark. **Then a few turns later the same user asked for the wordmark back: `"Zerostream Home Movies Series / tambahkan tulisan Zerostream tanpa effect apapun buat normal aja"`.** Endpoint = one row reading `Zerostream  Home  Movies  Series` on the left, actions on the right.
  - **What they kept deleting was the DECORATION, not the name.** The removals targeted the blue play-squircle glyph and the `brand-sheen` shimmer. Re-add the brand as a bare `<button className="text-white font-bold text-[15px]" onClick={() => handleNavigation('home')}>Zerostream</button>` — **no gradient, no `brand-sheen`, no `logo-sweep`, no icon**. `"tanpa effect apapun buat normal aja"` is literal; do NOT reach for the Step 10 shimmer classes here, that's exactly what they stripped.
  - **Adding a 4th item to a one-row header means retuning sibling sizes in the SAME edit.** Wordmark + 3 nav labels + icon overflows a phone header at the old `text-[15px]` nav size. Drop nav labels to `text-[13px]`, keep the wordmark at `15px` as the visual anchor, tighten the group `gap-5` → `gap-3.5`, add `shrink-0` to every child plus `min-w-0` on the group so nothing wraps or squeezes.
  - **Whenever you add or drop a header row, retune the content wrapper's top padding in the same edit** (`pt-[calc(env(safe-area-inset-top)+5.25rem)]` ↔ `+3.25rem`) or you ship an empty band / clipped content that reads as a layout bug.
  - **Do NOT predict that this user will delete all header branding.** An earlier version of this skill asserted "every branding element in a nav eventually gets deleted, don't invest in header chrome" — over-generalised from three consecutive removals, and this session contradicted it. The durable preference is narrower: **plain text yes, ornament no.** Build the minimal version; don't pre-emptively refuse the brand name.
- **Back button on browse/detail pages — OFFER but expect this user to strip it.** Initially added a back-to-home control to the Movies/Series sticky headers (`onClick={() => navigate('/')}`, `w-10 h-10 rounded-xl` frame, `BiArrowBack`), THEN the user said "tulisan series/movie dan icon < back di hapus" — remove BOTH the page `<h1>` title AND the back button, leaving the browse header nearly empty (just the "Browsing <genre>" chip when filtering + genre chips + sort). Consistent with this user's minimal-chrome instinct: browse pages don't need a title or back affordance because the top nav bar (Home/Movies/Series always visible) already handles navigation. Default to the leaner header for this user; when you remove the back button also drop its now-unused icon import (`BiArrowBack`) and the brand-icon tile (`BiMoviePlay`/`BiTv`) to avoid dead imports.

## Step 9 — Layout/scroll bug fixes that recur on this class of app

These three came up repeatedly on the same Vite+Tailwind mobile app; fix them proactively:

- **Android "black gap kepotong" at the bottom on overscroll** — when you scroll past the end, a blank dark band appears / the page looks cut off. Cause: `min-height: 100vh` (static vh doesn't track the mobile browser's collapsing URL bar) + default bounce overscroll exposing the page background. Fix in `index.css`: set `html, body, #root` background to the app's dark base color (e.g. `#05070d`, matching the theme, NOT pure black if the app uses a tinted dark), use `min-height: 100dvh` (dynamic viewport) alongside the `100vh` fallback, and add `html { overscroll-behavior-y: none; }`. Also remove leftover `pb-[calc(5rem+env(safe-area-inset-bottom))]` bottom padding from pages once the bottom nav is gone — it leaves an empty band.
- **Desktop sidebar menu "kepotong" when scrolled** — a `fixed` sidebar with `overflow-hidden` and a long genre list clips its own content. Fix: `overflow-hidden` → `overflow-x-hidden overflow-y-auto hide-scrollbar` and `h-full` → `h-screen max-h-screen` so the aside scrolls internally.
- **Top nav "tbtb ilang sendiri" / menu disappears (Home/Movies/Series/Profile all vanish) — THE FIX IS TO NEVER HIDE THE NAV AT ALL.** Two rounds of "smarter trigger" both failed on this user's device:
  1. Fork default: a `useEffect` on `window.visualViewport` `resize` sets `keyboardOpen=true` when `vv.height < innerHeight*0.85` and never resets it. On Android, scrolling collapses Chrome's URL bar → viewport shrinks → falsely read as "keyboard opened" → nav disappears **permanently**.
  2. The "correct" replacement — drive `keyboardOpen` purely off real `focus`/`blur` of `input`/`textarea` — **also failed, just with a different trigger**: the user tapped the search input, the keyboard opened, focus fired, and the `Zerostream`/Home button vanished. *"pada saat searching pa keyboard muncul kenapa homepage tombolnya hilang, perbaiki agar ttp ada"*. From the user's seat this is the same bug reported twice.
  
  **Resolution: delete the hide mechanism entirely** — the `keyboardOpen` state, the focus/blur effect, and the conditional `-translate-y-full` on the `<header>`. A permanently visible `fixed` top bar cannot get stuck hidden, and a ~48px bar costs nothing next to an on-screen keyboard. Verify structurally in the built bundle so it can't creep back: `cd dist/assets && for s in keyboardOpen translate-y-full visualViewport; do printf '%-18s ' "$s"; grep -o "$s" *.js | wc -l; done` → all `0`.
  
  **General lesson: relocating a bug's trigger is not fixing it.** Both attempts kept the premise "hide the nav sometimes" and only argued about when. When a user reports the same symptom after your fix, question the premise, not the condition.
- **Removing a page's redundant `<h1>` title** (e.g. "Search") — the user asked to drop the big "Search" heading shown above the search box (both the mobile `md:hidden` one and the `hidden md:block` desktop one). Delete the heading nodes but KEEP the sticky top-bar wrapper `<div>` (it provides the blur/safe-area spacing) — just empty it or self-close it.
- **Divergent per-page footers → hoist ONE `SiteFooter` into the layout route.** These forks hand-write the footer separately in each page, and the copies drift: the home footer (in `ParentComponent.jsx`, gated `{location.pathname === '/' && …}`) carried `Zerostream · Developed by <name> · © YEAR Zerostream | TMDB`, while `Movie/MovieDetails.jsx` and `TV/TvDetails.jsx` each had their own shorter variant (`bg-[#040507] border-t`) with **no "Developed by" credit**. The user notices: *"pas nonton film bagian bawah gada by Mftrferdinand, tambahkan sama seperti homepage"*. Don't patch the copies to match — extract `src/pages/Home/SiteFooter.jsx` and render it once inside the layout component, right after `<Outlet />`, unconditionally. Then delete the per-page `<footer>` blocks.
  - **Drop the `pathname === '/'` gate** when you hoist. It existed to keep the layout footer off pages that had their own; once there's one footer, every page should get it.
  - **Verify single-sourcing in the BUILT bundle, not the source** — the credit string must appear exactly once: `cd dist/assets && grep -o 'Developed by' *.js | wc -l` → `1`, and the old footer's distinctive class `grep -o 'bg-\[#040507\] border-t' *.js | wc -l` → `0`. Two hits means a duplicate survived.
  - **`grep -rn "<footer"` before starting** — the count tells you how many copies exist. Same near-duplicate trap as the two `VideoPlayer.jsx` / two detail pages: any chrome hand-written per page in these forks exists 3+ times.
  - Exercise the real route after the build, don't just curl `/`: pull a live id from TMDB and hit the actual watch path (`/movies/watch/x-<id>`, `/series/watch/x-1399`) so you've verified the page the user complained about.
  - Full procedure + duplicate-count table + verification commands: **`references/shared-chrome-extraction.md`**.

## Step 10 — Brand shimmer (HISTORICAL — this user deleted it; do not re-add)

**Read this warning before using anything below.** The shimmer was built, iterated on, and then explicitly killed: *"perbaiki di mode dekstop juga karena masih ada animasi nyala di Zerostream"*. The endpoint for this user is a **plain `font-bold` white wordmark, no animation, no gradient**. The recipe is kept only for a different project/user who asks for a moving-light logo.

**Removing an effect means deleting its CSS definition, not just its usages.** The `brand-sheen` class survived two "remove the branding" rounds because it lived in `index.css` while only the JSX call sites were edited — so it reappeared the moment a surface still referenced it (the desktop sidebar did). When the user says an effect is still visible, `grep -rn "<class-name>\|<keyframes-name>" src/` and delete BOTH the `@keyframes` blocks and the rule bodies. Verify against the built CSS *and* JS, all four names at once:
```bash
cd dist/assets && for s in brand-sheen brandSheen logo-sweep logoSweep; do printf '%-14s ' "$s"; grep -o "$s" *.js *.css 2>/dev/null | wc -l; done   # all must be 0
```

When the user wants the logo/wordmark to "hidup" with a moving light — and specifically says NOT a glow behind it, but the text/logo slightly DARKENED with a light that travels across — use a masked animated gradient, not a box-shadow. Define in `index.css`:

```css
/* wordmark: animate background-position of a gradient clipped to the text */
@keyframes brandSheen { 0%{background-position:-150% 0} 60%,100%{background-position:250% 0} }
.brand-sheen{
  background-image:linear-gradient(100deg,
    rgba(255,255,255,.72) 0%, rgba(255,255,255,.72) 38%,
    #fff 50%, #bfdbfe 56%, rgba(255,255,255,.72) 64%, rgba(255,255,255,.72) 100%);
  background-size:220% 100%; -webkit-background-clip:text; background-clip:text;
  color:transparent; animation:brandSheen 4.5s ease-in-out infinite;
}
/* logo squircle: a diagonal highlight strip that travels across, via ::after overlay */
@keyframes logoSweep { 0%{transform:translateX(-160%) skewX(-18deg);opacity:0} 45%{opacity:.9} 60%,100%{transform:translateX(160%) skewX(-18deg);opacity:0} }
.logo-sweep::after{ content:'';position:absolute;inset:0;
  background:linear-gradient(105deg,transparent 35%,rgba(255,255,255,.55) 50%,transparent 65%);
  animation:logoSweep 4.5s ease-in-out infinite;pointer-events:none; }
```
- The wordmark's base gradient sits at `.72` opacity (that's the "darkened slightly" the user wanted); the `#fff`/`#bfdbfe` band is the travelling highlight. Clip it to the text with `background-clip:text; color:transparent`.
- The logo tile needs `relative overflow-hidden` for the `::after` sweep to stay inside the squircle; add `brightness-90` to darken it a touch so the sweep reads.
- **Make it a reusable `BrandMark` component** (`size` sm/md/lg + `animated` bool) so the same lockup drops into the hero banner, the premium empty-state/profile pages, etc. Only pass `animated` on the homepage/hero to keep it "berlaku di home page aja" as this user scoped it.
- **Premium empty-state/profile**: put `<BrandMark size="lg" animated />` above the existing icon/copy, add `shadow-lg shadow-blue-900/20` to the icon tile — small touches make the signed-out Watchlist/profile read as premium and consistent with the nav logo.
- Verify the animation shipped: `grep -o "brand-sheen\|logo-sweep\|brandSheen\|100dvh" dist/assets/*.css` after build.

## Step 11 — The "de-sectioned homepage": strip labels + carousel, replace with uniform mixed rows

> **REVERSED LATER — read Step 11b before building this.** A subsequent session asked for titled
> sections, a Top 10, and a recommendation banner to come BACK: *"ubah homepage seperti awal aja pas
> pertama ada top trend top 10 dan lainya ada juga iklan Rekomedasi film di paling atas, tapi buat
> bisa di swap sendiri"*. Step 11 below is the intermediate state, not the endpoint. The durable
> preference is narrower than "no sections": **no AUTO-MOVING carousel** (that's what they called an
> "iklan"), and **no ornament** — titles and a hero are fine when the hero is swipe-manual.
> Build the Step 11b shape.

This user's homepage preference converged on the OPPOSITE of the typical fork template (which ships a hero carousel + 6-7 labelled rows like "Trending Movies", "Top 10 Movies This Week", "Now Playing in Theaters", "Asian Series", "Because you watched"). What they asked for, and the shape to default to:

- **No section titles at all.** "jangan ada tulisan trending film, top 10 film, series" → delete the `title` header block, the `accent` colour bar, the `See All` link, the `SectionDivider` label, and the rank numbers. Rows become bare poster strips.
- **No auto-moving hero / recommendation carousel.** "hapus iklan Rekomendasi film yang bergerak" — they read the auto-sliding `HeroBanner` (7s `setInterval`) and the "Because you watched" `PersonalizedRow` as *ads*, not features. Remove both from the homepage.
- **Uniform mixed Movie+Series rows.** "di tombol home campurkan aja semua film ada 10 baris, kesampingnya 20 film dan more tombol" → 10 rows, each 20 cards, each row mixing movies AND series, ending in a **More** tile.

Implementation that satisfied it — one new `MixedRow.jsx` component, driven by a flat genre table in `HomePage.jsx`. **A known-good, ready-to-copy version lives at `templates/MixedRow.js` in this skill** — start from it instead of retyping.

### Iteration 2 (what the user actually converged on) — read this BEFORE building the naive version

The flat genre table below is only the first draft. The user immediately pushed it to a **curated feed order + global dedup + one-constant spacing**. Build that shape from the start; the full recipe lives in **`references/homepage-mixed-feed.md`** and a known-good data layer in **`templates/homeFeed.js`**.

Three corrections, all of which will come up again:

1. **Curated row order beats a genre list.** The requested order: `1) movie trending global · 2) series trending global · 3) movie Indonesia · 4) series Indonesia · 5) anime & kartun · 6-10) random genre (shuffled per visit)`. Trending rows use `/trending/{type}/week`; Indonesia rows use `/discover/{type}?with_origin_country=ID` (**not** `with_original_language=id` — origin country returns the same top titles plus co-productions); anime/kartun is genre `16` on both movie and tv, interleaved.
2. **Dedup must be GLOBAL, not per-row.** "buat 10:20 film jangan ada yang sama di home page" = zero repeats across all 200 cards. Per-row `Set` dedup (the obvious implementation) still lets a title appear in row 1 *and* row 7. Centralise: fetch every row's candidates, then hand out cards **in row order** through one shared `seen` Set, so row 1 gets first pick and trending doesn't lose titles to a random row. Each row must over-fetch (2-3 TMDB pages) or it starves after dedup.
3. **One spacing constant for both axes.** The user's "tiap baris jaraknya sama kaya ke samping jangan terlalu lebar, presisikan" means vertical row gap must EQUAL horizontal card gap. The template ships `mb-12` (48px) vertical vs `gap-3` (12px) horizontal — visually way too airy. Export a single `GAP = 12` from `MixedRow.jsx` and drive `gap`, `paddingLeft/Right`, `marginBottom`, and the page wrapper's padding from it. Also normalise card width everywhere (132px) — `ContinueWatchingRow` shipped at 160px and broke the grid rhythm.

Two structural notes that make this maintainable:
- **Split data from presentation.** Global dedup is impossible if each row fetches its own data, so move all fetching into a `homeFeed.js` module (`buildRowSpecs()` + `loadHomeFeed(specs, 20)`) and make `MixedRow` purely presentational (`items` prop in, no `useEffect`). This is also what lets rows 6-10 be randomly shuffled per visit.
- **Skeleton must match the real grid** (same `GAP`, same card width, same `aspectRatio: '2/3'`) or the page visibly jumps when data lands.

### First-draft genre table (superseded by the curated order above, kept for the ID-pairing lesson)

```jsx
// HomePage.jsx — pair a movie genre id with the equivalent tv genre id per row
const ROWS = [
  { movie: 28,    tv: 10759 }, // Action / Action & Adventure
  { movie: 35,    tv: 35    }, // Comedy
  { movie: 18,    tv: 18    }, // Drama
  { movie: 878,   tv: 10765 }, // Sci-Fi / Sci-Fi & Fantasy
  { movie: 27,    tv: 9648  }, // Horror / Mystery
  { movie: 16,    tv: 16    }, // Animation
  { movie: 53,    tv: 80    }, // Thriller / Crime
  { movie: 10749, tv: 10766 }, // Romance / Soap
  { movie: 12,    tv: 10762 }, // Adventure / Kids
  { movie: 99,    tv: 99    }, // Documentary
];
```
- **Movie and TV genre IDs DIFFER on TMDB** — there is no `28` (Action) for tv; it's `10759` (Action & Adventure), and Sci-Fi is `878` for movies vs `10765` for tv. Read `src/pages/Home/tmdb.js` (these forks ship a static `GENRES = { movie: [...], tv: [...] }` map) and pair per row instead of reusing one id for both.
- **Interleave, don't concatenate** — `interleave(movies, tvs)` alternating one-by-one is what makes a row read as genuinely "campur"; `[...mv, ...tv]` just puts all movies first and looks unmixed.
- Fetch each side with `/discover/{type}` + `sort_by=popularity.desc&include_adult=false&vote_count.gte=80&with_genres=<id>`, filter `poster_path`, tag each item `media_type` (discover does NOT return `media_type`, unlike `/trending`), dedupe on `` `${media_type}-${id}` ``, `slice(0, 20)`.
- **The "More" tile is the last flex child of the same scroll row** (not a header link): `style={{ width: 132, aspectRatio: '2 / 3' }}` so it matches poster shape, arrow-in-circle + `More` label, `onMore()` → `navigate(buildBrowsePath('movie', row.movie))`. Pass `onMore` as a **function prop**, not a path string — the parent owns `navigate` + `window.scrollTo`.
- Reuse the existing drag-to-scroll refs pattern (`dragStateRef` / `suppressClickRef` / `mouseup` listener) verbatim from `TrendingRow.jsx`; without `suppressClickRef` a drag ends in an accidental card navigation.
- Narrower cards (`width: 132` vs the template's 160/185) fit more posters per screen on the phone — consistent with this user's "font/elemen kecil" mobile preference.
- **Drop the hero's top padding compensation**: with `HeroBanner` gone the page starts at the first row, so change the wrapper from `pt-10` to `pt-2` or the content sits under a big empty gap.
- **Verify labels are actually gone in the BUILT bundle**, not just the source: `cd dist/assets && for s in "Trending Movies" "Top 10" "Trending Series" "Asian Series" "Now Playing" "Because you watched"; do printf "%-22s " "$s"; grep -c "$s" *.js; done` — every count must be `0`.
- **Probe each genre pair against TMDB before shipping** so no row silently renders empty: loop the `ROWS` table through `/discover` and assert `len(results) == 20`. Cheap, and catches a bad id immediately.
- Leave the now-orphaned `TrendingRow.jsx` / `PersonalizedRow.jsx` / `HeroBanner.jsx` files in place but **tell the user they're unused and ASK before deleting** — this user iterates on layout and may want them back; unused modules don't enter the Vite bundle anyway. (In practice they never came back: two turns later they were swept up in the auth/watchlist deletion of Step 12. So flag them, don't block on an answer — fold them into the next structural delete.)

## Step 11b — The endpoint: hero + Top 10 + TITLED rows, and collapse to TWO pages

Where this user actually landed, after Step 11's title-less strip proved too undifferentiated
(*"perbaiki tampilan homepage nya agar tertata rapih"* = the unlabelled rows read as random). Full
recipe, row table, and the swipe-carousel component in
**`references/homepage-hero-top10-titled-rows.md`**.

Shape to build:

- **Hero "Recommended" at the top, SWIPE-MANUAL — no `setInterval`.** This is the whole distinction
  between a feature and the "iklan" they deleted in Step 11: content that moves on its own is
  chrome; content they move themselves is navigation. Implement with CSS
  `snap-x snap-mandatory` + `overflow-x-auto` (native-feeling on Android, free momentum) plus the
  usual mouse-drag refs for desktop. **Derive the active dot from `scrollLeft / clientWidth` in an
  `onScroll` handler, never from a timer** — a timer-tracked index desyncs the moment the user swipes.
- **Titled sections.** `Top 10 Trending Today` (numbered cards) → `In Theaters Now` →
  `Popular in Indonesia` → `Anime & Animation` → `Highest Rated of All Time` → `Trending Series` →
  ~6 shuffled genres. Pass `title` into the row component and render an `h2` at
  `text-[15px] font-bold`, left-padded by the same `GAP` constant so it aligns with the first card.
- **Top 10 rank numbers: DON'T BUILD THEM for this user — the title alone carries the ranking.**
  Shipped behind the card (invisible) → corrected to solid white in front → then deleted outright one
  turn later: *"angka 1234567 di top hapus aja"*. Two turns spent on an element that ended at zero.
  Render the Top 10 row **identically to every other row** (same `GAP`, no per-item margin, no
  `variant` prop); the `Top 10 Trending Today` heading plus 10 items in rank order is enough. If a
  different user does want numerals, the working recipe (`z-30`, `WebkitTextStroke` + `paintOrder:
  'stroke fill'`, `GAP + 10`, `marginLeft: 26`) is preserved in
  `references/homepage-hero-top10-titled-rows.md` — but note that an outline-only digit at `z-0`
  behind an opaque 2:3 poster is simply invisible, never "subtle".
- **Hero height comes from `svh`, not `aspectRatio` and not `vh`.** `min(72svh, 560px)`, shared as one
  constant with the skeleton. An aspect ratio on a full-bleed hero collapses toward square at phone
  width (*"terlihat hanya 1:1"*); `vh` on Android measures the URL-bar-collapsed viewport and pushes
  the title/CTA off screen.
- **Hero is EXEMPT from global dedup.** Keep the shared `seen` Set for every other row, but let hero
  titles reappear in Top 10 — otherwise Top 10 silently isn't the top 10. Implement as an
  early-return on `spec.kind === 'hero'` inside `loadHomeFeed`.
- **Drop the page-entry `framer-motion` fade.** An `initial/animate` wrapper on the whole home tree
  stutters on Android while first-row images decode. Plain `<div>`.

### Collapse to Home + Search only

Same session: *"perbaiki ui tombol pencarian jadi ringan tanpa ada filter film ini itu karena banyak
bug home jd hilang, buat ringan hanya ada homepage sama search yg ringan"*. The browse pages
(`Movie.jsx` / `Series.jsx` + `ContentGrid.jsx` + `urlFilters.js` + `tmdb.js`) and the faceted search
panel are the bug farm — genre chips, sort slugs, URL-encoded filter state, infinite scroll. Delete
the class of feature, don't debug it.

- **Nav becomes one word.** `Zerostream` (→ home) left, search icon right. Removing the last two nav
  buttons means the header is a single `<button>`, not a `.map()` over an array — collapse the loop
  too or you leave a one-element array that reads as unfinished.
- **Search rewrite: one input, `/search/multi`, debounce 400ms, `Load more` button.** No media-type
  filter, no genre chips, no sort. `multi` returns movies and series together (drop `person` hits).
  Keep `?q=` in the URL so results are shareable/refreshable. A `Load more` button beats infinite
  scroll on a low-end Android — no scroll listener, no layout thrash.
- **The "home jd hilang" bug was NOT in search** — it was the `visualViewport` keyboard heuristic in
  the header (see Step 9). Fix the real cause; deleting the filters alone would have left it. When a
  user attributes a bug to the screen where they *noticed* it, check the shared shell first.
- **Redirect, don't 404.** `<Route path="*" element={<Navigate to="/" replace />} />` catches the
  removed `/movies` and `/series` so shared links keep working.
- **Sweep the orphans in the same pass**, deepest-first: page components → the grid they used →
  the URL/filter helpers → now-unused `Fetcher.js` exports (`fetchContentByGenre`, `fetchTrending`)
  → `tmdb.js` (`GENRES`/`SPECIAL_CATEGORIES`/`SPECIAL_PARAMS`) → `react-query` from `main.jsx` once
  the rewritten Search no longer uses `useInfiniteQuery`. Then
  `grep -rn "<deleted-symbol>" --include=*.jsx --include=*.js src/` until clean.
- **Fix the `state: { from: … }` breadcrumbs.** Detail pages navigate back to `'/movies'` / `'/series'`;
  after the delete those are redirects. Point them at `'/'`.
- Quote the bundle drop as proof (455KB → 378KB, 137KB → 118KB gzip here).

## Removing the logo mark entirely (keep the wordmark)

"hapus icon logo biru video" = delete the blue play-squircle glyph from EVERY surface, leaving only the `Zerostream` wordmark. There are typically THREE hardcoded copies plus the component: mobile top bar in `ParentComponent.jsx`, desktop `Sidebar.jsx` logo button, and `BrandMark.jsx`. `grep -rn "logo-sweep\|from-sky-400 via-blue-500"` finds them all — after the sweep only the `.logo-sweep::after` CSS rule in `index.css` should remain (harmless, keep it for future use).
- Remove the now-unused `FaPlay` import from each file, and strip the `box`/`icon` size keys from `BrandMark`'s `dims` table so the component doesn't carry dead config.
- `npx eslint <files>` right after — the only errors left should be PRE-EXISTING unused vars (e.g. `IoPersonCircle`, `action`); don't "fix" unrelated ones unasked.



When the user sends "ini logo-nya, pakai ini" and the image looks NEARLY BLANK (white/pale shapes on white), DO NOT conclude it's an empty placeholder and move on — that's the exact mistake that made this user repeat themselves several times ("udh berapa kali gua kirim gambar ini"). A transparent PNG whose artwork is near-white simply won't read on a white viewer. Reveal it first, then recreate:

1. **Composite over a dark background to SEE the shape** before judging it:
   ```python
   from PIL import Image
   im = Image.open(SRC).convert('RGBA')
   bg = Image.new('RGBA', im.size, (10,12,18,255))
   Image.alpha_composite(bg, im).convert('RGB').save('/tmp/logo_on_dark.png')
   ```
   Also check opacity/avg color of non-transparent pixels to confirm it's real artwork, not a blank alpha layer. THEN run vision on the composited version and ask specifically for the geometry (how many shapes, symmetry, is there a play/triangle, gradient direction) — enough detail to rebuild it.
2. **Recreate as an inline SVG React component** (not a raster) so it stays crisp and can carry the brand gradient + shimmer. Exploit symmetry: model one half, then `<g transform="rotate(180 cx cy)">` the rest. Give the `<linearGradient>` a unique `id` per mount (pass a `gradientId` prop) so multiple instances on one page don't collide.
3. Swap the placeholder icon (often a generic `FaPlay` in a squircle) for `<Logo/>` everywhere the mark appears — hero, sidebar, favicon `public/<brand>.svg` — and keep the shimmer wrappers (`logo-sweep`, `brand-sheen`).
4. Tell the user the SVG is eyeballed and offer to fine-tune curves OR accept their original SVG file if they have one (100% accurate beats re-derived). Recreated Béziers are approximate; say so.

The lesson: a "blank" logo image from the user is almost never blank — it's low-contrast. Composite-over-dark is the 10-second check that prevents ignoring a real asset across multiple turns.

## Logo iteration whipsaw — don't over-invest in a recreated custom mark

Branding is the most-iterated area for this user; expect to redo the logo several times in one session and DON'T treat any version as final. Real sequence seen: (1) generic `FaPlay` squircle → (2) recreated the user's custom "Z pinwheel" as a bespoke SVG (heavy effort, eyeballed Béziers) → (3) user said "ganti pake ikon video biru cuma UI-nya dipercantik" i.e. THROW AWAY the custom SVG, go back to the simple play-triangle-in-blue-squircle, just make it prettier → (4) "hapus aja logo di homepage" entirely. Takeaways:
- When the user says "percantik" of a simple icon, they want the SAME glyph refined, not a new bespoke mark. Refine cheaply: 3-stop gradient (`from-sky-400 via-blue-500 to-blue-700`), a top inner-highlight strip (`absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-white/25 to-transparent`) for gloss, `ring-1 ring-white/20`, blue shadow, and a small drop-shadow on the glyph. That reads "premium" without a redesign.
- Keep the logo in a **reusable `BrandMark` component** so a swap is one file; a bespoke `ZLogo.jsx` you build may get deleted next turn — don't scatter it across many files before the user confirms they want it.
- "hapus logo di homepage" means remove the brandmark instance from the hero/homepage ONLY — leave it in the sidebar/favicon. Delete the now-unused import to avoid dead-code.

## Font choice to match a rounded/geometric logo

When the user wants fonts to "cocok sama logo" and the logo is a rounded squircle, a geometric-rounded sans like **Outfit** (already linked via Google Fonts in many of these templates) fits. Set it as the global `font-family` on `html, body` in `index.css` (`'Outfit', system-ui, -apple-system, sans-serif`) so the whole app inherits it, rather than per-component.

## Third-party embed players: subtitles & ad-blocking are OUT of your control (be honest)

Forked "free streaming" templates almost never host video themselves — the player is an `<iframe>` pointing at a third-party embed host (VidNest, VidLink, VidSrc, MultiEmbed, etc.), often selectable as "Server 1/2/3". When the user asks for **Indonesian/anime subtitles** or **"iklan 100% dihilangkan"**, do NOT promise either — the video, its subtitle tracks, AND its ads all live inside a CROSS-ORIGIN iframe the browser forbids you from reading or mutating (same-origin policy). Set expectations straight instead of faking it:

- **Subtitles**: come from whatever the embed host ships per title; you cannot inject your own `.srt`/`.vtt` into their iframe. A real subtitle feature requires OWNING the video source (your own backend + subtitle files) — a separate, large project, not a rebrand tweak. Say so plainly.
- **Ads**: you cannot remove ads rendered inside the third-party player (no DOM access across origins). What you CAN do and should offer:
  1. **`sandbox` the iframe** to kill the worst offenders — popups / redirects / new-tab hijacks — which are the ads that fire on tap: `sandbox="allow-scripts allow-same-origin allow-fullscreen allow-presentation"` (omit `allow-popups` and `allow-top-navigation` so the embed can't spawn popunders or redirect the top window). This cuts popup/redirect ads dramatically but does NOT remove in-player banner/overlay ads.
  2. **Reorder `SOURCES`** to put the cleanest embed host first.

**CRITICAL — `sandbox` on these embed hosts is a DEAD END. Expect to remove it entirely; budget one turn, not three.**

> **RELAPSE WARNING — THIS HAS NOW HAPPENED THREE TIMES.** Three separate sessions re-introduced the
> per-source `sandboxed: true|false` flag — the exact pattern this section documents as refuted —
> each time because the user asked for the uBlock Origin notice and adding "real" popup blocking
> alongside it felt like the more complete answer. Every time the user came back with the same
> sentence: *"Please disable sandbox mulu berarti gabisa ya, udh lah disable aja"*. The second
> relapse even shipped a shield icon on the "protected" servers, advertising a benefit that did not
> work; the third re-added the same flag with per-server comments explaining which hosts "tolerate"
> it. **THE ANSWER IS NO SANDBOX. There is no per-host, per-browser, or shield-icon variant of
> this idea that survives contact with the user's device.** If you are about to add a
> `sandbox` attribute to these iframes, stop. The ask "still seeing ads" is answered by the honest
> explanation + the uBlock notice, NOT by resurrecting sandbox. Adding it risks regressing playback
> ("Please Disable Sandbox") on a host the user was previously watching fine, which is worse than
> the ads. If you genuinely believe the environment changed, verify playback in a real browser on
> the user's device BEFORE reporting popup blocking as a benefit.
>
> **Why it keeps recurring, so you can catch yourself:** the trigger is always a request about ads.
> Explaining "cross-origin, I can't touch the iframe" feels like an incomplete answer, so the
> sandbox partial-mitigation gets reached for as a consolation prize. Resist it. The honest
> limitation IS the complete answer here, and the user has accepted it three times running once
> stated plainly.

Embed hosts detect the sandbox attribute and render only the text **"Please Disable Sandbox"** instead of the video. Observed escalation across one session, in this order:

1. Blanket `sandbox={SANDBOX}` on all four servers → VidNest (`vidnest.fun`) and MultiEmbed (`multiembed.mov`) both show "Please Disable Sandbox". User: *"server 3 gak jalan / cuma tulisan sandbox"*.
2. "Fixed" it with a **per-source `sandboxed: true|false` flag**, keeping VidLink + VidSrc sandboxed. Looked correct, built clean.
3. User came back: *"yang pertama gabisa open video karena di suruh please disable sandbox"* — **Server 1 (VidLink) refused too**, on the same Android Chrome/WebView. The per-source flag bought nothing.

**Resolution that actually worked: delete the `sandbox` attribute and the `SANDBOX` constant outright** from both `Movie/VideoPlayer.jsx` and `TV/VideoPlayer.jsx`, and lean on uBlock Origin as the only ad defence.

- **Do NOT reach for the per-source-flag pattern.** It reads like the careful engineering answer, adds a config axis, and still leaves the user hitting the same wall on a host you marked safe. Whether a host refuses varies by browser/WebView and changes over time, so any hardcoded allowlist rots. Skip straight to no-sandbox.
- **Say the tradeoff out loud when you remove it**, unprompted: sandbox was the thing blocking popups and forced redirects; without it those ads can fire, and the uBlock Origin notice is now the *only* defence. Removing a protection silently is worse than the ads.
- Rewrite the helper caption to match reality — one honest line (`"Pasang uBlock Origin buat blokir iklan. Kalau video gagal tampil, coba ganti Source di atas."`), never a stale "popup diblokir otomatis" claim.
- **Verify it's gone from the BUILT bundle, and read the surviving hits before declaring clean**: `grep -o '.\{60\}sandbox.\{60\}' dist/assets/*.js`. Expect exactly one match from the **Firebase SDK** (`staging-identitytoolkit.sandbox.googleapis.com`) — a raw `grep -c` returns `1` and looks like a failure. Inspect the context, don't trust the count.
- Both player components are near-duplicates; fixing only `Movie/` leaves Series broken.
- Honest framing for the user: popup/redirect ads can only be *fully* stopped by owning the stream. With a third-party iframe, uBlock Origin is the realistic ceiling — say that instead of shipping a fake "ad-free" claim.

## Step 12 — When the user says "hapus login/profil/watchlist, nonton tanpa akun"

The natural endpoint of Step 6's "make Firebase optional". Guarding `auth === null` keeps the app *alive* with blank env, but leaves dead UI (Sign In buttons, an empty Watchlist page) and ~350KB of unused SDK in the bundle. When the user says **hapus aja**, delete the subsystem rather than guarding it — and expect this ask on a streaming fork, because the accounts are upstream's product decision, not the user's.

Full recipe — file-by-file delete list, call-site edits, verification commands — in **`references/removing-auth-subsystem.md`**. Headlines:

- **Order matters: leaf pages → context/provider → call sites → deps → env.** Delete the provider first and every consumer throws, so you lose the ability to build-check intermediate states.
- **Snapshot to a tarball first** (`tar czf ~/<proj>-backup-$(date +%Y%m%d-%H%M).tar.gz --exclude=node_modules --exclude=dist <proj>`). ~4MB, and it doubles as the ESLint baseline below.
- **Add a catch-all route** when you remove routes: `<Route path="*" element={<Navigate to="/" replace />} />`. Otherwise an old `/watchlist` or `/login` bookmark renders blank inside the layout.
- **`npm uninstall firebase pocketbase`** and quote the bundle drop (800KB → 453KB / 139KB gzip) — cleanest proof the subsystem is actually gone. Forks often carry a second vestigial SDK (`pocketbase`) imported nowhere.
- **Scrub `VITE_FIREBASE_*` from `.env` AND `.env.example`** — mandatory before any public push (this user's standing rule: public releases must not leak personal data), and the example must stop instructing people to configure a subsystem that no longer exists.
- **`Movie/` and `TV/` detail pages are near-duplicates** — fixing one leaves the other broken. Same for the two `VideoPlayer.jsx`.
- **Report the collateral loss unprompted**: Continue Watching dies with it (account-scoped Firestore). Offer the `localStorage` per-device replacement in the same breath instead of letting the user discover the gap.

### ESLint on a repo with pre-existing lint debt — baseline, don't chase zero

These forks ship dozens of pre-existing violations (`react/prop-types`, unused `React` imports, `no-unescaped-entities`). After a wide delete, `npx eslint src` printed **49 errors** — looks like you broke something, and tempts an unasked-for cleanup spree.

Diff against the pre-change tarball instead of reading the absolute count:
```bash
mkdir -p "$PREFIX/tmp/zsbak" && tar xzf ~/<proj>-backup-*.tar.gz -C "$PREFIX/tmp/zsbak"
cd "$PREFIX/tmp/zsbak/<proj>" && ln -sfn ~/<proj>/node_modules node_modules
npx eslint src 2>&1 | tail -3      # → 87 before vs 49 after ⇒ nothing new introduced
```
Then lint **only the files you touched** and drive *those* to clean. Two Termux/flat-config gotchas: `/tmp` does not exist (use `$PREFIX/tmp`), and `npx eslint --ext .js,.jsx` is rejected under `eslint.config.js` — pass paths directly.

### Restart preview cleanly so the URL is deterministic

Stale `vite preview` processes stack up across a long session, and each new one drifts a port (`4173 → 4174 → 4175`), so the user gets a different URL every turn and reasonably assumes something is broken. Before restarting: `process(action='list')`, kill each preview session, `pkill -f "vite preview"`, then start exactly one. Confirm the served bundle hash matches the newest file in `dist/assets/` before handing over the URL.

## Step 13 — "Bikin desktop kaya Android aja" — collapse to ONE layout

These forks ship two layouts: a `md:` desktop sidebar plus a `md:hidden` mobile top bar, with
`md:`-prefixed compensation scattered across every page. This user only ever views on Android, so the
desktop branch is unseen code that drifts — it's where the deleted `brand-sheen` shimmer survived
("masih ada animasi nyala di Zerostream" was the *desktop sidebar*, already removed from mobile).

Default: **delete the desktop branch entirely.** Full procedure, edit table, and the two traps
(deleting the sidebar also deletes genre filtering; sticky sub-headers then collide with the fixed
bar) in **`references/desktop-mobile-layout-unification.md`**.

- **Before deleting a breakpoint-specific component, list what it uniquely provides.** The sidebar
  was the only genre picker; the fix is unhiding the mobile genre chips (`md:hidden` → nothing) in
  the same edit, not adding a new UI.
- **Effects hide in the branch you don't look at.** A "remove this animation" ask needs a repo-wide
  `grep` for the class AND deletion of its `@keyframes`/rule in `index.css` — see the Step 10 warning.
- Expect the bundle to shrink; quote it as proof the layout actually left.

## Supporting references

- `references/homepage-mixed-feed.md` — curated row order, global dedup, one-constant spacing for the de-sectioned homepage.
- `references/homepage-hero-top10-titled-rows.md` — the ENDPOINT homepage: swipe-manual hero, Top 10 outline numerals, titled rows, and collapsing the app to Home + Search.
- `references/removing-auth-subsystem.md` — file-by-file delete list for ripping out login/profile/watchlist.
- `references/shared-chrome-extraction.md` — single-sourcing duplicated footers/players/lockups that these forks hand-write per page, plus this user's 3-line left-aligned footer layout.
- `references/desktop-mobile-layout-unification.md` — deleting the desktop sidebar branch so one mobile-first top bar serves all breakpoints.
- `templates/MixedRow.js`, `templates/homeFeed.js` — known-good starting points for the homepage feed.

## Rewrite the README + .env.example

Replace upstream README with a short one in the user's language: what it is, prerequisites, `cp .env.example .env` + which key is mandatory, `npm run dev`, and a note that it's a rebrand of the upstream project (credit the license). Don't leave the old repo's clone URL / project name in the README.

## Pitfalls

- **Build the minimum the words ask for; let them ask for the increment.** This session's two biggest
  time sinks were both elements the user never requested: Top 10 rank numerals (invented because "Top
  10" seemed to imply ranks — corrected once, then deleted) and iframe `sandbox` (invented as a bonus
  alongside the uBlock notice — deleted for the third time across sessions). Both were *reasonable
  inferences*. Neither was asked for. On this project an unrequested addition is not a bonus, it's a
  correction round.
- **"Ornament is out" does NOT mean "make it subtle" — it means remove it or make it clearly legible.** Twice this session a restrained choice was rejected for being *invisible*, not for being ornamental: the Top 10 rank numeral hidden behind an opaque poster, and a hero sized by aspect ratio that collapsed to a square. This user strips decoration but wants *information* bold and unmissable. When an element carries meaning (a rank, a hero title, a CTA), go bigger and higher-contrast; save the restraint for things that carry none. **But note the numeral's fate: made legible, then deleted anyway** — legibility was necessary, not sufficient. Redundant information (a "3" on the third card of a row titled "Top 10") is still ornament to this user.
- **Relocating a bug's trigger is not fixing it.** The vanishing nav was "fixed" twice — first by replacing the `visualViewport` heuristic with focus/blur, which still hid the header and drew the identical complaint one turn later. Both attempts preserved the premise "hide the nav sometimes" and only argued about when. When the user reports the same symptom after your fix, delete the feature, don't retune the condition.
- **A deletion tells you the FORM is wrong, not that the CONTENT is unwanted.** This user deleted the auto-sliding recommendation hero as an "iklan", and two rounds later asked for it back — *"ada juga iklan Rekomedasi film di paling atas, tapi buat bisa di swap sendiri"*. Same for section titles: stripped once, then requested back because unlabelled rows read as random. The durable rule is narrow: **self-moving UI and ornament are out; the same content under user control is welcome.** When they kill a moving element, don't generalise to "they hate this feature" — offer the manual variant. (The Step 10 shimmer over-generalisation was the same mistake in the other direction.)
- **When a user blames a bug on the screen where they noticed it, check the shared shell first.** *"tombol pencarian ... banyak bug home jd hilang"* pointed at Search, but the vanishing nav was the `visualViewport` keyboard heuristic in the layout component (Step 9). Rewriting only the named screen would have shipped with the bug intact. Reproduce against the shell before accepting the user's localisation of a fault.
- **Brand CASING is exact — confirm and match it literally.** This user corrected `ZeroStream` → `Zerostream` (lowercase 's'). Once the user states the exact casing, sweep it case-sensitively everywhere (`grep -rn "ZeroStream"` must return 0) and rebuild. Don't invent a camelCase/two-tone styling the user didn't ask for; when they say "putih semua" (all one color) or give an exact spelling, honor it verbatim across footer, auth modal, verify/reset pages, and every detail header.
- **Strip decorative status badges/labels wherever this user points — it's a recurring, expanding preference.** They dislike ornamental chrome (same instinct that removed DMCA/emoji/promo headers elsewhere). This session removed FOUR such elements across iterations, each a separate ask; expect more and act on each:
  1. **Browse/section sticky headers** (Movies/Series): a `Trending` pill + `this week` caption when no genre is active → collapse the `{genre ? (…Browsing badge…) : (…Trending/this week…)}` ternary to `{genre && (…Browsing badge…)}` so the fallback promo badge disappears but the useful "Browsing <genre>" chip stays.
  2. **Hero banner (top of home)**: the "Live badge + type label" block (`Trending` pill + `Movie`/`Series` type label above the title) → delete the whole `<div className="flex items-center gap-3 mb-4">…</div>` so the hero opens straight on the title. Leave rating/year/genre/Play below intact.
  3. **Desktop sidebar logo subtitle**: a small `Streaming` tagline under the `Zerostream` wordmark (`<span … tracking-[0.22em] uppercase>Streaming</span>`) → delete just that span, keep the brand name.
  4. **Whole labelled sections + the auto-sliding hero carousel** — see Step 11. This user calls a moving recommendation carousel an "iklan" ("hapus iklan Rekomendasi film yang bergerak"). Anything that animates on its own and pushes content at them is chrome to be deleted, not a feature to defend.
  5. **The logo glyph itself** ("hapus icon logo biru video") — even the brand mark is fair game; wordmark-only is the endpoint for this user.
  When in doubt, prefer removing decorative labels over keeping them for this user. They send a cropped screenshot of the exact element and say "hapus tulisan X" — match the visible text to its JSX and remove that node only.
- **Footer attribution nuance: TMDB credit as plain text is fine and often wanted.** After earlier removing the "Data by TMDB" link, this user later asked to fold TMDB back into the copyright line as `© YEAR Zerostream | TMDB` (plain text, not a link/badge). So "remove TMDB" earlier didn't mean "never mention TMDB" — it meant remove the clutter/link. A compact text credit in the copyright line satisfies both the clean-UI preference and TMDB attribution. Apply the same string across home footer + movie/TV detail footers (`grep -rn \"© {new Date\"`).
- **User-facing terminology renames are separate from the brand sweep.** User asked "Series itu tv, ubah namanya setiap tv jadi series" — i.e. rename the *domain label* `TV`/`TV Shows`/`TV Show` → `Series` in all VISIBLE text (nav labels, page `<h1>`, hero type badge, section dividers, card meta, search filter labels, sidebar) WITHOUT touching the code-level `type="tv"` / `mediaType === 'tv'` API values or route paths (`/series` route already maps to tv). Grep visible strings (`grep -rnE "TV Show|>TV<|'TV'"`), swap only the display copy, leave the tmdb `tv` identifiers intact.
- **Case-insensitive global replace corrupts casing** — do specific→generic case-sensitive replaces, not one `re.sub(flags=I)`.
- **Don't defend a "careful" abstraction the user's environment already refuted.** The per-source `sandboxed` flag (see the embed-player section) was the textbook-correct fix after one host refused — and it still failed on the next host, in the user's browser, on the very next turn. When a third-party surface rejects a technique once, treat the technique as unavailable rather than building a config axis to route around it host-by-host. Blast-radius rule of thumb: if the workaround needs a hardcoded allowlist of external services, it will rot.
- **When you remove a protection to make something work, say so unprompted.** Deleting `sandbox` fixed playback but re-opened popup/redirect ads. State the tradeoff in the same breath as the fix; a silent security/UX downgrade is worse than the original bug.
- **`grep -c` on a built bundle lies — read the surrounding context.** Checking `sandbox` was gone returned `1`, which looks like a leftover; `grep -o '.\{60\}sandbox.\{60\}'` showed it was the Firebase SDK's `identitytoolkit.sandbox.googleapis.com`. Always widen to context before reporting either "clean" or "still there".
- **Global uniqueness needs a central data layer, not per-component Sets.** "jangan ada yang sama di home page" cannot be satisfied while each row fetches independently — each row's local dedup is blind to its siblings. Hoist fetching into one module and distribute results through a single shared `seen` Set (see Step 11 / `references/homepage-mixed-feed.md`).
- **Spacing complaints ("presisikan", "jangan terlalu lebar") mean derive both axes from one constant.** Don't hand-tune `mb-*` per section — export a single `GAP` and use it for horizontal gap, side padding, and vertical row margin. Then sweep sibling rows for stray widths (`ContinueWatchingRow` was 160px against everything else at 132px).
- **Missing the non-`src/` brand strings** (`.firebaserc`, robots.txt, sitemap.xml, manifest, package.json name) — grep the whole tree and loop until leftover-grep is empty.
- **Trusting a source-CSS grep for the recolor** — Tailwind purges/composes; verify against `dist/assets/*.css` after build.
- **Wrong TMDB key tier** — v3 (`?api_key=`) vs v4 (Bearer JWT) are not interchangeable; match the app's calling convention.
- **Signing off on "it works" from HTTP 200 alone** — 200 means the shell served; confirm real content by hitting the actual data endpoints with the user's key. On an SPA with a catch-all redirect route, **every** path returns 200 (the dev server falls back to `index.html` and the redirect happens client-side) — so a 200 on `/watchlist` after you deleted it is expected, not a sign the page survived.
- **Comment strings survive a subsystem delete and read as leftovers next session.** After ripping out watchlist code, `grep -rn "watchlist" src/` still matched `{/* Hover overlay — Play + Watchlist */}`. Reword stale comments as part of the delete, or the next agent's cleanliness grep reports a false positive.
- **Registering on the upstream service for the user** — captcha-gated signups (TMDB uses AWS WAF; others use Cloudflare Turnstile) are a human-in-5-minutes job. Solving the captcha token (even via 2Captcha) doesn't always clear WAF challenges that fingerprint the browser session. Ask the user to sign up + paste the key rather than burning turns/solver balance; then you do the wiring.
- **Duplicated chrome is the default failure mode of these forks — assume 3+ copies of anything visual.** Confirmed instances across one project: the footer (3×), `VideoPlayer.jsx` (Movie + TV), the detail pages (near-identical `MovieDetails`/`TvDetails`), the brand lockup (mobile header + sidebar + `BrandMark`), and the `© YEAR` string. Before editing any visible element, `grep -rn` its distinctive text/class to get the copy count, then either fix every copy in one pass or extract a shared component. Fixing one and reporting done is how "gada di halaman nonton" complaints happen.
- **A stale watch-pattern notification can arrive AFTER you killed the process.** Background `vite preview` sessions with `watch_patterns` can deliver a `Local: http://localhost:4175/` alert for a process already terminated, making it look like a rogue server drifted the port. Don't restart or re-kill on the strength of the notification — probe the ports first (`for p in 4173 4174 4175; do curl -s -o /dev/null -m 3 -w "$p:%{http_code}\n" http://localhost:$p/; done`; `000` = dead) and tell the user plainly that the message was a late echo from a killed process.
- **Establish the lint baseline the FIRST time you touch a repo, not after a scary count.** This project has no `.git` (Step 1 strips history), so `git show HEAD:file` and `git stash` are unavailable for before/after comparison — the pre-change tarball is the only baseline. Extract a single file from it and lint that to prove an error set is pre-existing: `tar xzf ~/<proj>-backup-*.tar.gz -C "$PREFIX/tmp/chk" <proj>/src/path/File.jsx`. **Copy it INTO the project tree before linting** (`cp … src/pages/Home/__orig_check.jsx`) — linting a file outside the project root picks up different flat-config resolution and under-reports (1 warning vs the real 15 errors). Delete the temp copy afterwards.
- **Report pre-existing lint debt as pre-existing, with the evidence.** "15 errors in SearchPage.jsx, confirmed identical on the backup copy, not from today's changes — want them cleaned?" is useful. Silently absorbing them looks like you broke something; fixing them unasked is scope creep.
