# Homepage endpoint: swipe hero + Top 10 + titled rows, and collapsing to Home + Search

Companion to Step 11b. This is where the Zerostream homepage actually landed after two reversals
(template sections → title-less strip → titled sections with a manual hero). Read the "preference
arc" section before assuming any earlier shape is final.

## The preference arc (why this keeps flip-flopping)

| Round | Ask | Result |
|---|---|---|
| 1 | *(fork default)* | Auto-sliding `HeroBanner` + 7 labelled rows + "Because you watched" |
| 2 | "hapus iklan Rekomendasi film yang bergerak", "jangan ada tulisan trending film, top 10 film" | 10 unlabelled mixed rows, no hero |
| 3 | "ubah homepage seperti awal aja pas pertama ada top trend top 10 dan lainya ada juga iklan Rekomedasi film di paling atas, **tapi buat bisa di swap sendiri**" | Hero (manual swipe) + Top 10 + titled rows |

**The invariant across all three is not "sections" or "no sections".** It is:

- **Anything that moves by itself is an ad.** The word "iklan" in round 2 was aimed at the
  `setInterval` carousel, not at the concept of a recommendation banner. Round 3 asked for the banner
  back the moment it was manual ("bisa di swap sendiri").
- **Ornament is out, information is in.** Titles came back because unlabelled rows read as random
  ("agar tertata rapih"). Gradients, shimmer, and icon logos never came back.

So: when this user deletes a moving UI element, do **not** infer they dislike the content. Offer the
manual version.

## Hero carousel — scroll-snap, not a timer

```jsx
// HeroCarousel.jsx — active index derived from scroll position, NOT a timer
const onScroll = useCallback(() => {
  const el = trackRef.current;
  if (!el) return;
  const idx = Math.round(el.scrollLeft / el.clientWidth);
  setActive((prev) => (prev === idx ? prev : idx));
}, []);

<div
  ref={trackRef}
  onScroll={onScroll}
  onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseLeave={endDrag}
  className="flex overflow-x-auto hide-scrollbar snap-x snap-mandatory select-none
             cursor-grab active:cursor-grabbing"
  style={{ scrollBehavior: 'auto' }}   // 'smooth' here fights the drag handler
>
  {items.map((item) => (
    <div key={`${item.media_type}-${item.id}`} className="shrink-0 w-full snap-center">…</div>
  ))}
</div>
```

Points that matter:

- **`snap-x snap-mandatory` + `overflow-x-auto` gives native Android momentum for free.** A JS
  transform carousel feels heavy on a low-end phone and needs its own gesture math.
- **Mouse drag still needs the manual refs** (`dragRef` with `startX`/`startScrollLeft`/`moved`,
  plus a `window` `mouseup` listener) because desktop pointer drag doesn't scroll a snap container.
  On drag release, snap explicitly: `el.scrollTo({ left: Math.round(el.scrollLeft/el.clientWidth) * el.clientWidth, behavior:'smooth' })`.
- **`suppressClickRef`** — same trap as the poster rows: without it, a drag that ends over the
  "Watch Now" button navigates. Set it from `moved` on drag end, clear it on the next tick.
- **`scrollBehavior: 'auto'` inline** on the track; the smooth scroll belongs only in the explicit
  `goTo(idx)` used by the dot indicators.
- **Hero needs `backdrop_path`, not `poster_path`.** Filter the trending payload on
  `backdrop_path && poster_path && media_type in ('movie','tv')` — some entries have one and not the
  other, and a missing backdrop renders a broken banner. Verified: 20/20 `/trending/all/week` items
  qualified, so 5 slides is safe.

### Hero sizing — use `svh`, never `aspectRatio`, and never `vh`

First attempt shipped `style={{ aspectRatio: '16 / 10', maxHeight: 460 }}`. On a phone-width viewport
that computes to a near-square box, and the user reported it exactly that way:
*"aga di bagusin tampil full sampe bawah itu terlihat hanya 1:1"*. **An aspect ratio on a
full-bleed hero is a width-derived height — on a narrow screen it collapses toward square.** Drive
height from the viewport instead:

