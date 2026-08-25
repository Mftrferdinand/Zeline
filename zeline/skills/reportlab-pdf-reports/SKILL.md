---
name: reportlab-pdf-reports
description: "Generate premium multi-page PDF reports, whitepapers, and comparison documents from scratch with Python reportlab — cover page, table of contents, styled comparison tables, callouts, code blocks, running header/footer, and a pypdf verification loop. Use when the user wants a polished, professional, many-page PDF document (version comparison, technical report, whitepaper, dossier) — NOT for editing existing PDFs (use nano-pdf) or business templates like invoices (use document-templater/invoice-generator)."
version: 1.0.0
author: Zeline Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  zeline:
    tags: [PDF, reportlab, report, whitepaper, document, pypdf, comparison]
    related_skills: [document-templater, invoice-generator, ocr-and-documents]
---

# reportlab Premium PDF Reports

Build long, polished, professional PDF documents (technical reports, version
comparisons, whitepapers, dossiers) programmatically with Python `reportlab`,
then verify them with `pypdf`. This is how the "Zeline v0.1.0 vs v0.2.0"
premium comparison report (13+ pages, cover, TOC, comparison tables, glossary)
was produced.

## When to use
- "Make a PDF report / whitepaper / comparison document, premium & professional, many pages."
- Turn research/analysis or a `git diff` between two versions into a formatted multi-page deliverable.
- Any PDF the user will judge on layout polish and iterate on (page count, density, styling).

Not this skill: editing an existing PDF's text → `nano-pdf`; invoices/receipts/quotes → `invoice-generator` / `document-templater`; extracting text from PDFs → `ocr-and-documents`.

## Deps & fonts
- `pip install reportlab pypdf` (both pure-Python, install fine on Termux).
- **Register Unicode TTFs** — reportlab's built-in Helvetica cannot render `中文`, em-dashes `—`, or arrows `→`. Register DejaVu (bundled in Termux at `$PREFIX/share/fonts/TTF/`): `DejaVuSans.ttf` (body), `DejaVuSans-Bold.ttf` (headings), `DejaVuSansMono.ttf` (code), `DejaVuSans-Oblique.ttf` (italic). Use these font names everywhere or non-ASCII glyphs render as tofu boxes.

## The chunked-builder pattern (CRITICAL — avoids write_file timeouts)
A full rich report script is large. Writing it as ONE `write_file` call reliably
**times out the tool stream** (seen live: a single large write stalled and never
delivered). Split the document into part files, each exporting a
`build_partN(story, P, bullet, title, cmp_table, callout, s)` that appends
flowables to `story` and returns it. The main script loads them with
`importlib.util` and calls them in order:

```python
def load(mod, path):
    spec = importlib.util.spec_from_file_location(mod, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
p1 = load('zp1', base+'report_part1.py')   # cover + TOC + intro sections
# ... p2, p3, ...
story = p1.build_part1(story, P, bullet, title, cmp_table, callout, s)
```
Keep each part file under ~8K tokens. Define shared style/helpers
(`P`, `bullet`, `title`, `cmp_table`, `callout`, the `Doc` class) once in the
main script and pass them in. This also makes per-section edits cheap `patch`
calls instead of rewriting the whole document.

