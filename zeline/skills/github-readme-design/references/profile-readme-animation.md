# GitHub profile README animation

Use this when a user references a visually rich GitHub profile and expects more than plain Markdown prose.

- A profile README lives in a public repository named exactly like the GitHub username, with a root `README.md`.
- Preserve user-supplied copy unless rewriting is requested; add presentation around it.
- Store custom animation under `assets/` and reference it relatively from README.
- Prefer a mobile-readable banner ratio around 2:1–2.3:1. At 3:1, secondary text commonly becomes illegible on a 320–360 px viewport.
- Keep 1–3 short text tiers, high contrast, and dim motion behind text. More source pixels do not fix undersized type at rendered width.
- Verify GIF dimensions, file size, multiple frames, animation flag, clipping, contrast, and mobile readability before push.
- After push, read back README and the asset through GitHub's API before reporting success.

Minimal pattern:

```html
<p align="center">
  <img src="assets/banner.gif" width="100%" alt="Animated profile banner" />
</p>
<h1 align="center">Display Name</h1>
<p align="center"><strong>Short role or tagline.</strong></p>
```

## The three surfaces users confuse (explain this up front)

Users repeatedly ask "why doesn't my README / logo / badges show up on my profile?"
because GitHub has THREE different surfaces that render differently. State the
distinction before building anything:

1. **Profile README** — repo named exactly `username/username`, public. Renders
   the full `README.md` (logo, badges, animation, HTML) at the TOP of the profile
   Overview tab. This is the ONLY way to get a rich visual block on the profile.
   It shows NO stars/forks/language of its own.
2. **Repository page** (`github.com/user/repo`) — shows the repo's README PLUS
   stars, forks, language bar, releases in the sidebar. All in one place.
3. **Pinned card** (on the profile Overview) — shows repo name, description,
   primary language, star count, fork count. It does **NOT** render README
   content, so a project's logo/badges never appear on the pinned card. This is
   normal, not a bug — do not try to "fix" it.

So "I want my README visible with stars/forks/language too" has two clean answers:
pin the repo (card gives stars/forks/lang; repo page gives the styled README), or
build a `username/username` profile README for the visual block. They are additive,
not alternatives.

## Live stats badges (stars/forks/release/language)

shields.io serves dynamic repo-stat badges — verified HTTP 200, auto-updating:

```
https://img.shields.io/github/stars/OWNER/REPO?style=for-the-badge&labelColor=334155&color=7DD3FC
https://img.shields.io/github/forks/OWNER/REPO?style=for-the-badge&labelColor=334155&color=7DD3FC
https://img.shields.io/github/v/release/OWNER/REPO?style=for-the-badge&labelColor=334155&color=0A84FF
https://img.shields.io/github/languages/top/OWNER/REPO?style=for-the-badge&labelColor=334155&color=1D4ED8
```

Put them in one extra `<p align="center">` row after the existing badge block.
On a protected `main` this is still docs → branch → PR → CI → squash merge, and
mirror the same row into every i18n README (`docs/README.<lang>.md`) for consistency.

## CRITICAL: filename ≠ content — vision-verify brand assets before embedding

An image asset's FILENAME does not guarantee its CONTENT. In one repo the files
`zeline-logo.png` and `zeline-social-preview.png` still rendered the OLD brand
text "OLD-BRAND" (renamed on disk during a rebrand, but the pixels were never
regenerated). Embedding `zeline-logo.png` silently shipped the wrong brand to the
public profile; the user caught it, not the pre-push checks.

Rule: before embedding ANY logo/banner into a public brand-sensitive README,
`vision_analyze` the actual image and confirm the visible text matches the
intended brand. Never trust the filename. When multiple candidate assets exist,
check all of them and pick the one whose PIXELS say the right word (here
`zerolinear-logo.png` was the clean "ZELINE AGENTIC AI" wordmark). Flag any
mis-branded assets still living in the repo so they can be regenerated/removed.

## Forcing badge rows: `<br>` is a HINT, separate `<p>` blocks are a GUARANTEE

`<br>` inside a single `<p align="center">` does NOT reliably wrap badges to a new
line. GitHub centers the whole paragraph and, when the badges are short enough to
fit the viewport width, packs them onto ONE visual line and ignores the `<br>`.
This bites when the user explicitly asks for "2 baris" / two rows and you keep
shipping what looks like one row.

- To GUARANTEE N rows, use N separate `<p align="center">…</p>` blocks — one per
  row. Each paragraph is its own centered line no matter how short the badges are.
- `<br>` only visibly wraps when the combined badge width already exceeds the
  render width; don't rely on it for intentional grouping.

## Badge size ladder (smallest → largest)

When the user says "perkecil" / make badges smaller, step DOWN this ladder; the
style token is the lever, not the text:
`style=flat` (smallest) → `style=flat-square` → `style=for-the-badge` (tallest,
all-caps, widest). `for-the-badge` also uppercases the label/message; `flat` keeps
your literal casing. Shorten the message text too (`Docs` vs `DOCS-ZELINE.ZEROLINEAR.COM`)
to reduce width. Re-verify each changed badge URL is HTTP 200 after editing.

## Cross-repo asset references

When the profile repo (`username/username`) is separate from where the brand
asset lives, reference the asset by absolute raw URL, not a relative path:
`https://raw.githubusercontent.com/OWNER/REPO/main/assets/<file>` — verify HTTP 200
before committing. Relative `assets/...` only works when the file is in the same repo.

## Iteration is expected and cheap — including full teardown

Profile/README visual work is highly iterative and taste-driven. Expect the user
to flip-flop ("bikin", "hapus aja", "kembaliin kaya tadi", "coba yang ini"). Treat
each as a small, reversible step: `gh repo delete` a throwaway profile repo without
ceremony, `git revert` a merged docs PR (then its own branch→PR→CI→merge on
protected main), regenerate a banner with tweaked params. Don't over-engineer the
first version; ship a clean small version fast and adjust from real screenshots.