```js
// ONE constant, shared by the carousel and its skeleton so data arrival can't shift layout
const HERO_H = 'min(72svh, 560px)';
// …
<div className="relative w-full overflow-hidden" style={{ height: HERO_H }}>
```

- **`svh`, not `vh`.** On Android, `vh` resolves against the viewport with the URL bar *collapsed*,
  so a `vh`-sized hero is taller than what's actually on screen and its bottom content — the title
  and the Watch Now button — sits under the URL bar. `svh` (small viewport height) always uses the
  smallest viewport, guaranteeing the whole hero is visible. `dvh` would resize mid-scroll and jitter.
- **Cap with `min(…, 560px)`** so a tablet/desktop doesn't get an absurdly tall banner.
- **Two gradients, not one**, once the hero is tall: a full-height
  `bg-gradient-to-t from-[bg] via-[bg]/60 via-45% to-transparent` for text legibility, plus a short
  `h-24` bottom fade so the banner melts into the page background instead of ending on a hard edge.
- **Skeleton must use the same `HERO_H` constant.** An `aspectRatio` skeleton against a `svh` hero
  visibly jumps when the fetch lands.

## Row table that satisfied "top trend top 10 dan lainya"

| key | title | endpoint |
|---|---|---|
| `hero` | *(none)* | `/trending/all/week` → 5 items, backdrop-filtered |
| `top10` | `Top 10 Trending Today` | `/trending/all/day` → 10 items |
| `now-playing` | `In Theaters Now` | `/movie/now_playing` ×2 pages |
| `id-movie` | `Popular in Indonesia` | `/discover/{movie,tv}?with_origin_country=ID`, interleaved |
| `anime-cartoon` | `Anime & Animation` | `/discover/{movie,tv}?with_genres=16`, interleaved |
| `top-rated` | `Highest Rated of All Time` | `/movie/top_rated` + `/tv/top_rated`, interleaved |
| `trending-tv` | `Trending Series` | `/trending/tv/week` ×2 pages |
| `random-*` ×6 | genre label | `/discover/{movie,tv}?with_genres=<paired ids>` |

All seven endpoints verified live with the project's v3 key — each returned 20 results. Probe before
shipping; a bad genre id renders a silently empty row.

## Top 10 numbering — built twice, then deleted. Default to NO numerals.

Final state for this project: **the Top 10 row renders exactly like every other row.** Heading
`Top 10 Trending Today` + 10 items in rank order, uniform `GAP`, no per-item margin, no `variant`
prop. The numerals were requested implicitly (a "Top 10" needs ranks, surely), corrected once, then
killed: *"angka 1234567 di top hapus aja"*.

The arc, because it cost two turns:

| Round | Implementation | Outcome |
|---|---|---|
| 1 | outline-only digit at `z-0` **behind** the poster | invisible — *"buat di depan jangan di belakang"* |
| 2 | solid white digit at `z-30` in front, dark stroke | visible, looked right | 
| 3 | — | *"hapus aja"* — deleted |

**Lesson: on this project, ordering is conveyed by the heading and the sequence, not by chrome
drawn over the artwork.** Don't add rank numerals unasked. The user's ornament aversion extends to
elements that carry information *redundantly* — a numeral on card 3 of a row titled "Top 10" tells
them nothing the position didn't.

If a different project genuinely wants them, this is the version that worked visually:

```jsx
{isTop10 && (
  <span
    aria-hidden="true"
    className="absolute left-0 bottom-6 -translate-x-[42%] font-black leading-none
               select-none pointer-events-none z-30 text-white"
    style={{
      fontSize: 88,
      WebkitTextStroke: '4px #0a0c12',   // dark outline keeps it legible over bright posters
      paintOrder: 'stroke fill',          // stroke UNDER fill, else the outline eats the glyph
    }}
  >
    {idx + 1}
  </span>
)}
<div className="relative z-10">{/* ContentCard */}</div>
```

- **`z-30` over the card's `z-10`.** An outline-only digit at `z-0` behind an opaque 2:3 poster is
  not "subtle", it is gone. There is no middle setting.
- **`paintOrder: 'stroke fill'` is required.** Default paint order draws the stroke *over* the fill,
  so a 4px stroke on an 88px glyph visibly thins it back into an outline.
