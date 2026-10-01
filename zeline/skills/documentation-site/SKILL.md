---
name: documentation-site
description: Build and deploy MkDocs Material documentation sites with custom CSS theming, social links, hero sections, and grid-card navigation. Covers mkdocs.yml config, markdown frontmatter, build, and Cloudflare Pages deployment.
category: productivity
trigger: user wants to create a documentation site, guide, or knowledge base
---

# Documentation Site — MkDocs Material

Build a branded documentation site using MkDocs Material theme. Used for personal guides, project docs, and knowledge bases.

## Quick Start

```bash
pip install mkdocs mkdocs-material
mkdirdocs new .
```

## mkdocs.yml — Key Sections

### Theme & Palette

```yaml
theme:
  name: material
  language: id
  palette:
    - scheme: slate        # dark mode
      primary: custom      # uses --md-primary-fg-color
      accent: cyan
    - scheme: default      # light mode
      primary: custom
      accent: cyan
  font:
    text: Inter
    code: JetBrains Mono
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.top
    - navigation.indexes    # index.md shows as section landing
    - search.suggest
    - search.highlight
    - content.code.copy
```

### Custom Colors (extra.css)

Put in `docs/assets/extra.css`.

```css
:root {
  --md-primary-fg-color: #6C63FF;           /* primary brand */
  --md-primary-fg-color--light: #8B85FF;
  --md-primary-fg-color--dark: #4A42E8;
  --md-accent-fg-color: #00D4AA;
}
```

### Social Links

```yaml
extra:
  social:
    - icon: fontawesome/brands/github
      name: GitHub
      link: https://github.com/username
    - icon: fontawesome/brands/x-twitter
      name: Twitter
      link: https://x.com/username
    - icon: fontawesome/brands/instagram
      name: Instagram
      link: https://instagram.com/username
    - icon: fontawesome/brands/telegram
      name: Telegram
      link: https://t.me/username
    - icon: fontawesome/brands/discord
      name: Discord
      link: https://discord.gg/invite
```

### Navigation with Section Indexes

```yaml
nav:
  - Beranda: index.md
  - Section Name:
    - section/index.md       # landing page for section
    - Page 1: section/page1.md
    - Page 2: section/page2.md
```

## Markdown Frontmatter

Hide navigation and TOC on landing pages:

```yaml
---
hide:
  - navigation
  - toc
---
```

## Homepage Hero Section

Wrap in a div with class `hero` and add custom CSS in `extra.css`:

```html
<div class="hero">

# Site Title

**Tagline** — Description.

<div class="stats">
  <div class="stat">
    <div class="stat-num">92</div>
    <div class="stat-label">Skills</div>
  </div>
</div>

<div class="social-badges">
  <a href="https://github.com/..." style="border-color:#333;color:#fff;background:#222">GitHub</a>
</div>

</div>
```

### Grid Cards (for navigation)

```html
<div class="grid">
  <a href="path/" class="grid-card" style="text-decoration:none;color:inherit">
    <h3>⚙️ Title</h3>
    <p>Description text here.</p>
  </a>
</div>
```

### extra.css for Hero + Grid

```css
.md-typeset .hero { text-align:center; padding:3rem 0; }
.md-typeset .hero h1 {
  font-size:2.5rem; font-weight:800;
  background:linear-gradient(135deg, #6C63FF 0%, #00D4AA 100%);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
}
.md-typeset .hero .stats { display:flex; justify-content:center; gap:2rem; margin-top:2rem; }
.md-typeset .hero .stat-num { font-size:1.8rem; font-weight:700; color:#6C63FF; }
.md-typeset .social-badges { display:flex; flex-wrap:wrap; gap:0.6rem; margin:1rem 0; }
.md-typeset .social-badges a {
  display:inline-flex; align-items:center; gap:0.4rem;
  padding:0.4rem 1rem; border-radius:2rem; font-size:0.85rem; font-weight:600;
  border:1px solid; transition:all 0.2s;
}
.md-typeset .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:1rem; }
.md-typeset .grid-card {
  padding:1.2rem; border-radius:12px; border:1px solid rgba(255,255,255,0.1);
  background:rgba(255,255,255,0.03); transition:all 0.2s;
}
.md-typeset .grid-card:hover { border-color:#6C63FF; transform:translateY(-2px); }
```

## Build & Serve

```bash
mkdocs build                  # → site/ directory
```

A long warning about "MkDocs 2.0 backward-incompatible changes" may print — this is informational only from the Material theme team and does not block the build.

### Serve on Termux

For environments where `mkdocs serve` is heavy or unavailable (Termux):

```bash
cd /path/to/site && python3 -m http.server 8081 --directory site
```

This serves the pre-built `site/` folder on `http://localhost:8081`.

Health check:
```bash
curl -sI http://localhost:8081  # → 200 OK
```

### ⚠️ Custom Script Pipeline (Site/ Overwrite Trap)

If the project has **custom Python scripts** that generate HTML directly into `site/` (e.g., `expand_docs_v32.py`, `translate_id_v32.py`, `add_parity_v34.py`), **do NOT use `mkdocs serve`** — it cleans `site/` and rebuilds from `docs/`, destroying all custom changes.

**Use the project's custom static server instead** (e.g., `no_cache_server.py`):

```bash
kill <mkdocs-pid>         # Kill mkdocs serve first
python3 no_cache_server.py # Start the static server
```

