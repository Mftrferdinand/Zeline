# Portfolio dark mode, compact controls, and local preview hygiene

Use this for Astro/static portfolio work when adding a theme toggle, polishing a compact mobile dashboard menu, or verifying changes through a local dev server on Android/Termux.

## 1. Dark mode: neutral charcoal, not accidental navy

When the user asks to make an existing dark mode darker and “not blue,” changing only the base background is insufficient. Blue ambient orbs, card fills, borders, and backdrop overlays can preserve a strong blue cast.

Recommended neutral palette:

```css
:root[data-theme="dark"] {
  --bg: #101010;
  --surface: #1a1a1a;
  --surface-2: #2a2a2a;
  --ink: #f2f2f2;
  --ink-2: #d8d8d8;
  --muted: #999;
  --line: #2b2b2b;
  --glass: rgba(30,30,30,.68);
  --glass-strong: rgba(42,42,42,.82);
  --glass-border: rgba(255,255,255,.12);
  --accent: #4da6ff; /* reserve blue for intentional accents */
  color-scheme: dark;
}
```

Dark-mode sweep:

- Body: neutral `#101010` with only a faint neutral radial highlight.
- Cards/panels: charcoal glass, not navy glass.
- Borders: low-alpha white, not blue-gray.
- Dim/backdrop layer: neutral near-black alpha.
- Inline chips/tags: low-alpha white neutral surfaces.
- Blue remains only on CTAs, active state, section numbers, or deliberate highlighted words.
- Existing blue ambient orbs must be overridden in dark mode with grayscale + very low opacity, or replaced by neutral white radial glows. Otherwise screenshots still read as blue even when the body token is charcoal.

```css
:root[data-theme="dark"] .orb {
  opacity: .07;
  filter: blur(72px) grayscale(1);
}
:root[data-theme="dark"] .orb {
  background: radial-gradient(circle, rgba(255,255,255,.12), transparent 68%);
}
```

Verify via screenshot, not just CSS tokens: inspect large empty background areas, card surfaces, and the hero. “The token is #101010” does not prove the rendered page has no blue cast.

## 2. Persistent theme toggle without reload flash

Set the theme before first paint in `<head>`:

```html
<script>
  (() => {
    const saved = localStorage.getItem('site-theme');
    const theme = saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = theme;
  })();
</script>
```

Toggle behavior:

1. Read current `document.documentElement.dataset.theme`.
2. Switch between `light` and `dark`.
3. Save to `localStorage`.
4. Update `aria-label`, `aria-pressed`, icon state, and `<meta name="theme-color">`.
5. Preserve the user choice across reload; system preference is only the first-run default.

Use a compact visual control (about 30–32px) next to the dashboard button, but retain a transparent 44×44px pseudo-element hit target on mobile. Moon indicates switching into dark; sun indicates switching back to light.

Functional verification must perform a real `.click()` after the page is complete and assert:

- dataset theme changed;
- localStorage changed;
- `aria-pressed` and `aria-label` changed;
- theme-color changed;
- reload restores the selected theme;
- mobile screenshot remains readable.

## 3. Exact header branding is a release assertion

When the user specifies a joined or punctuation-free wordmark, treat it literally. For example, “Mftrferdinand without the period” means the header DOM must contain exactly `Mftrferdinand` and no `.dot` span. Do not rely on OCR alone; assert the exact header text and absence of the old decoration in served HTML/DOM.

## 4. Never place Chromium profiles inside an Astro/Vite project

A Chromium `--user-data-dir` under the project (for example `.audit/cdp-*`) can create thousands of changing files. Vite/Chokidar may watch those files and hit:

```text
ENOSPC: System limit for number of file watchers reached
```

Symptoms include:

- Astro dev server appears in `ps` but HTTP requests time out;
- high CPU from stale Chromium/Node processes;
- repeated unhandled watcher rejections;
- hot reload starts additional servers on 8082/8083/8084 because the requested port remains occupied.

Durable prevention:

- Put browser profiles outside the repository, e.g. `~/tmp/chromium-audit/<run-id>`.
- Delete temporary profiles after each verification.
- Never launch a long-lived debug Chromium without guaranteed cleanup in `finally`.
- Keep screenshots/logs in `.audit/` if desired, but not the browser profile directory.

Recovery sequence:

1. Inspect the Astro logs for `ENOSPC` before changing application code.
2. Terminate stale Chromium audit profiles and stale Astro process trees explicitly.
3. Remove only temporary browser profile directories.
4. Restart one Astro dev process on the exact requested port.
5. Confirm the startup output says the exact port, then `curl` it for HTTP 200.
6. Do not trust “ready” from an instance that silently fell forward to another port.

## 5. Dashboard menu + theme control coexistence

Keep both controls inside one `.nav-actions` row with a compact 7–8px gap. The menu stays sharp above the full-screen blur backdrop, and the theme button must remain clickable while the menu is closed. The dashboard’s visual trigger and theme toggle should share radius, border weight, and height so the header reads as one system rather than two unrelated widgets.

## Release gate

- Exact header wordmark assertion passes.
- Light and dark builds compile.
- Toggle click and persistence are functionally tested.
- Dark screenshot is neutral charcoal, not navy.
- Dashboard blur still works in both themes.
- No Chromium profile lives under the project directory.
- Exactly one server owns the requested preview port and returns HTTP 200.
