# Mobile Dashboard Menu, Backdrop Blur, and Persistent Theme

Use this reference when refining a compact portfolio header/dashboard menu on mobile, especially when the user wants the page behind the open menu to blur while the menu remains sharp.

## Correct layer architecture

Do not blur the main page container with `filter: blur(...)`; that can blur the menu too and creates awkward stacking contexts. Use three layers:

1. **Page content** at the normal document layer.
2. **Full-screen backdrop** below the header/menu but above page content.
3. **Header + menu panel** above the backdrop, kept sharp.

```css
.nav { position: sticky; top: 0; z-index: 50; }
.nav-backdrop {
  position: fixed; inset: 0; z-index: 40;
  background: rgba(216,224,231,.34);
  backdrop-filter: blur(8px) saturate(78%);
  -webkit-backdrop-filter: blur(8px) saturate(78%);
  opacity: 0; visibility: hidden; pointer-events: none;
  transition: opacity .24s ease, visibility .24s ease;
}
body.nav-menu-open { overflow: hidden; }
body.nav-menu-open .nav-backdrop {
  opacity: 1; visibility: visible; pointer-events: auto;
}
```

The overlay should be a blue-white haze, not a black dimmer, when the site's palette is icy blue-gray. A useful baseline is `#D8E0E7` with accent `#0A84FF`.

## Compact menu trigger with safe touch target

The user prefers small visual controls, but mobile accessibility still needs a generous hit area. Keep the visible button around `30×30px`, then add an invisible `44×44px` pseudo-element:

```css
.menu-summary { position: relative; width: 30px; min-height: 30px; }
.menu-summary::after {
  content: '';
  position: absolute;
  width: 44px; height: 44px;
  left: 50%; top: 50%;
  transform: translate(-50%,-50%);
}
```

Do not make the whole visual button 44px if the user explicitly wants a compact header.

## Premium compact panel

- Width: roughly `178–188px` on a ~412px viewport.
- Rounded corners: `16–18px`, not an oversized 24px bubble.
- Menu row: `31–32px` minimum height, ~`11.5–12px` type.
- Glass: cool blue-white translucent gradient, thin light border, restrained shadow.
- Add a tiny uppercase `NAVIGATION` eyebrow for hierarchy.
- Use subtle 4px accent dots rather than emoji or large icons.
- Opening animation should be transform/opacity only (`translateY(-5px) scale(.98)`), ~200ms.

## State synchronization

With native `<details>`, listen to `toggle` and synchronize body/backdrop state. Do not assume setting `details.open = true` always fires at the same time in every browser test; verify computed overlay opacity.

```js
const dashboard = document.querySelector('.nav-dashboard');
const backdrop = document.querySelector('.nav-backdrop');

function syncMenu() {
  const open = Boolean(dashboard?.open);
  document.body.classList.toggle('nav-menu-open', open);
  if (backdrop) {
    backdrop.setAttribute('aria-hidden', String(!open));
    backdrop.style.opacity = open ? '1' : '0';
    backdrop.style.visibility = open ? 'visible' : 'hidden';
    backdrop.style.pointerEvents = open ? 'auto' : 'none';
  }
}

dashboard?.addEventListener('toggle', syncMenu);
backdrop?.addEventListener('click', () => { dashboard.open = false; });
dashboard?.querySelectorAll('a').forEach(a =>
  a.addEventListener('click', () => { dashboard.open = false; })
);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && dashboard?.open) dashboard.open = false;
});
```

Inline style updates are a robust fallback when a CSS class appears on `body` but computed overlay opacity remains `0` in an Android/WebView-like environment.

## Persistent dark mode without flash

Use a small icon-only theme button beside the dashboard trigger. Dark mode should use deep blue-gray, never flat black.

Suggested tokens:

```css
:root[data-theme='dark'] {
  --bg: #111923;
  --surface: #1a2532;
  --ink: #F1F6FC;
  --ink-2: #D7E1EC;
  --muted: #94A5B7;
  --line: #2B3A49;
  --accent: #4DA6FF;
  color-scheme: dark;
}
```

Prevent theme flash by applying the saved/system theme in an inline `<head>` script before the page renders:

```js
const saved = localStorage.getItem('mf-theme');
const theme = saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
document.documentElement.dataset.theme = theme;
```

On toggle:

- Update `documentElement.dataset.theme`.
- Save to `localStorage`.
- Update `aria-label`, `aria-pressed`, and `<meta name="theme-color">`.
- Use sun/moon SVGs, not emoji.
- Keep the visual control ~30px on mobile with a 44px invisible hit target.

## Verification gate

Do not rely on HTTP 200 or static screenshot alone. Verify in a real browser context:

1. Trigger visible size is around `30×30px`.
2. Open panel dimensions are compact (approximately `184×386px` for eight rows, not a half-screen oversized card).
3. `details.open === true`.
4. Body has the open-state class.
5. Backdrop computed `opacity === "1"`.
6. Backdrop computed filter includes `blur(8px)`.
7. Menu panel and header remain sharp in screenshot.
8. Theme click changes `data-theme`, persists to `localStorage`, updates `aria-pressed`, and changes `theme-color`.
9. Reload preserves the selected theme.
10. Run the project build after edits; repeated Astro hot reloads can wedge the dev server, so restart cleanly if `curl` hangs or CPU spikes.

## Common failure modes

- **Backdrop class exists but opacity remains 0:** synchronize inline opacity/visibility/pointer-events as well as the body class, then verify computed styles.
- **New dev server starts on another port:** an old Astro process still owns the requested port. Kill exact old PIDs, clear `.vite` if needed, restart on the intended port, and verify with `curl`.
- **Visual control is too large:** shrink the visible button, not the touch target.
- **Dark mode is flat black:** use a layered blue-gray background and slightly lighter glass cards.
- **Menu is sharp but the page is still readable:** increase backdrop blur/haze slightly; the underlying page should remain recognizable as structure but not compete with navigation.