If `site/` was already overwritten, re-run the custom scripts in order to regenerate.

See `references/custom-script-pipeline.md` for the full workflow, detection, and recovery steps.

### Pitfalls on Termux

1. **`pymdownx.emoji` crashes on Python 3.13.** `mkdocs build` aborts with `TypeError: unsupported callable` from `inspect.getfullargspec` in `pymdownx/emoji.py`. Fix: **remove** the `pymdownx.emoji` entry entirely from `markdown_extensions` in `mkdocs.yml`. Emoji shortcodes like `:smile:` won't render, but Unicode emoji in markdown work fine without the extension.

2. **YAML `!!python/name:` tags need quoting.** Under strict YAML parsing (which `write_file` linter uses), unquoted `!!python/name:material.extensions.emoji.twemoji` causes `YAMLError: could not determine a constructor`. Wrap in quotes: `"!!python/name:material.extensions.emoji.twemoji"`. Better yet, skip `pymdownx.emoji` entirely (see pitfall #1).

3. **Nav paths must be full relative paths.** A `nav:` entry like `- sosial.md` fails with `WARNING - A reference to 'sosial.md' is included in the 'nav' configuration, which is not found`. Pages exist in docs dir but aren't in nav. Always use the full path from `docs/` root with an explicit title: `- Sosial: profil/sosial.md`.

4. **Serve with `background=true`.** `python3 -m http.server 8081 --directory site &` fails in foreground terminal with "Foreground command uses '&' backgrounding". Use `terminal(background=true)` to start the server, then `curl -sI http://localhost:8081` to health-check.

5. **Always use a venv.** `pip install` into the system Python on Termux can conflict. Create `python3 -m venv venv` and `source venv/bin/activate` before `pip install mkdocs mkdocs-material` and before every `mkdocs build`.

6. **Build warnings about MkDocs 2.0 are harmless.** Material for MkDocs prints a long warning about upcoming MkDocs 2.0 incompatibility. This is informational only — the build still succeeds.

## Deploy to Cloudflare Pages

1. Push `site/` folder or whole repo to GitHub
2. Cloudflare Dashboard → Workers & Pages → Create → Pages
3. Connect GitHub repo (or Upload assets for static)
4. Build command: `mkdocs build`
5. Output dir: `site`

## Profile Page Structure

When a personal site includes an author profile:

```
docs/
├── index.md          # Hero + grid cards
├── assets/
│   └── extra.css     # Custom styling
├── profil/
│   ├── index.md      # Name, aliases
│   ├── sosial.md     # Social links, groups, repos
│   └── stack.md      # Device, agent, trading, cloud setup
├── setup/
│   ├── index.md      # Section landing
│   ├── termux.md
│   └── zeline.md
└── ...
```

## Reference Files

- `references/custom-static-site.md` — Full pattern for building a custom animated static site (no framework): generate.py skeleton, SVG icon dict, CSS animation toolkit, relative path depth calc, 3D cube background, card tilt effect, translate toggle (ID/EN), markdown-to-HTML converter, delegation pattern for large sites, color theme preferences, professional tone rules, deploy to Cloudflare Pages
- `references/content-scraping.md` — Scraping MkDocs/HTML reference sites, HTML→markdown conversion, and Indonesian slang cleaning (`clean_slang()` regex pattern with ordering rules)
- `references/static-guide-live-route-and-i18n.md` — Required QA for static guide rebuilds: prove the exact user-visible route before editing, avoid parallel version folders and stacked legacy overrides, build a mobile off-canvas menu instead of hiding navigation, implement complete English-first EN/ID translation, keep ordinary inline technical text out of decorative chips, and verify interactions in a real browser rather than relying on screenshots alone.

### Solid Brand Logos (not transparent)

**Critical lesson (2026-07-13, v6):** User explicitly rejected transparent/logo-only brand icons. Said: "logo logo gpt gemini telegram Twitter Instagram jangan transparant gunakan warna asli dari aplikasi tersebut, buat jauh lebih menarik dan profesional".

Brand logos MUST have **solid background with the real brand color**, not transparent borders or glass effects:

```css
/* WRONG — transparent, looks cheap to this user */
.brand-openai { background: transparent; border: 1px solid rgba(255,255,255,.08); }
.brand-openai svg { color: #60a5fa; }

/* CORRECT — solid brand color background */
.brand-openai { background: #10a37f; } .brand-openai svg { color: #fff; }
.brand-google { background: #4285f4; } .brand-google svg { color: #fff; }
.brand-nvidia { background: #76b900; } .brand-nvidia svg { color: #000; }
.brand-meta { background: #0668e1; } .brand-meta svg { color: #fff; }
.brand-anthropic { background: #d97757; } .brand-anthropic svg { color: #fff; }
.brand-deepseek { background: #4d6bfe; } .brand-deepseek svg { color: #fff; }
.brand-mistral { background: #ff7000; } .brand-mistral svg { color: #fff; }

/* Social — solid brand colors, no transparency */
.social-github { background: #24292e; } .social-github svg { color: #fff; }
.social-twitter { background: #000; } .social-twitter svg { color: #fff; }
.social-instagram { background: linear-gradient(135deg, #f09433, #e6683c, #dc2743, #cc2366, #bc1888); } .social-instagram svg { color: #fff; }
.social-telegram { background: #0088cc; } .social-telegram svg { color: #fff; }
```

Same rule applies to social icons in footer — give each `<a>` a class and a solid background color.

### Mobile Horizontal Overflow Fix

**Critical lesson (2026-07-13):** User reported content "terpotong di bagian kiri dan kanan, sangat sangat lebar ke samping" on Android phone. Root cause: missing `overflow-x: hidden` on `html`/`body`, and tables/code blocks without scroll containers.

Required CSS for mobile-first sites:
```css
html { overflow-x: hidden; max-width: 100vw; }
body { overflow-x: hidden; max-width: 100vw; }
/* Tables must scroll horizontally on mobile */
.prose table { display: block; overflow-x: auto; white-space: nowrap; max-width: 100%; -webkit-overflow-scrolling: touch; }
/* Code blocks too */
.prose pre { overflow-x: auto; max-width: 100%; }
/* Viewport meta must cap zoom */
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
```

### Page Title Bar (persistent section label)

User requested: "di setiap pilihan menu, ada judul di pojok kiri atas sesuai nama menu nya". A fixed bar below the nav showing the current section name + breadcrumb:

```html
<div class="page-title-bar">
  <h2>Section Name</h2>
  <div class="breadcrumb"><a href="../index.html">Home</a> / Section / Page</div>
</div>
```
```css
.page-title-bar { position: fixed; top: var(--nav-h); left: 0; right: 0; z-index: 90; }
```

### Lightweight 3D Animation (performance)

User wants "animasi menarik tetapi ringan". Keep particle count to 15 max (not 30+). Two glow orbs is enough. Card tilt effect uses `perspective(600px)` not `1000px`. No canvas-based 3D — pure CSS transforms only.

## User Preference: Pre-Built Templates Over MkDocs

**Critical lesson (2026-07-04):** User (aes/icibos) rejected MkDocs-based documentation for personal branding sites, saying "template nya jelek" and "struktur nya jelek euy". For personal landing pages, portfolio sites, or community hubs:

**Critical lesson (2026-07-13, SECOND confirmation):** User rejected MkDocs output AGAIN with stronger language: "jelek banget gada animasi full emoji lagi jelek". Specific complaints: (1) no animations, (2) emoji used in grid card icons, (3) overall look not premium enough. User then requested: "jangan ada 1 pun emoji, buat sebagus mungkin gapapa lama yang penting bagus". This is now a **confirmed pattern, not a one-off** — MkDocs Material output gets rejected every time for this user's personal/brand sites.

### Decision Flow (updated 2026-07-13)

```
User wants a personal/landing/brand site or "bikin kayak [reference site]"
├── "dokumentasi" / "guide" / "tutorial" → MkDocs is OK (content-focused, not visual)
├── "bikin kayak [soul-guide / other slick site]" → DO NOT use MkDocs. Build custom HTML/CSS/JS.
│    └── User wants: animated bg, particles, scroll reveal, gradient text, SVG icons, zero emoji
└── "landing page" / "portfolio" / "personal site" / "branding" / "komunitas" / "template"
    ├── "astropaper" / "astro" → use astro-paper (satnaing/astro-paper, ⭐4786)
    ├── "astro landing" / "web3" / "landing" → use astro-landing-page (mhyfritz/astro-landing-page, ⭐674)
    ├── "nextjs" / "next" → use desgy-nextjs (GetNextjsTemplates/desgy-nextjs-tailwind-landing-page-template-free)
    └── "web3 blockchain" / "3d" / "futuristic" → use web3-blockchain template (Omkarghare8)
```

### Custom Static Site Pattern (when MkDocs is rejected)

When the user rejects MkDocs output and wants a polished, animated site, build a **custom static site** with pure HTML/CSS/JS — no framework, no build step. See `references/custom-static-site.md` for the full reusable pattern (generate.py script, shared partials, SVG icon set, CSS animation toolkit).

Key rules:
1. **ZERO emoji.** Use inline SVG icons for ALL visual elements (cards, nav, social links). Define a Python dict of SVG path strings and inject them into HTML.
2. **Animations required.** Animated gradient background orbs, floating particles, scroll-triggered reveal (IntersectionObserver), stat counter animation. Users explicitly request "sebagus mungkin" — static pages feel cheap to this user.
3. **Python generate.py pattern.** Write a single Python script that holds all page content as strings, uses shared `nav_html()` / `footer_html()` / `page()` functions, and writes all HTML files to `site/`. This is faster and more maintainable than hand-writing 13+ HTML files.
4. **Relative path depth calc.** `depth = len([p for p in path.replace("index.html","").strip("/").split("/") if p])` → prefix = `"../" * depth`. Get this wrong and CSS/JS won't load in subdirectory pages.
5. **Serve with `background=true`** on Termux: `cd site && python3 -m http.server 8081`.

### Template Reference

| Template | Stars | Type | Use Case |
|---|---|---|---|
| [astro-paper](https://github.com/satnaing/astro-paper) | ⭐4786 | Astro + Tailwind | Blog + portfolio, SEO-friendly |
| [astro-landing-page](https://github.com/mhyfritz/astro-landing-page) | ⭐674 | Astro + Tailwind | Landing page, dark/light, responsive |
| [desgy-nextjs](https://github.com/GetNextjsTemplates/desgy-nextjs-tailwind-landing-page-template-free) | ⭐30 | Next.js | Landing page, login/signup pages |
| [verve](https://github.com/Shreyas-29/verve) | ⭐30 | Next.js | SaaS landing page, modern |
| [web3-blockchain-v5](https://github.com/Omkarghare8/web3-blockchain-landing-page-template-v5) | ⭐1 | HTML/CSS | Web3 3D futuristic landing |

### Clone & Run

```bash
git clone https://github.com/USER/REPO.git project-name
cd project-name
npm install
npm run dev     # runs on localhost:4321 (Astro) or :3000 (Next.js)
```

### Content Depth Requirement

**Critical lesson (2026-07-13, v7):** User was furious about shallow content: "KENAPA ISI NYA TIDAK ADA?? KEMANA CODING PEMBAHASAN NYA KENAPA ISINYA KOSONG SEMUA". Pages with 3-4 bullet points or 2-line summaries are unacceptable. Each content page MUST be 800-1500 words minimum with:
- Multiple H2/H3 sections with real explanations
- Real code examples in fenced blocks (not pseudocode)
- Detailed tables with actual data (pricing, specs, comparisons)
- Step-by-step instructions
- Real-world examples and use cases
- Best practices with reasoning

Bullet points are NOT content. A page that says "Zero-shot: berikan instruksi tanpa contoh" is useless. A page that explains zero-shot with a 150-word explanation + code example + when to use + output comparison IS content.

## Anti-Fabrication Rule for Documentation Content

**CRITICAL lesson (2026-07-13):** User caught fabricated documentation content — invented CLI commands (`npm install -g zeline`), made-up config structures, and fake API patterns that don't exist in the real product. User said: "kenapa kamu output nya tanpa sumber? kenapa ga langsung dari zeline guide install nya?" and "buat output Resmi dr zeline".

**Rule:** When building a documentation site for a real product (Zeline, any framework, any tool):
1. **Load the product's available skill first** (discover its exact name with the skill listing tool) to get REAL commands, config paths, and API patterns. Do not assume a skill named `zeline` exists.
2. **Never invent CLI commands.** If you don't know the exact install command, look it up — don't guess `npm install -g X` when the real installer is `curl -fsSL https://.../install.sh | bash`.
3. **Never fabricate config structures.** Real `config.yaml` keys, real env var names, real file paths. Check the skill/docs source.
4. **Never make up provider tables, model lists, or feature lists.** Pull from the real source.
5. **If a skill exists for the product, USE IT.** Cross-check commands and paths against the current repository and official documentation; copied skill text is not proof of an API.
6. **Link to official docs** (e.g. `zeline.zerolinear.com/docs`) so the user can verify.

**Symptom of violation:** User says "tanpa sumber" or "kenapa ga langsung dari [product] install nya?" — you fabricated content instead of pulling from the real source.

## GitHub Docs Dark Theme Pattern

User explicitly liked the GitHub Docs dark aesthetic. This is a valid design reference for documentation sites (distinct from MkDocs Material). Build custom HTML/CSS/JS — no framework.

### Color Palette (Dark Mode)
```css
--bg:#0d1117; --bg2:#161b22; --bg3:#21262d;
--surface:#161b22; --surface2:#1c2128;
--border:#30363d; --border-light:#484f58;
--text:#e6edf3; --text-dim:#8b949e; --text-bright:#f0f6fc;
--blue:#58a6ff; --blue-dark:#1f6feb;
--green:#3fb950; --yellow:#d29922; --red:#f85149; --purple:#bc8cff;
```

### Light Mode Override (GitHub Light)
```css
body.light {
  --bg:#f6f8fa; --bg2:#ffffff; --bg3:#e8eaed;
  --surface:#ffffff; --surface2:#f0f2f5;
  --border:#d0d7de; --border-light:#afb8c1;
  --text:#1f2328; --text-dim:#656d76; --text-bright:#0a0c10;
  --blue:#0969da; --blue-dark:#218bff;
  --green:#1a7f37; --yellow:#9a6700; --red:#cf222e; --purple:#8250df;
}
```

### Key Components
- **Navbar:** Fixed top, 56px height, brand text (split-colored), nav links, search input, lang/theme toggles
- **Sidebar:** 270px sticky, collapsible sections, active link tracking on scroll
- **Section cards:** `border-radius:10px`, hover border-light, `scroll-margin-top:80px` for anchor offset
- **Code blocks:** Header bar with language label + copy button, `JetBrains Mono` font
- **Terminal mockups:** macOS-style dots (red/yellow/green), dark bg `#010409`
- **Callouts:** tip (green), warn (yellow), danger (red), info (blue) — icon + title + body
- **Accordion:** Click header to expand/collapse, CSS `max-height` transition
- **Tab pills:** `data-tab` attribute matching, pill active state with `--blue-dark` bg
- **Progress bar:** Fixed 2px bar at top tracking scroll position
- **Back-to-top:** Fixed bottom-right, appears after 400px scroll

### User Color Preferences (confirmed 2026-07-13)
- **Section headings (`<h2>`):** Yellow (`color:var(--yellow)`) in BOTH dark and light mode
- **Navbar brand:** Split-colored — first word yellow, second word white (`--text-bright`)
- **Hero banner title:** Split-colored — first word blue (`--blue`), second word white
- **All buttons in navbar must match height** (32px for lang + theme toggles)
- **No space in brand name:** "ZelineGuide" not "Zeline Guide"

## Language Toggle (EN/ID)

User requested bilingual support with English as the PRIMARY language. Pattern:

```html
<!-- Navbar button -->
<button class="lang-toggle">
  <span class="lang-en active">EN</span><span class="lang-sep">|</span><span class="lang-id">ID</span>
</button>
```

```html
<!-- Content elements with data-en -->
<p data-en="English version of this text">Versi Indonesia dari teks ini</p>
```

```js
// JS: swap textContent between data-en and data-original
function applyLang(lang) {
  if (lang === 'en') {
    document.querySelectorAll('[data-en]').forEach(el => {
      if (!el.getAttribute('data-original')) el.setAttribute('data-original', el.textContent);
      el.textContent = el.getAttribute('data-en');
    });
  } else {
    document.querySelectorAll('[data-en]').forEach(el => {
      var orig = el.getAttribute('data-original');
      if (orig) el.textContent = orig;
    });
  }
  localStorage.setItem('zeline-lang', lang);
}
```

**Rules:**
- English is DEFAULT (savedLang = 'en')
- Toggle saves to `localStorage` key `zeline-lang`
- Active language gets `--blue-dark` background pill
- Only translate `<p>` descriptions and key UI text — code blocks, commands, and config stay unchanged

## Theme Toggle (Dark/Light)

```html
<button class="theme-toggle">
  <svg class="icon-sun"><!-- sun icon --></svg>
  <svg class="icon-moon"><!-- moon icon --></svg>
</button>
```

```css
.theme-toggle .icon-sun { display: none; }
.theme-toggle .icon-moon { display: block; }
body.light .theme-toggle .icon-sun { display: block; }
body.light .theme-toggle .icon-moon { display: none; }
```

```js
btn.addEventListener('click', () => {
  document.body.classList.toggle('light');
  localStorage.setItem('zeline-theme', document.body.classList.contains('light') ? 'light' : 'dark');
});
```

**Rules:**
- Dark mode is DEFAULT
- Light mode uses `body.light` class + CSS variable overrides
- All colors via CSS variables — NO hardcoded colors in component styles
- Saved to `localStorage` key `zeline-theme`

## Large File Write Timeout Workaround

**Pitfall:** `write_file` with content >15KB causes stream timeout on Termux. The tool call is delivered partially or not at all.

**Workaround:** Write a small skeleton file with placeholder comments, then `patch` each section into its placeholder:
```html
<main class="main">
<!--CONTENT_PLACEHOLDER-->
</main>
```
Then:
```
patch_file(path='site/index.html', old_text='<!--CONTENT_PLACEHOLDER-->', new_text='<section>...</section><!--CONTENT_PLACEHOLDER-->')
```
Keep each patch small and targeted. Zeline's `execute_code` runs ordinary Python: it cannot call `patch()` or import a tool bridge. Use the exposed `patch_file` tool directly, or write a standalone generation script using pathlib and execute it with Python.

## Footer Deduplication

**Lesson (2026-07-13):** User said to remove GitHub/Telegram/X/Instagram links from the footer because they're already in the Resources > Community section. Don't duplicate social links in the footer if a dedicated community/social section exists on the page. Footer should only contain: `ZelineGuide — by Mftrferdinand — Zeline AI Agent Documentation` (with source link).

## Delegation Rejection for Content Generation

**Critical lesson (2026-07-13, v7):** User explicitly rejected delegation for content-heavy tasks: "gausa pake sub agent sub agent, lu kerja sendiri buru yang bagus, masa GLM 5.2 jelek bgt hasilnya". Subagent-generated content was consistently: (1) shallow, (2) had indentation/syntax errors in Python scripts, (3) had duplicate sections from race conditions, (4) took 90+ minutes with poor results.

For content generation tasks where quality matters, work directly in the main session. Use `execute_code` or `write_file` to build generate scripts incrementally. Do NOT delegate to subagents for:
- Writing article-length page content
- Generating Python scripts with multi-line f-strings
- Tasks requiring consistent tone across many pages

## Premium Quality Expectation

User said "buat jauh lebih menarik seperti web seharga ratusan juta, gapapa lama saya tunggu". This means:
- Visual design must feel premium (not just functional)
- Animations must be smooth and professional (not gimmicky)
- Typography, spacing, color harmony matter
- User will wait for quality — speed is not the priority
- Reference sites the user admires (soul-guide-gutluc.pages.dev) set the bar

## Slang Cleaning for Indonesian Content

When scraping Indonesian-language reference sites, the content often contains casual slang ("lo", "gua", "kayak", "ga", "nih", "dong", "sih"). For knowledge base sites, clean these to formal Indonesian. See `references/content-scraping.md` for the reusable `clean_slang()` regex pattern.

### Raw Code in `<p>` Tags (post-generation fix)

**Critical issue (2026-07-13):** After scraping + markdown→HTML conversion, some code lines end up as paragraph text inside `<p>` tags instead of `<div class="code-block">` wrappers. This happens when scraped single-backtick code blocks produce `<p>code line</p>` instead of `<pre><code>`.

**Symptom:** User sees raw code like `return run_command(cmd)` or `pending_commands[chat_id] = call` floating in prose with no code block styling or copy button.

**Fix (post-generation, on HTML files):** Scan all `<p>` tags for code keywords (`return run_`, `pending_commands[`, `re.compile(`, `OpenAI(`, `.choices[0]`, `os.environ`, tree structures `├──`/`└──`/`│`). Replace matching `<p>code</p>` with `<div class="code-block"><pre><code>escaped_code</code></pre><button class="copy-btn">Copy</button></div>`. Also detect consecutive `<p>code</p>` sequences (2+ in a row) and wrap as single code block.

**Verification:** After all fixes, extract prose (excluding code blocks), strip tags, scan for strict code patterns. Any match = code still leaking = bug. See `references/content-scraping.md` for the full detection and fix implementation.

## Pitfalls

1. **Don't overbuild with MkDocs for personal branding.** User said "template nya jelek" and prefers pre-made Astro/Next.js.
2. **Astro runs on port 4321**, Next.js on 3000. Check `package.json` for `"dev"` script.
3. **Install takes 30-60s on Termux** due to npm resolution.
4. **Some templates need `astro add`** for integrations (tailwind, react).
5. **Don't delegate content generation to subagents.** Subagent output is consistently shallow and error-prone. Work directly.
6. **Content must be DEEP.** 800-1500 words per page, with code examples and tables. Bullet points are NOT content.
7. **CRITICAL: Python import side-effect trap.** When splitting a site generator into multiple scripts (e.g., `generate_v7.py` for soul-guide sections + `section_agents_llm.py` for AI/LLM sections), if the section script does `from generate_v5 import content_page, ...`, Python executes generate_v5.py as a **side effect** — which regenerates ALL pages with shallow content, **overwriting** the deep content just written by other section scripts. This was the root cause of ALL shallow content in this session. Fix: (a) import from the clean version that doesn't have the shallow sections (e.g., `from generate_v7 import ...` where v7 had those sections removed), or (b) define functions independently in each section script, or (c) run main generator FIRST, section scripts LAST, and ensure section scripts don't import the main generator. ALWAYS verify after running all scripts with a char count check per page — pages under 2000 chars = shallow = bug.
8. **Content verification loop.** After running all generate scripts, verify EVERY page:
```bash
for page in "llm/what-is.html" "agents/what-is.html" "zeline/combo.html"; do
  chars=$(curl -s "http://localhost:8081/$page" | python3 -c "
import sys, re
html = sys.stdin.read()
m = re.search(r'<div class=\"prose\">(.*?)</div>', html, re.S)
if m:
    text = re.sub(r'<[^>]+>', ' ', m.group(1))
    print(len(re.sub(r'\s+', ' ', text).strip()))
else: print(0)
")
  echo "  $page: $chars chars"
done
```
Pages under 2000 chars = shallow. Zero tolerance — if ANY page is shallow, the import side-effect trap is likely the cause.

9. **Scraped code block corruption.** When scraping MkDocs Material sites with the HTML→markdown regex pipeline, `<pre><code>` blocks can be converted to single-backtick (`` ` ``) instead of triple-backtick (```` ``` ````). This causes the `md_to_html()` converter to treat multi-line code as inline code, and raw code text (import statements, config, function definitions) leaks into prose paragraphs without `<pre>` wrappers. **Symptom:** User sees raw code like `BOT_TOKEN = os.environ[...]` floating in paragraph text with no code block styling or copy button. **Fix:** Run a markdown post-processor (`fix_md_codeblocks.py`) that detects code-like lines (containing `import`, `def`, `=`, `os.`, `BOT_TOKEN`, `cat >`, `systemctl`, etc.) and wraps them in triple-backtick fenced blocks before generating HTML. See `references/content-scraping.md` for the detection heuristics.

10. **Post-generation slang cleaning required.** Even if `clean_slang()` runs during markdown loading, section scripts that import generate_v5/v7 regenerate pages from raw content, re-introducing slang. **Always run `clean_all_slang.py` as a POST-PROCESS step** on all generated HTML files AFTER all generate scripts complete. The correct pipeline order is:
```
1. python3 generate_v7.py        # Home + soul-guide sections
2. for script in section_*.py; do [ -f "$script" ] || continue; python3 "$script" || exit; done           # Deep content sections (agents, llm, prompt, automation, api, coding, zeline)
3. python3 clean_all_slang.py    # Post-process: clean ALL HTML files
4. Verify: char count per page + slang scan
```
The `clean_all_slang.py` script reads each `.html` file in `site/`, applies the full slang regex replacement list directly to the HTML output, and writes it back. This catches slang from ALL sources (scraped content, section scripts, inline content).

11. **Section script import fix (confirmed).** The fix for pitfall #7 is confirmed: change `from generate_v5 import (...)` to `from generate_v7 import (...)` in section scripts, where v7 has had the shallow AI/LLM/API/Coding/Zeline sections REMOVED (truncated to only home + soul-guide). This prevents the side-effect regeneration. Verify by checking that `generate_v7.py` does NOT contain `section_index("agents"` or `content_page("llm"` calls.

12. **Premium quality bar.** User said "saya bayar anda mahal" and "BIKIN JADI WEB 1 MILYAR". This is not hyperbole — it's the quality bar. Every page must have substantial content (5K+ chars), proper code blocks with copy buttons, zero slang, zero shallow pages. The user will explicitly call out any page that looks like "tulisan simpel" — bullet points without explanation are unacceptable. When the user references a site they admire (soul-guide-gutluc.pages.dev), match or exceed that quality level.

13. **Pipe-delimited table corruption from scraping.** When scraping MkDocs Material sites, markdown tables that lack the `|---|---|` separator row (common in scraped content) are NOT recognized as tables by `md_to_html()`. Instead, each `| text |` line becomes a separate `<p>| text |</p>` paragraph, and the pipe character shows as literal text in the rendered page. **Symptom:** User sees broken text like `| Pengaruh dari Identity |` floating in paragraphs instead of a proper table. **Fix:** Two-step approach: (a) Run `fix_md_codeblocks.py` which also has a table-fixing pass that detects consecutive pipe-delimited lines and inserts a separator row; (b) Run a post-generation HTML fixer (`fix_tables.py`) that finds `<p>| text</p>` sequences and converts them to `<table>` HTML directly. **Prevention:** Always verify scraped markdown has `|---|` separators before generating HTML. If not, insert them programmatically.

14. **"Copy" button text leaking into prose.** When code-block divs are generated as `<div class="code-block"><pre><code>...</code></pre><button class="copy-btn">Copy</button></div>`, the word "Copy" from the button can leak into the rendered prose text if the HTML is post-processed by slang cleaners or other regex-based fixers that strip tags but leave button text. **Symptom:** User sees the word "Copy" appearing randomly in paragraph text. **Fix:** In the post-processing pipeline, specifically target and remove standalone "Copy" text that appears outside of `<button>` tags: `re.sub(r'>Copy<', '><', html)` and `re.sub(r'\nCopy\n(?=<)', '\n', html)`. Also ensure the copy button HTML is always well-formed before running slang/entity cleaners.
This fix runs AFTER `clean_all_slang.py` and `fix_all_issues.py` as a separate post-processing pass. The correct pipeline order is:

```
1. python3 generate_v8.py              # Home + soul-guide sections
2. for script in section_*.py; do [ -f "$script" ] || continue; python3 "$script" || exit; done                # Deep content sections
3. python3 clean_all_slang.py          # Clean slang from ALL HTML files
4. python3 fix_all_issues.py           # Fix double-escaped entities, "Copy" leaks, raw backticks
5. python3 fix_tables.py               # Fix pipe-delimited text → proper HTML tables
6. python3 fix_format.py               # Fix single-word/broken <p> tags → code blocks or merge
7. Verify: char count per page + slang scan + pipe-<p> scan + double-escaped scan + code leak scan
```

## Single-Word / Broken `<p>` Tag Formatting

**Critical issue (2026-07-13):** Scraped content converted to HTML often produces consecutive single-word or very short `<p>` tags that look broken to the user. Symptom: "1kata langsung paragraph karena terkesan eror terlihatnya". Examples:

```html
<p>EOF</p>
<p>↓</p>
<p>├── history/</p>
<p>└── <user_id>.json</p>
<p>source venv/bin/activate</p>
<p>python main.py</p>
```

These are actually code lines or terminal output that got split into individual paragraphs by the markdown→HTML converter.

**Fix (`fix_format.py`):** Detect `<p>` tags containing code indicators (keywords, tree chars `├──`/`└──`/`│`, arrows `↓`/`→`, shell commands, heredoc markers `EOF`/`PYEOF`) and:

1. **Consecutive code `<p>` groups (2+ in a row):** Merge into single `<div class="code-block">` with all lines joined by `\n`
2. **Single `<p>` with strict code keywords** (`return run_`, `pending_commands[`, `re.compile(`, `OpenAI(`, `os.environ`): Wrap individually in code-block div
3. **Single-word code tokens** (`EOF`, `↓`, tree chars): Wrap in code-block div

Detection keywords for code-like `<p>` content:
```python
CODE_KEYWORDS = [
    'return ', 'def ', 'import ', 'os.', 'json.', 'async ', 'await ',
    'resp', 'answer =', 'temperature', 'chat_id', 'save_history',
    'pending_commands', 'log_action', 'run_command', 'run_shell',
    'search_duckduckgo', 're.compile', 'OpenAI(', 'TELEGRAM_BOT_TOKEN',
    'OPENAI_API_KEY', 'source ', 'python3 ', 'pip install', 'npm install',
    'cat >', 'EOF', 'PYEOF', 'systemctl', 'chmod ', 'mkdir ', 'echo ',
    '├──', '└──', '│', '↓', '→',
    # JSON patterns
    '"role":', '"content":', '"name":', '"arguments":',
    # Config lines
    'TELEGRAM_BOT_TOKEN=*** 'OPENAI_API_KEY=*** 'OPENAI_BASE_URL=',
    'OPENAI_MODEL=', 'OWNER_TELEGRAM_ID=', 'AGENT_NAME=',
]
```

Also detect: single-word `<p>` tags that match `^[a-z_]+\s*$` (like `else:`, `try:`) or `^[a-z_]+\(` (function calls).

## Name Replacement in Scraped Content

When scraping content from a reference site that uses different proper names (e.g. "Kai", "Gutluc"), replace them with the user's preferred names. Use `sed` with word boundaries on BOTH HTML and markdown source files:

```bash
find site/ content/ -type f \( -name "*.html" -o -name "*.md" \) -exec sed -i \
  -e 's/\bkai-agent\b/aes-agent/g' \
  -e 's/\bkai-bot\b/aes-bot/g' \
  -e 's/\bkai-key\b/aes-key/g' \
  -e 's/\bKai\b/Aes/g' \
  -e 's/\bkai\b/aes/g' \
  -e 's/\bGutluc\b/Mahesa F. Ferdinand/g' \
  {} \;
```

**Critical:** Use `\b` word boundaries to avoid replacing substrings in normal words:
- `kai` as standalone word → replace with `aes`
- `pakainya` (contains "kai") → DO NOT replace (it's "pakaianya" = "way to use")
- `rangkaian` (contains "kai") → DO NOT replace (it's "sequence")
- `kait` (contains "kai") → DO NOT replace (it's "relate")

Handle compound names first (`kai-agent`, `kai-bot`, `kai-key`) before bare `kai`. Verify with:
```bash
grep -rn "\bkai\b\|\bKai\b" site/  # Must return 0 results
grep -roh "kai[a-z_-]*" site/ | sort -u  # Check remaining matches are all substrings of normal words
```
Each fix script catches issues that the previous step may introduce. Running them out of order or skipping steps results in broken rendering. After ALL fixes, run a verification scan that checks: (a) zero `<p>` tags starting with `|`, (b) zero `&amp;mdash;` or similar double-escaped entities, (c) zero "Copy" text outside `<button>` tags, (d) zero raw backticks in prose, (e) every page has 2000+ chars in prose. Any page failing any check = bug.

16. **Double-escaped HTML entities.** When content goes through multiple processing stages (markdown → HTML → slang cleaner → entity fixer), HTML entities like `&mdash;` can become `&amp;mdash;` (the `&` gets escaped again). **Symptom:** User sees literal `&amp;mdash;` text in the rendered page instead of an em-dash (—). **Fix:** In the post-generation fixer, explicitly replace: `&amp;mdash;` → `&mdash;`, `&amp;nbsp;` → `&nbsp;`, `&amp;lt;` → `&lt;`, `&amp;gt;` → `&gt;`, `&amp;amp;` → `&amp;`, `&amp;rarr;` → `&rarr;`, `&amp;quot;` → `&quot;`. Run this AFTER all other fixes, as a final cleanup pass.

### Triggers

User says: "bikin guide dong", "buat dokumentasi", "template yang oke", "bikin situs kayak soul-guide", "pengen kayak mkdocs", "buat halaman profil".

**When the user references a slick site URL** (e.g. "bikin kayak http://example.pages.dev") and wants a polished, animated result — DO NOT default to MkDocs. Check the reference site first. If it's MkDocs Material but the user wants it to look better (animated, no emoji, premium feel), build a **custom static site** instead (see `references/custom-static-site.md`). MkDocs output has been rejected twice (2026-07-04 and 2026-07-13) — it's a confirmed pattern, not a one-off.

For personal/landing sites: "bikin landing page", "portfolio", "personal site", "template keren", "template nya jelek" (correction signal), "ganti template", "template yang bagus", "cari template".

### Dark/Light Mode Toggle (v8)

User requested: "buat juga tombol mode malam dan mode siang. Mode malam warna biru kuning putih abu. Mode siang warna putih biru kuning abu." Implement with CSS custom properties + `data-theme` attribute on `<html>`:

```css
/* Dark (default) */
:root { --bg: #0B1120; --text: #cbd5e1; --yellow: #FACC15; --blue: #2563EB; }
/* Light */
[data-theme="light"] { --bg: #FFFFFF; --text: #1e293b; --surface: #F8FAFC; }
```
```js
// Toggle: set/remove data-theme on <html>, save to localStorage
btn.addEventListener('click', () => {
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  if (isLight) { document.documentElement.removeAttribute('data-theme'); localStorage.setItem('theme','dark'); }
  else { document.documentElement.setAttribute('data-theme','light'); localStorage.setItem('theme','light'); }
});
```

Sun/moon SVG icon in nav button. Button gets `.active` class when light mode is on.

### Mega-Menu Dropdown (v8)

User spec: "Sidebar sticky dengan mega-menu dropdown". Full-screen dark overlay with categorized links. Toggle via hamburger button (mobile) or menu button (desktop). Close with X button or Escape key.

```html
<div class="mega-menu"> <!-- translateY(-100%) hidden, .open shows -->
  <button class="mega-menu-close">X</button>
  <div class="mega-menu-grid">
    <div class="mega-menu-section"><h4>Section</h4>
      <a href="...">Label<span class="desc">Description</span></a>
    </div>
  </div>
</div>
```

### Search Overlay (v8)

User spec: "Search bar global untuk pencarian dokumentasi". Fixed overlay with input + live results. Triggered by nav search button or Ctrl+K. Esc closes.

```js
// Ctrl+K to open
if ((e.metaKey || e.ctrlKey) && e.key === 'k') { overlay.classList.add('open'); }
```

Results are a hardcoded JS array of `{title, url, desc}` objects filtered by input text. No backend needed.

### Progress Bar for Tutorials (v8)

User spec: "Progress bar untuk tutorial multi-langkah". Fixed 3px bar at top of page that tracks scroll position:

```js
window.addEventListener('scroll', () => {
  const scrollHeight = document.documentElement.scrollHeight - window.innerHeight;
  const progress = (window.scrollY / scrollHeight) * 100;
  fill.style.width = Math.min(progress, 100) + '%';
}, { passive: true });
```

Only add to long tutorial pages (pass `has_progress=True` to page builder).

### Feedback Widget (v8)

User spec: "Widget feedback 'Apakah halaman ini membantu?'". Simple yes/no buttons at bottom of content pages. Replaces with thank-you message on click.

### Navy/Blue/Yellow Palette (v8)

User specified exact palette: Navy `#0B1120` (navbar, footer, mega-menu), Blue `#2563EB` (links, icons, focus), Yellow `#FACC15` (CTA, highlight, logo), White `#FFFFFF` (content bg in light mode), Grey `#64748B` (secondary text). Text on yellow buttons must be dark (navy) for WCAG AA contrast.

