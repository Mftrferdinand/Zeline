# Github Readme Design

> Craft GitHub README headers: shields.io badges, logo/tagline layout, and multilingual (i18n) README structure. Use when a user asks to add/restyle badges, arrange the logo + tagline block, adjust badge colors/labels, or add translated READMEs.

Presentation layer of a repo README: badge rows, the logo + tagline header, and
translated READMEs. Iterative visual work — expect many small rounds of
"move this up", "change that color", "make it tighter". On a **protected main
branch every one of these edits still needs branch → PR → CI → squash merge**
(see `github-pr-workflow`), so batch related tweaks into one PR when possible.

## Profile README vs repository README (read first)

Before building anything, be clear which surface the user wants — they conflate
these constantly. The special `username/username` **public** repo renders a
README-only card on the profile (no star/fork/language). An **ordinary** repo
page already shows README + stars + forks + language automatically. The Bio field
is plain short text, no images/HTML/badges. Full breakdown, gh create/delete
commands, and public-visibility rules: `references/profile-readme-vs-repo.md`.

## Animated banners from user artwork

When a user wants a rich/animated header (or sends reference artwork to match),
**animate the original image, don't redraw it** — redrawing loses the art's
character and gets rejected. Layer subtle motion (breathing zoom, halo/eye glow,
blue shimmer) with Pillow and screen-blending. Watch the 10 MB GitHub cap: tune
width / frame count / palette-colors down until ~3–5 MB. Full recipe and the size
convergence example: `references/animate-static-artwork-banner.md`. Ready-to-edit
generator: `scripts/animate_artwork_banner.py`. Always verify n_frames>1 +
is_animated + size, vision-check an extracted PNG frame, and curl the pushed
asset for `HTTP 200 | image/gif`.

**Exception — text wordmarks CAN be rebuilt from scratch.** The don't-redraw rule
protects organic/illustrated art. When the header is a blocky **text wordmark**
and the user asks to recreate it in a new palette/style/animation, rebuilding it
cleanly in Pillow is the right move and what they want. Full workflow (font
sourcing, supersampled 3D extrude + gradient, multi-pass electric-spark animation,
transparent-APNG output, and the vision-check iteration loop):
`references/build-wordmark-logo-from-scratch.md`.

## shields.io badges

Format: `https://img.shields.io/badge/LABEL-MESSAGE-COLOR?style=...&labelColor=...`

- Two-part badge `LABEL-MESSAGE-COLOR`; single-part `LABEL-COLOR` (drops the message).
- URL-encode spaces as `%20`, slashes as `%2F`, non-ASCII (中文) as normal UTF-8 (browsers/GitHub handle it; verify with curl using percent-encoding).
- `style=for-the-badge` = the uniform flat all-caps look; `labelColor=334155` gives the grey left segment.
- Add a logo: `&logo=telegram&logoColor=white`.
- Wrap in `<a href="...">...</a>` for links; put the whole row inside `<p align="center">`.

### CRITICAL: badge text-contrast rule (auto-computed, not settable)

shields.io **auto-picks the message text color from the background brightness** —
you cannot force it. Light background → dark text (`#333`); dark/saturated
background → white text (`#fff`). So white message text on a medium-dark blue
(e.g. `#0A84FF`) can be hard to read, and dark text is invisible on very light.

Fix when a label "doesn't show up": change the *background* color to shift which
text color it auto-selects. Example from real use: `MIT` and `ZELINE.ZEROLINEAR.COM`
were unreadable on `#38BDF8`/`#0A84FF` → switched those badges to lighter
`#7DD3FC`, which flips shields.io to dark text and makes them legible.

Verify the actual rendered text color before/after:
```bash
curl -s "https://img.shields.io/badge/LICENSE-MIT-7DD3FC?style=for-the-badge&labelColor=334155" \
  | grep -oE '<text[^>]*fill="#[0-9a-fA-F]{3,6}"[^>]*>(MIT|LICENSE)'
# fill="#333" => dark text (readable on light bg); fill="#fff" => white text
```
Always curl each badge URL for a `200` and eyeball the fill before committing.

### Dynamic live-data badges (self-updating stats)

shields.io has live GitHub endpoints — no static value to maintain, they refresh
on their own. Use these when the user wants stars/forks/release/language shown
*inside* the README (the ordinary repo page already shows them in the sidebar):

- `https://img.shields.io/github/stars/OWNER/REPO?style=for-the-badge&labelColor=334155&color=7DD3FC`
- `https://img.shields.io/github/forks/OWNER/REPO?...`
- `https://img.shields.io/github/v/release/OWNER/REPO?...`
- `https://img.shields.io/github/languages/top/OWNER/REPO?...`

Wrap each in `<a href>` to its GitHub page (`/stargazers`, `/network/members`,
`/releases`). curl each for HTTP 200 before committing. When adding to an i18n
repo, add the SAME block to every README variant (root + `docs/README.*.md`).

### Multi-row badge blocks

To keep two badge rows tight (vertical gap == horizontal gap) put them in **one**
`<p align="center">` separated by a literal `<br>`, NOT two separate `<p>` blocks
(separate paragraphs add a full paragraph gap). User preference: "padat" / tightly packed.

To **merge two badge rows back into one line**, just delete the literal `<br>`
between them (keep the single `<p>`). To **split into two guaranteed rows**, see below.

