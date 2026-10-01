# Ripping out an optional auth / account subsystem (Firebase + watchlist + continue-watching)

Trigger phrasing seen: *"bisa ga gada sistem login dan profil dan watch list saved, hapus aja kalo bisa, dan nonton film bisa tanpa harus login"*.

This is the **endpoint** of the "make Firebase optional" work in SKILL.md Step 6. Guarding `auth === null` at every call site keeps the app alive with blank env, but leaves dead UI (Sign In buttons, empty Watchlist page) and ~350KB of SDK in the bundle. When the user says *hapus aja*, delete the subsystem instead of guarding it.

Do the deletion in dependency order — **leaf pages → context/provider → call sites → deps → env**. Deleting the provider first makes every consumer throw and you lose the ability to build-check intermediate states.

## 0. Snapshot first (this is a wide, hard-to-hand-reverse delete)

```bash
cd ~ && tar czf zerostream-backup-$(date +%Y%m%d-%H%M).tar.gz \
  --exclude=node_modules --exclude=dist zerostream
```
~4MB for a Vite app. Cheap insurance across a 12-file delete, and it doubles as the ESLint baseline in step 6. Tell the user the tarball path in the final report.

## 1. Map the blast radius before touching anything

```bash
# every module that imports the subsystem
grep -rnE "firebase|Watchlist|watchlist|AuthModal|onAuthStateChanged|useWatchlist|continueWatching" src/
```
Typical fork layout — 12 files to delete:

| File | Role |
|---|---|
| `src/firebase.js` | SDK init |
| `src/components/AuthModal.jsx` | login/signup modal |
| `src/context/WatchlistContext.jsx` | provider + `useWatchlist` |
| `src/utils/continueWatching.js` | Firestore writes |
| `src/pages/Home/WatchlistPage.jsx` | saved list page |
| `src/pages/Home/ResetPasswordPage.jsx` | auth flow |
| `src/pages/Home/EmailVerificationPage.jsx` | auth flow |
| `src/pages/Home/AuthActionPage.jsx` | Firebase `?mode=&oobCode=` dispatcher |
| `src/pages/Home/ContinueWatchingRow.jsx` | account-scoped home row |
| `TrendingRow.jsx` / `PersonalizedRow.jsx` / `HeroBanner.jsx` | dead code from earlier homepage rewrite |

`rmdir context utils components 2>/dev/null` after — these dirs are often left empty.

## 2. Call sites to edit (not delete)

- **`main.jsx`** — drop `<WatchlistProvider>` wrapper. Keep `QueryClientProvider` / `HelmetProvider`.
- **`App.jsx`** — remove auth + watchlist `<Route>`s, then **add a catch-all**:
  ```jsx
  import { BrowserRouter as Router, Route, Routes, Navigate } from 'react-router-dom';
  <Route path="*" element={<Navigate to="/" replace />} />
  ```
  Without it, an old bookmark or a link the user still has to `/watchlist`, `/login`, `/reset-password` renders a blank page inside the layout. With it, every removed route quietly lands on home.
- **`Sidebar.jsx`** — drop the `watchlist` entry from `NAV_ITEMS`, delete the whole bottom Sign In / Log Out block, drop the `onOpenAuthModal` prop + its propType, drop the cached-user `localStorage` helper. The bottom block was the only `mt-auto` element, so replace it with `<div className="mt-auto h-6 shrink-0" />` or the genre list loses its bottom spacer.
- **`ParentComponent.jsx`** — remove the bookmark + account/logout header icons, the `openAuthModal` window-event listener, `handleLogout`, the `user` state, and `<AuthModal>`. Collapse the icon `.map()` to a single Search button once only one icon remains. Also drop `watchlist` from the `activePage` ternary chain.
- **`ContentCard.jsx`** — remove the `+`/`✓`/trash watchlist button from BOTH the hover overlay and the trailer pop-out, plus props `posterPath`, `voteAverage`, `onNeedAuth`, `isWatchlistPage` and their propTypes. Change the overlay from `justify-center gap-3` to `justify-center` (a lone play button with a dangling gap is off-centre).
- **`MovieDetails.jsx` + `TvDetails.jsx`** — near-duplicates, fix both: drop the `useWatchlist()` destructure, the `toggleWatchlist` handler, the "Add to Watchlist" `<button>` and its wrapping `{/* Actions */}` div, the `saveToContinueWatching` effect, `<AuthModal>`, `isAuthModalOpen` state **and its reset inside the `useLayoutEffect` state-reset block** (easy to miss — surfaces as `no-undef` on `setIsAuthModalOpen`).
- **`HomePage.jsx`** — remove `<ContinueWatchingRow>` + its import.

