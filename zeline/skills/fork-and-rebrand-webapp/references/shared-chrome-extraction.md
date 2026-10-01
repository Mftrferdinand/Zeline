# Single-sourcing duplicated chrome in a forked SPA

Forked React/Vite templates hand-write shared UI (footer, player, brand lockup, detail pages)
separately per page. The copies drift, and the user finds the drift before you do.

Real trigger: *"pas nonton film bagian bawah gada by Mftrferdinand, tambahkan sama seperti homepage"*
— the home footer had a "Developed by" credit, the watch pages had a different, shorter footer.

## Confirmed duplicate counts (one project, `zerostream`)

| Element | Copies | Locations |
|---|---|---|
| `<footer>` | 3 | `ParentComponent.jsx` (home only, gated), `Movie/MovieDetails.jsx`, `TV/TvDetails.jsx` |
| `VideoPlayer.jsx` | 2 | `Movie/`, `TV/` — near-identical `SOURCES` tables |
| Detail page | 2 | `MovieDetails.jsx`, `TvDetails.jsx` — same layout, different field names (`title`/`name`, `release_date`/`first_air_date`) |
| Brand lockup | 3 | mobile header, desktop `Sidebar.jsx`, `BrandMark.jsx` |
| `© YEAR Brand \| TMDB` | 3 | one per footer |

Rule of thumb: **any visible element in these forks exists 3+ times.** Count before editing.

## Procedure

1. **Count the copies first.**
   ```bash
   grep -rn "<footer" src/            # → 3 hits
   grep -rn "Developed by" src/       # → 1 hit (that's the drift: 2 footers lack it)
   ```

2. **Pick the richest copy as the source of truth** — usually the home/layout one, since that's
   the version the user has been iterating on and the one they'll cite as "sama seperti homepage".

3. **Extract to a component** (`src/pages/Home/SiteFooter.jsx`), no props needed if the content is static.
   Keep the exact markup of the chosen copy; don't "improve" it during extraction or you introduce a
   second visual change the user didn't ask for.

4. **Render once in the layout route, after `<Outlet />`.** In react-router v6 the layout component
   (`ParentComponent`) wraps all pages via `<Route element={<ParentComponent />}>`, so one instance
   there covers every page.
   ```jsx
   <div className="md:pl-[84px] pt-[...] pb-8">
     <Outlet />
     <SiteFooter />
   </div>
   ```

5. **Drop any per-path gate.** The original was `{location.pathname === '/' && <footer>…</footer>}`
   — that conditional existed only because other pages had their own. With one footer it must be
   unconditional. (Watch for `location` becoming an unused variable if it was only used for the gate.)

6. **Delete the per-page copies.** Patch out the whole `<footer>…</footer>` block from each detail page.
   Both `Movie/` and `TV/` — fixing one leaves the other wrong.

## Verification — count in the BUILT bundle, not the source

```bash
cd dist/assets
echo -n 'Developed by : '; grep -o 'Developed by' *.js | wc -l          # → 1  (single-sourced)
echo -n 'credit name  : '; grep -o 'Mftrferdinand' *.js | wc -l          # → 1
echo -n 'old footer   : '; grep -o 'bg-\[#040507\] border-t' *.js | wc -l # → 0  (copies gone)
```

`1` proves single-sourcing. `2+` means a duplicate survived the delete. `0` on the first line means
you deleted the copies but never mounted the shared component.

Then exercise the **actual route the user complained about**, with a real id:

```bash
KEY=$(grep VITE_TMDB_API .env | cut -d= -f2)
ID=$(curl -s "https://api.themoviedb.org/3/trending/movie/week?api_key=$KEY" \
     | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)
for p in / /movies /series "/movies/watch/x-$ID" "/series/watch/x-1399"; do
  printf '%-26s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' "http://localhost:4173$p"
done
```

Note this is an SPA with a catch-all redirect, so **every** path returns 200 — the HTTP check only
proves the server is up. The bundle greps above are the real evidence.

## Footer LAYOUT this user converged on: 3 lines, all left-aligned, no ornament

Extraction alone wasn't the end of it. One turn after single-sourcing, the follow-up was:
*"ubah jadi 3 baris buat presisi … jadi zerostream sama Mftrferdinand ga sesajajar gua pengen 3baris
dan buat tulisanya semuanya di kiri presisi"*. The template's footer is a `justify-between` row
(`Zerostream · Developed by X` on the left, `© YEAR` flung to the right, `items-center`) — which is
exactly what they didn't want. Target shape:

```
Zerostream
Developed by Mftrferdinand
© 2026 Zerostream | TMDB
```

```jsx
<div className="px-3 py-5 flex flex-col items-start gap-1 text-[11px] text-gray-600">
  <span className="text-white font-bold text-[13px]">Zerostream</span>
  <span>Developed by <span className="text-gray-400 font-semibold">Mftrferdinand</span></span>
  <span>© {new Date().getFullYear()} Zerostream | TMDB</span>
</div>
```

- Drop `justify-between`, `items-center`, `sm:flex-row`, `max-w-5xl mx-auto` and the `·` separators.
  `flex-col items-start` is the whole fix.
- **"presisi" here means the footer's left edge lines up with the content above it.** Match the
  footer's horizontal padding to the home feed's `GAP` constant (`px-3` = 12px), not the template's
  `px-6`, or the text sits inboard of the poster edge and reads as misaligned.
- `font-black` → `font-bold` and no `brand-sheen` on the wordmark. Same rule as the header: this user
  wants the brand *name*, never brand *ornament*.
- Verify the old layout is gone from the bundle, not just that the new one is present:
  `grep -o 'sm:flex-row items-center justify-between gap-4' *.js | wc -l` → `0`.


- Extracting during an unrelated edit hides the change. Do the extraction as its own patch set.
- If the layout component used `location` *only* for the footer gate, ESLint flags it unused after
  removal — check, but don't go fixing unrelated pre-existing lint noise (see the ESLint-baseline
  section in SKILL.md).
- The detail pages' footers may use a different background (`bg-[#040507]` vs `bg-[#0a0c12]`).
  Once unified, the watch page's footer background changes slightly — expected, and consistent, but
  worth a one-line mention so the user isn't surprised.
