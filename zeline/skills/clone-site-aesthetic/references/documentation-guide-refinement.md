# Documentation-guide refinement notes

Use these notes when reworking a static technical tutorial for the user.

## Routing and verification

- Change the route the user actually opens; an alternate hidden guide route does not count as a homepage redesign.
- Cache-bust changed CSS/JS URLs and render the exact final URL with Chromium before delivery.
- Inspect rendered screenshots as well as HTTP responses.
- Scan generated HTML for accidental line-number prefixes such as `^\d+\|` before shipping. This can happen if line-numbered output from a file-read tool is written back to the page.
- After bulk replacements, scan headings for duplicated word fragments (for example `Configurationuration`).

## User-specific guide UI

- Light documentation system: blue-gray background family based on `#D8E0E7`, accent `#0A84FF`; do not use black code/terminal panels when the user asks for this palette.
- Keep prose in normal reading flow. Glass chips are only for compact metadata/status. Do not turn every inline handle, token example, slash command, or command mention into a blue code bubble.
- Copy buttons are for executable command/config blocks only, not illustrative terminal output. Make Copy high-contrast and provide copied feedback.
- English is the default language. Put an EN/ID selector at upper-right; keep command text, paths, configuration keys, and API values untranslated. Persist choice in localStorage.
- Exact brand: `MftrferdinandDocs` is joined with no visible space. Remove redundant top-right social actions and duplicate footer/source references when an official source callout already exists near the introduction.