## Layout building blocks
- **`BaseDocTemplate` + `Frame` + `PageTemplate(onPage=deco)`** for a running header/footer drawn per page. Gate it on `if d.page > 1:` so the cover stays clean.
- **Cover**: a full-bleed navy `Table` (one cell, fixed `rowHeights`) with centered white title/subtitle paragraphs; `VALIGN=MIDDLE`.
- **TOC**: a plain list of section titles (hand-authored is fine for a one-shot report; reportlab's live TOC needs multi-pass build).
- **Comparison tables**: a helper `cmp_table(rows, headers, widths)` with a header row, alternating `WHITE`/`PALE` row backgrounds, thin grid, `VALIGN=TOP`, `repeatRows=1` so it re-headers across page breaks.
  - **Header-row color (aes preference):** use a LIGHT tint for the header row with dark text, NOT a heavy dark/navy bar. aes rejected the dark header: "di setiap tabel jangan ada tabel hitam kosong sebagai atas tabelnya, lgsg aja tabel" — a solid navy/black header bar reads to her as an empty black strip stuck on top of the table. Style header cells `('BACKGROUND',(0,0),(-1,0), LIGHT)` + `('TEXTCOLOR',(0,0),(-1,0), NAVY)` (e.g. `#EAF3FF` fill, navy text). Apply this to EVERY table helper (`cmp_table`, `catalog`, metrics tables) — she means all of them, not just one.
- **Callout boxes**: single-cell `Table` with a tinted background + colored box border for "in short / note / recommendation" emphasis (blue = info, amber = warning, green = recommendation).
- **Code/command blocks**: a mono paragraph style (`DejaVuSansMono`) with a pale background + border; use `<br/>` for line breaks inside one Paragraph.
- Full copy-paste cookbook (Doc class, all style definitions, cmp_table, callout, catalog, cover): `references/reportlab-report-cookbook.md`.

## User preferences for premium reports (embed these — aes stated them explicitly)
- **No watermark** by default. A diagonal page watermark reads as cheap/draft; only add one if explicitly asked. (User: "jangan ada watermark".)
- **No empty or half-empty pages; no gratuitous `PageBreak()`.** Let content flow densely and continuously — only break where a major section genuinely should start fresh. (User: "setiap pages jangan ada yang kosong, saling sambung gt jangan, langsung aja padat".) Over-using `PageBreak()` after every short section is the #1 cause of near-empty pages.
- **Prefer MORE pages via real content**, not padding — expand with genuine reference sections (detailed tables, glossary, per-item breakdowns, workflows), not whitespace. (User: "yang banyak pages nya".)
- **Explain the concepts, don't just tabulate.** For a technical audience doc, include a "What is X?" / anatomy / glossary section so the report teaches, not just compares.
- Justified body text (`alignment=TA_JUSTIFY`) + a navy/blue palette reads premium.
- **Give every point/paragraph top spacing — don't let items butt against the line above.** aes: "setiapp point baru kasih jarak paragraf dr atas, karena itu nempel banget keliatanya." Set `spaceBefore` on the bullet style (`spaceBefore=5`), the body style (`spaceBefore=3`), and generous `spaceBefore` on headings (H1 ~16, H2 ~13) so each new element visibly breathes. `spaceAfter` alone is not enough — without `spaceBefore` a bullet sits flush under the preceding sentence and looks cramped.
- **Keep heading sizes modest / proportional — don't oversize them.** aes flagged big headings as too large. Section headings (H1) ~14pt, sub-headings (H2) ~11pt, cover title ~22pt (not 19/13/27). Oversized headings look unbalanced against 9–10pt body.

## Sourcing accurate data (don't fabricate figures)
When comparing two versions/tags, pull real numbers from git rather than
guessing: `git diff --stat TAG_A TAG_B | tail -1` (files/insertions/deletions),
`git ls-tree -r TAG --name-only | grep -c '\.py$'` (module counts), read
`pyproject.toml` at each tag for name/version/deps, and grep the tools/skills
modules for actual registered names. Put the exact tag the user asked for in the
title and cover — if they say "0.1.0 → 0.2.0", do NOT silently use a newer tag.

## Verification loop with pypdf (always run before delivering)
Never ship on "the script ran". Read it back and check:
```python
from pypdf import PdfReader; import re
r = PdfReader(path); print('PAGES', len(r.pages))
for i, p in enumerate(r.pages):
    t = p.extract_text().replace(chr(10), ' ')
    body = re.sub(r'(RUNNING HEADER TEXT|FOOTER TEXT|Page \d+)', '', t).strip()
    if len(body) < 60: print('NEARLY EMPTY p', i+1)     # catch blank/half pages
    if 'WRONG_VERSION' in t: print('version leak p', i+1) # catch stale strings after a global find/replace
```
- **Near-empty detection** enforces the "no empty pages" preference automatically.
- **Version/string-leak detection**: after switching the whole doc from one version label to another (e.g. 0.2.1→0.2.0), grep every page for the old string — it hides in table headers and helper defaults (`cmp_table(headers=(...,'v0.2.1'))` bit this live).
- If you can't rasterize pages for a visual check (no poppler/pymupdf, or network install times out), say so honestly and rely on the pypdf text/structure check — do not claim a visual preview you didn't do.

## Delivery
Deliver the finished PDF with a `MEDIA:/abs/path/to/report.pdf` line so Telegram sends it as a file attachment. Name the file descriptively (`Zeline-v0.1.0-vs-v0.2.0-English.pdf`). Offer to expand with more sections rather than assuming the length is final.

### Temporary share link (when the user asks for a "temporary/temp download link")
Prefer `tmpfiles.org` — it worked from Termux when others were down: `curl -sS -F "file=@FILE.pdf" https://tmpfiles.org/api/v1/upload` returns JSON `{"data":{"url":"https://tmpfiles.org/ID/name.pdf"}}`. Give the user that page URL (the `/dl/ID/` variant is the direct-download form). Note: tmpfiles is behind a bot-protection layer, so a bare `curl` re-download returns an HTML challenge page, NOT the PDF — verifying via curl will look like it failed even though the browser link works fine; don't panic-loop on that. As of this session `0x0.st` (uploads disabled), `transfer.sh` (DNS/connect fail), `file.io` and `litterbox.catbox.moe` (Cloudflare/500) were all unavailable — try tmpfiles first. Always also attach the file via `MEDIA:` so the user has it regardless of the host's uptime.