- The numeral overflows left, so that row needs `gap: GAP + 10` and `marginLeft: 26` per item —
  tune together or the digit collides with the previous poster.

### Removing it: delete the config axis too, not just the render

The numeral came with a whole variant mechanism — `variant="top10"` prop, an `isTop10` local, a
conditional `gap`, a conditional `marginLeft`, and `PropTypes.oneOf(['row','top10'])`. Deleting only
the `<span>` leaves a component that still *claims* to have two modes while rendering one, and the
parent still threads a meaningless prop. Remove all of it in the same pass:

```bash
# after the edit, these must all be 0 in the built bundle
cd dist/assets && for s in paintOrder "stroke fill" "4px #0a0c12" isTop10; do
  printf '%-16s ' "$s"; grep -o "$s" *.js *.css 2>/dev/null | wc -l; done
```

`top10` itself will still appear (~3 hits) — it survives as the **data** key in `homeFeed.js` that
caps the row at 10 items. That's correct; read the context (`grep -o '.\{30\}top10.\{60\}'`) rather
than chasing the count to zero.

## Global dedup with a hero exemption

```js
export const loadHomeFeed = async (specs, perRow = 20) => {
  const raw = await Promise.all(specs.map((s) => s.fetch()));
  const seen = new Set();

  return specs.map((spec, i) => {
    if (spec.kind === 'hero') return { ...spec, items: raw[i].slice(0, 5) };  // exempt

    const limit = spec.kind === 'top10' ? 10 : perRow;
    const picked = [];
    for (const item of raw[i]) {
      const id = `${item.media_type}-${item.id}`;
      if (seen.has(id)) continue;
      seen.add(id);
      picked.push(item);
      if (picked.length >= limit) break;
    }
    return { ...spec, items: picked };
  });
};
```

The hero exemption is a correctness fix, not a shortcut: hero shows this week's top 5, Top 10 shows
today's top 10, and they legitimately overlap. Feeding hero through the shared `seen` Set silently
knocks the biggest titles out of Top 10, which then isn't a top 10.

## Collapsing to Home + Search

### Delete order (deepest first)

1. `pages/Home/Movie/Movie.jsx`, `pages/Home/TV/Series.jsx` — the browse pages
2. `pages/Home/ContentGrid.jsx` — the infinite-scroll grid only they used
3. `pages/Home/urlFilters.js` — genre/sort slug ↔ path mapping
4. `pages/Home/tmdb.js` — `GENRES`, `SPECIAL_CATEGORIES`, `SPECIAL_PARAMS`
5. `Fetcher.js`: drop `fetchContentByGenre` + `fetchTrending` (keep the detail/person fetchers)
6. `main.jsx`: drop `QueryClientProvider` once the rewritten Search no longer uses `useInfiniteQuery`

After each step: `grep -rn "<symbol>" --include=*.jsx --include=*.js src/`. Two things that only
show up on that grep, not in the build:

- **`state: { from: '/movies' }` / `'/series'`** in `MovieDetails.jsx` and `TvDetails.jsx` — the back
  breadcrumb now points at a redirect. Repoint to `'/'`.
- **`App.jsx` needs `<Route path="*" element={<Navigate to="/" replace />} />`** or old `/movies`
  bookmarks render blank inside the layout. Note that on a Vite SPA every path returns 200 either
  way (dev server falls back to `index.html`), so a 200 on `/movies` proves nothing about whether the
  redirect works — check the rendered content, not the status.

### Lightweight search

```
one <input> → 400ms debounce → /search/multi?query=&page= → grid → "Load more" button
```

- **`/search/multi`** returns movies + series in one call; filter to
  `poster_path && media_type in ('movie','tv')` (drops `person` hits, which have no poster page here).
- **Debounce writes `?q=` too** (`setParams(trimmed ? {q: trimmed} : {}, {replace:true})`) so results
  survive a refresh and can be shared. `replace:true` keeps the back button usable.
- **`Load more` over infinite scroll** on a low-end Android: no scroll listener, no IntersectionObserver,
  no layout thrash mid-swipe. Track `page` + `total_pages`, append with a `seenRef` Set so paging
  can't duplicate a card.
