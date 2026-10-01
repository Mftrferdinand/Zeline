# Static Documentation Rebuild & Verification

Use this when restyling or rebuilding a custom static documentation/guide site, especially on Android/Termux or mobile WebViews.

## 1. Lock the actual served route before editing

Do not assume the project root, `site/`, `/guide/`, or a versioned subroute is what the user is viewing.

1. Inspect the live process and its document root.
2. Fetch the exact URL the user opens.
3. Identify the exact HTML, CSS, and JS returned by that URL.
4. Edit that source of truth—or deliberately replace the root page.
5. Never claim a redesign is visible when it only exists under a new hidden route.

If the user says “no change,” treat that as evidence that route/docroot/source selection is wrong until proven otherwise. Do not keep inventing `/guide-vN/` routes or query-string versions as a substitute for fixing the served root.

## 2. Rebuild instead of endlessly patching legacy markup

When requirements change from a landing page to a deep documentation site, stop appending CSS overrides to old generated HTML.

Preferred recovery:

1. Preserve the old page as a backup.
2. Build one clean standalone HTML/CSS/JS implementation.
3. Put the requested information architecture directly in the root route.
4. Retire or redirect obsolete routes so users cannot land on mixed legacy formats.
5. Ensure one visual system controls the navbar, sidebar, content, code blocks, tables, callouts, and FAQ.

Repeated override layers create contradictory selectors, stale branding, duplicated sections, and “desktop has random files/sections” complaints.

## 3. Never write numbered tool-display output back into source

`read_file` output shown in the conversation is prefixed with display line numbers such as `17|`. Those prefixes are not source content.

- Do not copy rendered tool output into `write_file` or string-replacement pipelines.
- Read the real file through a direct filesystem API when scripting, or use targeted `patch` edits.
- After any bulk rewrite, scan served HTML for lines matching `^[0-9]+\|` and render a screenshot. Leaked prefixes are a release-blocking bug.

## 4. English/Indonesian must be real and complete

For mobile WebViews, a JavaScript-only language toggle is not sufficiently robust.

Preferred architecture:

- `/` is the complete English page (default).
- `/id/` is the complete Indonesian page.
- `EN` and `ID` are ordinary `<a href>` links, not buttons that require JavaScript.
- Translate content at build time using explicit marked elements or a structured content source.
- Keep commands, file paths, model IDs, and code blocks unchanged.
- Localize UI labels such as Copy/Salin.

Verification:

1. Fetch `/` and assert `<html lang="en">` plus a known English sentence.
2. Fetch `/id/` and assert `<html lang="id">` plus its Indonesian counterpart.
3. Assert both pages contain reciprocal EN/ID links.
4. Count marked translation elements and fail if any translation key is missing.
5. Browser-test navigation rather than only checking button appearance.

## 5. Mobile navigation must expose the same information architecture

Never solve mobile width by `display:none` on the documentation sidebar when the sidebar is the main menu.

Use:

- A visible hamburger button.
- A fixed off-canvas sidebar/drawer.
- A backdrop that closes the drawer.
- Close-on-link-click.
- `aria-expanded` state on the trigger.
- The same grouped menu and anchors as desktop.

Browser-test the click and verify the body/drawer open state, not just the presence of CSS.

## 6. Platform installation is a chooser, not an Android-only path

When documentation supports multiple environments, present a platform selector before installation:

- Linux/VPS — recommended for 24/7 production.
- macOS — local desktop/development.
- Windows/WSL2 — native or Linux-compatible path.
- Android/Termux — portable, zero-cost experimentation.

Termux is one option, not the universal prerequisite. Explain when to choose each platform and use platform-appropriate commands. Avoid decorative emoji in labels; use typography and SVG where an icon is necessary.

## 7. Inline technical text is not automatically a chip

Do not turn every mention or command into a blue bubble. In prose and ordered steps, items like `@BotFather`, `/newbot`, token examples, and short commands should usually be plain inline text or subtle monospace text with no filled background.

Reserve contained surfaces for:

- Copyable multi-line code blocks.
- Security warnings and important callouts.
- Selected navigation states.
- A small number of high-value metadata badges.

## 8. FAQ pattern

Use native `<details>/<summary>` accordions so FAQ works without JavaScript. A professional pattern includes:

- Numbered rows.
- Clear question hierarchy.
- A small plus/minus indicator.
- Hairline separators rather than a bubble around every answer.
- Eight or more genuinely useful answers covering platform choice, VPS necessity, Android support, migration, config paths, stale changes, gateway silence, and provider switching.

## 9. Cache and server verification

Query strings help only if the browser actually requests the right asset. They do not fix a wrong route or wrong document root.

For stubborn local WebView caching:

- Serve with `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`.
- Add `Pragma: no-cache` and `Expires: 0`.
- Restart the exact server process bound to the port.
- Fetch headers and served asset sentinels afterward.

## 10. Release gate

Before reporting completion:

- Exact root URL renders the new site directly.
- No legacy format is reachable from normal navigation.
- Desktop screenshot passes.
- Mobile screenshot passes.
- Hamburger click is functionally tested.
- EN→ID and ID→EN navigation are functionally tested.
- Every sidebar anchor resolves to a real section.
- Copy controls work and preserve SVG/icon markup when updating labels.
- Long mobile commands wrap without clipping or page-level horizontal overflow.
- No debug line prefixes, duplicate branding, stale footer sources, or duplicate source links remain.