**Alternating badge colors:** users often want the badge row to look cohesive with
a 2-color alternating scheme (seen live: "selang seling seperti EN ID", i.e. vivid
`#0A84FF` / dark `#1D4ED8` alternating, and an explicit "jangan ada warna biru muda"
— kill the light `#7DD3FC`). Recolor by swapping the `-COLOR` segment of each badge
URL; do it across ALL i18n variants in one batch (`execute_code` looping `patch`
over the 3 files is fast). Matching the **logo/wordmark fill to one of the badge
colors** (e.g. font = the EN badge blue) is a common cohesion request too.

**Rename a badge label but keep its link/logo:** change only the `LABEL` segment
of the badge URL (e.g. `badge/Telegram-...` → `badge/Community-...`) — leave the
`<a href>` and `&logo=telegram` untouched. Seen live: "ubah jadi Community tapi
link tetap ke telegram."

**Reorder tagline vs badges (hug the badge block):** to move the tagline BELOW
the badges, keep ONE `<p align="center">` and place `<br><strong>tagline</strong>`
as the last children after the badge `<a>`s (not in the logo block). To move it
ABOVE, put it right after the logo. Same-`<p>` + `<br>` keeps it hugging; a
separate `<p>` adds a paragraph gap.

**Animated badges — usually not worth it (trade-off).** A synchronized shine
sweep across the badge row can be built (see `pil-image-animation`: one global
clock, per-badge x-offset, each badge a standalone clickable APNG). BUT
shields.io badges are only ~20px tall, so re-rastering them as animation frames
makes the text visibly softer than crisp static shields, and GIF's 1-bit alpha
adds a dark corner fringe on light theme. Seen live: built it, shipped it, user
then said "jangan pake animasi" and we reverted to static shields.io. Offer the
trade-off explicitly and expect a likely revert; prefer static badges by default.

**BUT the `<br>` only breaks when each intended row is wide enough that GitHub
actually wraps there.** If all badges together fit on one rendered line (common
with small `style=flat`/`flat-square` badges), GitHub keeps everything on ONE row
no matter how many `<br>`s you insert — seen live: user asked for two rows
repeatedly and single-`<p>`+`<br>` kept rendering one row until switched. Decide by
priority: tightness wins → one `<p>`+`<br>`; a guaranteed 2-row split wins → use
**two separate `<p align="center">` blocks** (one per row), accepting the larger gap.

## Header layout (logo + tagline)

- To make a tagline **hug** the logo, put both in the *same* `<p align="center">`
  block separated by `<br>` — separate `<p>` blocks leave a paragraph gap.
- Order the user tends to want: logo → tagline → badge block.
- Don't duplicate the tagline. When restructuring, grep the tagline string
  across ALL README variants afterward — it's easy to leave a stale second copy
  below the badges (happened here in the ID/ZH files but not EN).

## Multilingual (i18n) READMEs

Standard structure — do NOT create a `README.en.md`:
- **Main `README.md` at repo root = the primary/English version.** The `EN`
  language badge links to `README.md` itself.
- Translations live in `docs/README.<lang>.md` (e.g. `docs/README.id.md`, `docs/README.zh.md`).
- Language-switch badge row is identical in every file so switching always works.

### Relative-path fixups in docs/ (easy to miss)

Files under `docs/` are one level down, so paths that work in the root README
must be rewritten in the translations:
- logo `src="assets/..."` → `src="../assets/..."`
- `href="LICENSE"` → `href="../LICENSE"`
- EN badge `href="README.md"` → `href="../README.md"`
- ID/ZH badges → `href="README.id.md"` / `href="README.zh.md"` (same dir, no prefix)

Translate prose only; keep code blocks, commands, file paths, URLs, CLI names,
code identifiers, and the license line UNCHANGED. Fan out translations to
parallel subagents (one per language) for speed, then fix the paths yourself.

## Pitfalls

- **`patch` escape-drift on HTML with quotes.** README headers are full of `"`
  (`<a href="...">`, `src="..."`). When you feed old_string/new_string containing
  those quotes, the patch tool may reject with "Escape-drift detected: literal `\"`"
  because the serialization prefixed the quotes with backslashes. Fix: pass the
  old/new strings with **plain unescaped `"`** — don't hand-escape quotes. Shorten
  the match to a unique fragment that still contains the quotes verbatim
  (e.g. `logo=telegram&logoColor=white"></a>\n  <br>\n  <a href="LICENSE">`) rather
  than pasting a giant multi-line block. Bit us 3× in one session before switching.
- **Verify image-asset CONTENT, not just the filename — filenames lie after a
  rebrand.** Seen live: a repo shipped `zeline-logo.png` and
  `zeline-social-preview.png` whose pixels still read `OLD-BRAND` (old
  pre-rebrand art, only the filename was updated); `zerolinear-logo.png` was the
  only asset actually rendering the current `ZELINE` wordmark. Before embedding
  any logo/banner, vision-check the raw image (`raw.githubusercontent.com/.../asset`)
  to confirm the wordmark matches the current brand. The user WILL catch a
  wrong-brand logo instantly. When you find such stale assets, flag them — they
  poison anyone reusing the repo.
- GitHub file/PR views ALWAYS show binary images (logos) as before/after diff
  panes — that is not a bug and cannot be turned off. Merged PRs cannot be deleted.
- After merging on a protected branch, `git pull` may refuse to fast-forward if
  local diverged; `git reset --hard origin/main` after confirming the PR merged.
- Screenshots the user sends may reflect an OLD commit ("1 minute ago") from
  before your latest merge — verify current file state and tell them to refresh;
  don't re-fix something already fixed.