- **No react-query.** Plain `fetch` in a `useCallback` + four `useState` is smaller and there's one
  query in the whole app. Removing the provider dropped `tanstack` to 0 occurrences in the bundle.
- Skeleton only on the *first* page (`loading && results.length === 0`); showing it during
  `Load more` makes the existing grid flicker.

### The bug they blamed on search wasn't in search

*"banyak bug home jd hilang"* — the nav vanishing was the `visualViewport` keyboard heuristic in
`ParentComponent.jsx` (Step 9), which fires on Android URL-bar collapse and never resets. It was
merely *noticed* on the search screen because that's where the keyboard opens. **When a user
attributes a bug to a screen, check the shared shell before rewriting that screen.** Here the rewrite
was wanted anyway, but the fix lived elsewhere — shipping only the rewrite would have left the bug.

**And the first fix didn't hold.** Replacing the viewport heuristic with a focus/blur trigger still
hid the header — so the very next turn the user reported it again from the search screen:
*"pada saat searching pa keyboard muncul kenapa homepage tombolnya hilang"*. The nav-hiding feature
itself was the bug. Delete `keyboardOpen` + the effect + the conditional `-translate-y-full`
outright; see the Step 9 pitfall. **Two failed fixes that share a premise means the premise is
wrong** — stop tuning the trigger.

## Post-ship correction round (all three fixed in one turn)

The first delivery of this homepage drew exactly three corrections. Pre-empt them:

| Complaint | Cause | Fix |
|---|---|---|
| *"terlihat hanya 1:1"* — hero looks square | `aspectRatio: '16/10'` collapses toward square at phone width | `height: 'min(72svh, 560px)'` |
| *"angka … buat di depan jangan di belakang"* | rank numeral at `z-0` behind an opaque poster | `z-30`, solid white + dark stroke, `paintOrder: 'stroke fill'` — **then deleted entirely one turn later; don't build it at all** |
| *"keyboard muncul … tombolnya hilang"* | header hides on input focus | delete the hide mechanism entirely |

All three are the same failure mode: an implementation choice that is defensible in the abstract
(preserve aspect ratio, keep decoration subtle, maximise space for the keyboard) but wrong against
this user's actual device and preferences. When in doubt on this project, choose **bigger, more
visible, always-present** over clever.

**Two of the three were second attempts at the same complaint.** The numeral and the nav both took
a correction round *after* a "fix", because the first fix preserved the flawed premise (decoration
can be subtle / the nav can hide sometimes) and only adjusted its parameters. Cheapest correction on
this project is usually **remove the element**, not retune it.

## Verification actually run

```bash
# 1. lint only the files you wrote (repo has ~31 pre-existing errors elsewhere)
npx eslint src/App.jsx src/main.jsx src/pages/Home/{HomePage,HeroCarousel,MixedRow,SearchPage,ParentComponent,SiteFooter}.jsx src/pages/Home/homeFeed.js

# 2. build
npm run build          # 455KB → 378KB (137 → 118 gzip)

# 3. strings that must be GONE / PRESENT in the built bundle
cd dist/assets
for s in BiSliderAlt MdOutlineTune useInfiniteQuery SPECIAL_PARAMS fetchContentByGenre tanstack; do
  printf '%-22s ' "$s"; grep -io "$s" *.js | wc -l; done          # all 0
for s in "Top 10 Trending Today" "In Theaters Now" "Recommended" "snap-mandatory" "search/multi"; do
  printf '%-24s ' "$s"; grep -o "$s" *.js | wc -l; done            # all 1

# 4. Tailwind classes used only in new files must exist in the built CSS
grep -o 'snap-mandatory\|snap-center\|line-clamp-2\|md:grid-cols-6' dist/assets/*.css

# 5. every TMDB endpoint the new feed calls
#    (loop the 7 paths with the .env key, assert results==20)
```

**`grep` counts need reading, not trusting.** A "Movies must be 0" check returned 1 — the hit was
`title="Zerostream — Stream Movies & TV Shows"` in the SEO description, not a surviving nav button.
Widen with `grep -o '.\{50\}Movies.\{50\}'` before reporting either clean or dirty.
