# Homepage mixed feed — curated row order, global dedup, one-constant spacing

Reference for Step 11 of `fork-and-rebrand-webapp`. This is the shape the user
converged on for the Zerostream (WeFlix fork) homepage after two rounds of
correction. Build this directly; the naive "one genre table, each row fetches
itself" version gets rejected.

## The three requirements, verbatim

- `"baris pertama movie yang trending global / ke dua series yang trending global / ketiga movie trending di Indonesia / ke empat series trending indonesia / ke lima anime dan kartun / baris ke 6 - 10 random"`
- `"buat 10:20 film jangan ada yang sama di home page"` → 200 cards, zero duplicates.
- `"tiap baris jaraknya sama kaya ke samping jangan terlalu lebar, presisikan"` → vertical gap == horizontal gap.

## Iteration 3 — the series rows got cut (final shape)

One turn after the order above shipped, the user narrowed it: `"jangan ada series indo trend di home page, hanya movies indo aja, jangan ada series global trend"`. Final row order:

| # | Row | Content |
|---|---|---|
| 1 | Movie trending global | `/trending/movie/week`, movie only |
| 2 | Movie Indonesia | `/discover/movie?with_origin_country=ID`, movie only |
| 3 | Anime & kartun | genre `16`, movie+tv interleaved |
| 4-10 | Random genre | movie+tv interleaved, shuffled per visit |

Keep the row count at 10 by bumping the random slice from 5 to **7** (`shuffle(RANDOM_POOL).slice(0, 7)`) — deleting two rows without compensating silently ships an 8-row homepage.

The lesson for next time: **standalone series rows are not wanted; series belong mixed into genre rows, never as their own trending strip.** Default to that. Verify the dedicated series endpoint is actually gone from the built bundle (`grep -c "trending/tv/week" dist/assets/*.js` → `0`) and assert row 2 has zero non-movie items in the verification script.

## Architecture: data module + dumb row component

Global dedup is impossible while each row owns its own `useEffect` fetch. Split:

- `homeFeed.js` — every fetch, the row order, the shuffle, the dedup. Exports
  `buildRowSpecs()` and `loadHomeFeed(specs, perRow)`.
- `MixedRow.jsx` — presentational only. Takes `items`, `onMore`, `onSelect`.
  Keeps the drag-to-scroll refs. No data logic.
- `HomePage.jsx` — `useMemo(buildRowSpecs, [])` so the random pick is stable
  across re-renders, then one `useEffect` calling `loadHomeFeed`.

## TMDB endpoint choices that matter

| Row | Endpoint | Notes |
|---|---|---|
| Movie trending global | `/trending/movie/week` | returns `media_type` already |
| Series trending global | `/trending/tv/week` | same |
| Movie Indonesia | `/discover/movie?with_origin_country=ID&sort_by=popularity.desc` | see below |
| Series Indonesia | `/discover/tv?with_origin_country=ID&sort_by=popularity.desc` | 41 pages available, plenty |
| Anime & kartun | `/discover/{movie,tv}?with_genres=16` interleaved | `vote_count.gte` 30 (movie) / 15 (tv) |
| Rows 6-10 | `/discover/{movie,tv}?with_genres=<pair>` interleaved | genre pair shuffled from a 12-entry pool |

- **`with_origin_country=ID` over `with_original_language=id`.** Both return
  Indonesian titles (verified: same top 3 — Ikatan Darah, Me Before Me, The Raid)
  but origin-country also catches co-productions and gives more pages.
- **`/discover` does NOT return `media_type`** (unlike `/trending`). Tag it
  yourself or the detail-page router can't tell movie from tv.
- **Movie and TV genre IDs differ.** No `28` (Action) for tv — it's `10759`
  (Action & Adventure). Sci-Fi is `878` movie vs `10765` tv. Pair per row.
- **Over-fetch 2-3 pages per row.** After global dedup a single 20-item page
  starves — a row that only fetched page 1 can end up with 11 cards.
- **`interleave(a, b)` alternating one-by-one**, not `[...a, ...b]`. Concatenating
  puts all movies first and the row doesn't read as mixed.

## Global dedup: hand out cards in row order through one Set

```js
export const loadHomeFeed = async (specs, perRow = 20) => {
  const raw = await Promise.all(specs.map((s) => s.fetch()));
  const seen = new Set();
  return specs.map((spec, i) => {
    const picked = [];
    for (const item of raw[i]) {
      const id = `${item.media_type}-${item.id}`;
      if (seen.has(id)) continue;
      seen.add(id);
      picked.push(item);
      if (picked.length >= perRow) break;
    }
    return { ...spec, items: picked };
  });
};
```

Row order is priority order: row 1 picks first, so trending global keeps its
titles and the random rows absorb the collisions. Reversing this makes the
headline rows look gutted.

## Spacing: one constant, both axes

```jsx
export const GAP = 12;          // MixedRow.jsx
const CARD_W = 132;
```

Drive everything from it — `gap`, `paddingLeft`, `paddingRight`,
`marginBottom` on the section, and the page wrapper's `paddingTop/Bottom`.
The template ships `mb-12` (48px) vertical against `gap-3` (12px) horizontal;
that mismatch is exactly the "terlalu lebar" the user flagged.

Hover-scale headroom without breaking the rhythm: `paddingTop/Bottom: 16` on
the scroll container, cancelled by `marginTop/Bottom: -16`.

Normalise card width across EVERY row including `ContinueWatchingRow` (shipped
at 160px) — one stray width and the vertical rhythm reads as broken.

Skeleton must reuse `GAP`, `CARD_W`, and `aspectRatio: '2/3'` or the page jumps
when data lands.

## Verification script

Mirror the real dedup logic in a throwaway `.cjs` and run it under node before
shipping. Node 18+ has global `fetch`, so no deps. Assert:

```
ROWS: 10  TOTAL CARDS: 200  UNIQUE: 200  DUPES: 0
```

and print per-row `(mv:N tv:N)` counts plus the first 3 titles — that catches a
bad genre id, an empty Indonesia row, and an unmixed row in one glance. Delete
the script afterwards.

Also confirm the removed section labels are gone from the **built** bundle, not
just source:

```bash
cd dist/assets && for s in "Trending Movies" "Top 10" "Trending Series" \
  "Asian Series" "Now Playing" "Because you watched" "Loading Highlights"; do
  printf "%-22s " "$s"; grep -c "$s" *.js
done   # every count must be 0
```

## Random rows

Pool of 12 genre pairs, `shuffle().slice(0, 5)`. Because `buildRowSpecs()` runs
inside `useMemo(..., [])`, the pick is stable for the session but different on
next visit — which is what "random" meant here, not per-render churn.
