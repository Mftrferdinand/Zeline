# Static Guide: Live-Route, Responsive Navigation, and Bilingual QA

Use this checklist when rebuilding an existing static documentation site, especially when the user says they see no change.

## 1. Identify the exact user-visible route before editing

Do not assume the project root, a newly created subfolder, or a versioned URL is what the user has open.

1. Inspect the running server command and its served directory.
2. Fetch the exact URL the user is viewing.
3. Record its resolved document, CSS URL, JS URL, title, and one visible text sentinel.
4. Edit that document/template first. Do not build a parallel `/guide-vN/` route unless explicitly requested.
5. After editing, fetch the same exact URL and prove the new sentinel and asset versions are present.

If the user reports “no change,” treat route mismatch as a first-class possibility. Do not repeatedly blame browser cache.

## 2. Prefer a clean rebuild over stacking overrides

When legacy HTML/CSS has multiple generators, themes, or contradictory overrides:

- Build one coherent page/component tree and one stylesheet.
- Replace the active route atomically after the new page is ready.
- Remove or stop linking obsolete sections rather than hiding them behind more CSS.
- Never copy line-numbered `read_file` output into source. Tool output such as `17|<div>` is display metadata, not file content.
- Verify the served DOM contains no leaked `^\d+\|` prefixes.

## 3. Documentation navigation must work on desktop and mobile

Desktop:
- Sticky grouped sidebar with compact links and active-section tracking.
- Every sidebar `href="#id"` must resolve to an element with that ID.
- Sidebar must scroll independently if its content exceeds viewport height.

Mobile:
- Never merely hide the sidebar.
- Add a visible hamburger button, off-canvas drawer, backdrop, and close-on-link behavior.
- Keep brand and EN/ID control visible in the top bar.
- Test at approximately 390–430 px width.
- Wrap long commands on mobile or otherwise ensure they do not clip the page or cause horizontal body overflow.

## 4. English-first bilingual implementation

Do not mix English UI with untranslated Indonesian prose.

Recommended pattern:

```html
<h2 data-i18n="installTitle">Install Zeline</h2>
<button data-lang="en">EN</button>
<button data-lang="id">ID</button>
```

```js
const copy = {
  installTitle: ['Install Zeline', 'Instal Zeline']
};
function applyLanguage(lang) {
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const pair = copy[el.dataset.i18n];
    if (pair) el.textContent = pair[lang === 'id' ? 1 : 0];
  });
}
```

Rules:
- Render complete English prose in HTML by default.
- Give every translatable visible node a stable `data-i18n` key.
- Translate headings, body paragraphs, navigation, notices, list items, and control labels—not only the hero.
- Never translate or mutate commands, code blocks, paths, model IDs, tokens, or config keys.
- Persist language in `localStorage`, but default to English when no preference exists.
- Clicking EN and ID must update `document.documentElement.lang`, button active state, and visible copy.

## 5. Typography: not every technical token is a chip

Use visual containers only for meaningful components:
- Multi-line copyable scripts
- Important warnings and notes
- Status indicators
- Small hero feature metadata

Keep ordinary inline references such as `@BotFather`, `/newbot`, token examples, commands mentioned in prose, paths, and user IDs as plain inline text. If distinction is needed, use a subtle monospace color with no background, border, radius, or padding.

## 6. Interaction verification is mandatory

A screenshot proves appearance, not function. Verify both.

Visual checks:
- Desktop screenshot
- Mobile screenshot
- No horizontal page overflow
- No legacy/duplicate layouts
- Exact brand rendering

Functional browser checks (CDP, Playwright, Puppeteer, or equivalent):
- Click ID and assert translated text plus `html[lang="id"]`.
- Click EN and assert English text plus `html[lang="en"]`.
- Click mobile hamburger and assert drawer open state / `aria-expanded="true"`.
- Click a sidebar link and assert the drawer closes on mobile.
- Click Copy and verify the copied/c success state where clipboard access is available.

## 7. Cache and server delivery

Cache-bust shared CSS and JS URLs after each visible release. If this is a local iterative preview, a no-cache server is useful:

```python
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial

class NoCache(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

ThreadingHTTPServer(
    ('127.0.0.1', 8089),
    partial(NoCache, directory='/absolute/path/to/site')
).serve_forever()
```

This does not replace route verification. First prove the correct route is being served, then address caching.

## 8. Android/WebView fallback for EN/ID

If the user reports that an in-place EN/ID JavaScript toggle does not work, stop layering more click handlers onto the same control. Build two static language routes:

- `/` renders complete English HTML.
- `/id/` renders complete Indonesian HTML.
- EN and ID are ordinary `<a href>` links, not JavaScript-only buttons.
- Commands, paths, config keys, model IDs, and code blocks remain identical.
- Both routes use the same cache-busted CSS/JS assets.
- JavaScript remains only for nonessential enhancements such as Copy feedback and the mobile drawer.

Verify the fallback through the served DOM, not source assumptions: fetch both routes and assert `html[lang]`, representative translated body text, reciprocal EN/ID links, and translated Copy labels. Route-based language switching remains functional even if localStorage or event handlers fail in the user's WebView.

## 9. Platform selection is a content model, not an Android-only section

Do not present Termux as the only installation path. Build a compact platform chooser covering at least:

- Linux/VPS — always-on gateway and production automation
- macOS — local desktop/development
- Windows/WSL2 — native or Linux-compatible workflow
- Android/Termux — portable learning and lightweight automation

Each option should explain its use case and include only verified installation commands. Termux is one option, not the architecture of the whole guide. Follow the selector with practical advice: experiment on Termux, work locally on a computer, and use a VPS for independent 24/7 operation.

## 10. Premium FAQ without JavaScript dependency

Use native `<details><summary>` accordions so questions remain functional when JavaScript is unavailable. A polished FAQ should have:

- A concise intro and 6–10 substantive questions
- Numbered rows with a small expand/collapse indicator
- Clear answers about platform choice, VPS requirements, migration, configuration paths, gateway restarts, silent bots, and provider changes
- English and Indonesian answers generated statically for their respective routes
- Thin dividers and restrained blue accents rather than a bubble around every line

Verify item count in both DOMs, open one native `<details>` item in a real browser, scroll it into view, and inspect a mobile screenshot. Direct `#faq` screenshot capture can race page loading; load normally, wait, scroll via the browser DOM, open an item, then capture.

## Completion gate

Do not report success until all are true:

- Exact user-visible root URL serves the new DOM directly or intentionally redirects once.
- CSS and JS sent by that route contain the release sentinel.
- All sidebar anchors resolve.
- Desktop and mobile screenshots are clean.
- Mobile menu interaction works.
- EN → ID → EN interaction works and representative body prose changes.
- Inline technical mentions are not rendered as decorative bubbles.
- No duplicate bottom source links when an official-source callout already exists above.
