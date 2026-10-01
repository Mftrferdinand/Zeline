# Collapsing a responsive fork into ONE mobile-first layout

Trigger: *"ubah tampilan mode dekstop kaya mode Android aja tombol tombol nya di atas"*.

These forks ship a two-layout app: a `md:` desktop sidebar (`fixed`, `w-[84px]` collapsed,
expanding on hover) plus a separate `md:hidden` mobile top bar. Every page then carries
`md:`-prefixed compensation classes for both. This user develops and views on Android, so the
desktop branch is code they never see, drifts independently, and is where stale effects survive
(the `brand-sheen` shimmer lived on in the sidebar after being removed from mobile).

**Default for this user: delete the desktop branch. One top bar, all breakpoints.**

## Delete list

```bash
rm src/pages/Home/Sidebar.jsx src/pages/Home/BrandMark.jsx
grep -rn "Sidebar\|BrandMark" --include=*.jsx --include=*.js src/    # → only the layout import
```

`BrandMark` goes too if the sidebar was its last consumer — after the header wordmark became plain
text, the component held nothing but the shimmer config.

## Layout component edits (`ParentComponent.jsx`)

| Before | After |
|---|---|
| `<Sidebar … />` + 4 props | deleted |
| `<header className="md:hidden fixed top-0 …">` | drop `md:hidden` — bar is universal |
| `<div className="md:pl-[84px] pt-[…] md:pt-0 pb-8">` | `<div className="pt-[calc(env(safe-area-inset-top)+3.25rem)] pb-8">` |
| `px-3.5` header padding | `px-3 sm:px-4` (a bit more room on wide screens) |
| `active:text-white` only | add `hover:` variants — desktop has a real pointer now |

Also drop the props the sidebar alone consumed (`selectedGenreId`, `handleGenreSelect`,
`useSearchParams`, `getCategoryBySlug`) or ESLint flags them unused.

## The trap: deleting the sidebar deletes a FEATURE

The sidebar was the only place with the **genre list**. Remove it naively and desktop users lose
genre filtering entirely. The mobile equivalent already exists but is hidden on desktop:

```jsx
// Movie.jsx / Series.jsx — genre chips
- <div className="md:hidden overflow-x-auto hide-scrollbar mt-3 -mx-4 px-4 pb-1">
+ <div className="overflow-x-auto hide-scrollbar mt-3 -mx-4 px-4 pb-1">
```

Rule: before deleting a breakpoint-specific component, list what it uniquely provides and unhide
the mobile counterpart in the same edit.

## Sticky headers now collide with the fixed top bar

Browse pages use `sticky top-0 z-40`, which was fine when the top bar was `md:hidden` on desktop and
`z-40` on mobile only. With one universal fixed bar, sticky sub-headers must sit *below* it:

```jsx
- sticky top-0 z-40
+ sticky top-[calc(env(safe-area-inset-top)+3.25rem)] z-30
```

`z-30` < the header's `z-40` so the bar always wins the overlap.

## Sweep the leftover `md:` compensation

```bash
grep -rn "md:pl-\[84px\]\|md:pt-\|safe-area-inset-top" src/
```
Every `md:pt-0` / `md:pt-10` existed to undo mobile spacing on desktop. With one layout they create
empty bands or clipped content. Seen in this project:

- `Movie.jsx` / `Series.jsx`: `pt-[calc(env(safe-area-inset-top)+1rem)] md:pt-4` → `pt-4`
- `SearchPage.jsx`: wrapper `pt-0 md:pt-10` → `pt-0`; and its sticky spacer div
  (`sticky top-0 … md:bg-transparent md:border-none mb-4 md:mb-0`) collapses to a plain `pt-3 pb-1`.

## Verify

```bash
npm run build
cd dist/assets
grep -o 'md:pl-\[84px\]' *.js *.css | wc -l    # → 0
for s in brand-sheen brandSheen logo-sweep logoSweep; do
  printf '%-14s ' "$s"; grep -o "$s" *.js *.css 2>/dev/null | wc -l   # → 0
done
```
Expect the bundle to *shrink* (455KB → 448KB here); a whole component tree plus its CSS left.
Then hit `/`, `/movies`, `/series`, `/search` and a real watch route.
