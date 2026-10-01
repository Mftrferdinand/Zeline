# Mobile Glass Navigation: Sharp Menu, Blurred Page

Use this pattern when a compact dashboard/menu button should open a glass panel while the page behind it becomes softly blurred and desaturated.

## Layering contract

Keep the backdrop as a separate fixed sibling, not as a filter on the page container:

```html
<body>
  <div class="nav-backdrop" aria-hidden="true"></div>
  <header class="nav">
    <details class="nav-dashboard">...</details>
  </header>
  <main>...</main>
</body>
```

Recommended z-index order:

- Main content/background: `z-index: 1`
- Backdrop: `z-index: 40`
- Header and menu: `z-index: 50+`
- Menu panel within header: above the backdrop

Never apply `filter: blur()` to a container that also contains the menu; that blurs the control itself. Use a separate `backdrop-filter` layer so the menu and header remain sharp.

## Blue-white blur, not a black dimmer

```css
.nav-backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(216,224,231,.28);
  backdrop-filter: blur(7px) saturate(82%);
  -webkit-backdrop-filter: blur(7px) saturate(82%);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity .24s ease, visibility .24s ease;
}
body.nav-menu-open { overflow: hidden; }
body.nav-menu-open .nav-backdrop {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
}
```

On mobile, a slightly stronger `blur(8px) saturate(78%)` plus a pale blue-gray haze works well. Avoid a black overlay when the surrounding design uses light blue-gray glass.

## Compact visual control with accessible hit area

A small visual button can still have a comfortable mobile target:

```css
.nav-dashboard summary {
  position: relative;
  width: 30px;
  min-width: 30px;
  min-height: 30px;
}
.nav-dashboard summary::after {
  content: '';
  position: absolute;
  width: 44px;
  height: 44px;
  left: 50%;
  top: 50%;
  transform: translate(-50%,-50%);
}
```

Do not enlarge the visible UI merely to satisfy touch ergonomics; enlarge only the invisible hit target.

## Menu-panel proportions

For a 400–430 px mobile viewport:

- Visible trigger: around `30×30px`
- Panel width: around `180–190px`
- Link text: `11.5–12px`
- Link row: `31–32px` minimum height
- Panel radius: `16–18px`
- Compact padding: `8px`

A small uppercase section label (`NAVIGATION`) and subtle blue dot markers create hierarchy without oversized controls or decorative clutter.

## State synchronization

With `<details>`, synchronize the page state from its `toggle` event:

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
syncMenu();
```

Explicit inline state is a useful defensive fallback for Android WebViews where animation/event timing can make a class-driven overlay appear one frame late or remain visually stale.

## Verification gate

Do not stop at HTTP 200 or source greps. Verify the actual served route:

1. Confirm the process is listening on the intended port; kill stale dev servers that force the new process onto another port.
2. Fetch the live HTML and check for the backdrop/menu sentinels.
3. Use a headless browser at the target mobile viewport.
4. Open the menu through the real control or set `<details>.open = true`, then wait for the toggle state.
5. Read computed state:
   - trigger dimensions
   - panel dimensions
   - backdrop opacity (`1`)
   - computed `backdropFilter` (`blur(...)`)
   - body scroll-lock class
   - menu-link count
6. Capture and inspect a screenshot with the menu open.

A passing screenshot must show the background visibly blurred/faded while the header, trigger, menu panel, and menu text remain sharp.