## 3. Uninstall the deps

```bash
npm uninstall firebase pocketbase
```
Check for other orphans the fork shipped (`pocketbase` was vestigial — imported nowhere, never used). Bundle went **~800KB → 453KB (139KB gzip)**; quote the number, it's the cleanest proof the subsystem is gone.

## 4. Scrub the credentials from `.env` AND `.env.example`

Non-negotiable when the target is a public repo (this user's standing rule: public releases must not leak personal data). Remove the whole `VITE_FIREBASE_*` block from both files, and delete the "Firebase (OPSIONAL)" explanatory section from `.env.example` — otherwise the example still instructs future users to configure a subsystem that no longer exists.

## 5. Verify against the BUILT bundle

```bash
npm run build
grep -c "firebase\|firestore\|identitytoolkit" dist/assets/*.js   # must be 0
grep -rnE "firebase|useWatchlist|AuthModal|onAuthStateChanged" src/ # only comments may remain
```
Comment strings survive the sweep and read as leftovers in the next session's grep — reword them (`{/* Hover overlay — Play + Watchlist */}` → `{/* Hover overlay — Play */}`).

Route check, with the catch-all in place every path is a 200 (SPA fallback serves `index.html`; the redirect is client-side):
```bash
for p in / /movies /series /search /watchlist /login; do
  printf "%-12s " "$p"; curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:4173$p"
done
```

## 6. ESLint on a repo with pre-existing lint debt — baseline, don't chase zero

These forks ship with dozens of pre-existing violations (`react/prop-types`, unused `React` imports, `no-unescaped-entities`). `npx eslint src` returned **49 errors** after the deletion, which looks alarming and tempts a cleanup spree the user never asked for.

Compare against the pre-change tarball instead:
```bash
mkdir -p "$PREFIX/tmp/zsbak" && tar xzf ~/zerostream-backup-*.tar.gz -C "$PREFIX/tmp/zsbak"
cd "$PREFIX/tmp/zsbak/zerostream" && ln -sfn ~/zerostream/node_modules node_modules
npx eslint src 2>&1 | tail -3     # → 87 errors before vs 49 after
```
Down, not up ⇒ no new errors introduced. Then lint **only the files you touched** and drive *those* to clean:
```bash
npx eslint src/App.jsx src/main.jsx src/pages/Home/{HomePage,MixedRow,ContentCard,Sidebar,ParentComponent}.jsx src/pages/Home/homeFeed.js
```
Use `$PREFIX/tmp` — Termux has no `/tmp`, so `cd /tmp` fails with "No such file or directory". Also note `npx eslint --ext` is rejected under flat config (`eslint.config.js`); pass paths directly.

The two real errors this delete introduces, both trivial: a `no-undef` on `setIsAuthModalOpen` left in a state-reset block, and `'React' is defined but never used` once JSX-only files stop using the namespace.

## 7. Restart preview cleanly so the URL is deterministic

Stale `vite preview` processes stack up across a long session and each new one drifts a port (`4173 → 4174 → 4175`), so the user gets a different URL every turn. Before restarting: `process(action='list')`, kill each preview session, then `pkill -f "vite preview"`, then start one. Confirm the served bundle hash matches the newest file in `dist/assets/` before handing over the URL.

## 8. Report the functional loss unprompted

Deleting the subsystem also deletes **Continue Watching** — its storage was account-scoped Firestore. Don't let the user discover this. State it, and offer the no-account replacement: same row backed by `localStorage`, per-device, no login. (This user's history says they want the feature, not the account.)
