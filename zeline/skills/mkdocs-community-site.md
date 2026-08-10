# Mkdocs Community Site

> Build MkDocs Material documentation sites for personal branding, community hubs, and portfolio + blog combos. Custom CSS theming, hero sections, grid cards, social badges, multi-section navigation.

Build a full MkDocs Material site with personal/community branding.

## When to Use

- User wants a "guide site" like `soul-guide-gutluc.pages.dev` (MkDocs Material)
- User wants personal branding + community pages (the community style)
- User wants portfolio + blog + documentation in one site
- User wants to deploy via Cloudflare Pages / GitHub Pages / local HTTP server

## Quick Start

```bash
pip install mkdocs mkdocs-material
mkdocs new .
```

## Navigation Structure (Community Site Pattern)

Use `navigation.tabs` + `navigation.sections` + `navigation.indexes` features for tabbed multi-section nav:

```yaml
nav:
  - Beranda: index.md
  - Tentang:
    - tentang/index.md
    - Track Record: tentang/karir.md
    - Portfolio: tentang/portfolio.md
  - Komunitas:
    - komunitas/index.md
    - Channel Utama: komunitas/channel.md
    - General: komunitas/general.md
    - AI Insight: komunitas/ai-insight.md
    - Research: komunitas/research.md
  - Konten:
    - ai/index.md
    - research/index.md
    - airdrop/index.md
  - Referensi:
    - Cheatsheet: referensi/cheatsheet.md
```

## Theme Features Required

```yaml
theme:
  features:
    - navigation.tabs        # Tab bar on top
    - navigation.sections    # Section grouping in sidebar
    - navigation.top         # Scroll-to-top button
    - navigation.indexes     # Index pages as section anchors
    - navigation.instant     # Instant loading (SPA-like)
    - search.suggest
    - search.highlight
    - content.code.copy
    - header.autohide
```

## Custom CSS Patterns

### 1. Primary Color Override

```css
:root {
  --md-primary-fg-color: #6C63FF;
  --md-primary-fg-color--light: #8B85FF;
  --md-primary-fg-color--dark: #4A42E8;
  --md-accent-fg-color: #00D4AA;
}
```

**Pitfall:** Set `primary: custom` in mkdocs.yml, NOT a built-in color name — otherwise CSS vars are ignored.

### 2. Hero Section (for homepage)

```markdown
<div class="hero">

# Brand Name

<div class="sub">by owner</div>

Description text...

<div class="social-row">
  <a href="...">🌐 Link</a>
</div>

<div class="stats-bar">
  <div class="stat"><div class="num">92</div><div class="lbl">Skills</div></div>
</div>

</div>
```

CSS:
```css
.md-typeset .hero { text-align: center; padding: 4rem 0 2rem; }
.md-typeset .hero h1 {
  font-size: 2.8rem; font-weight: 800;
  background: linear-gradient(135deg, #6C63FF 0%, #00D4AA 50%, #FF6B6B 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}
```

### 3. Grid Cards

```markdown
<div class="grid-cards">
  <a href="page/" class="grid-card">
    <div class="icon">📢</div>
    <h3>Title</h3>
    <p>Description...</p>
  </a>
</div>
```

CSS:
```css
.md-typeset .grid-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 1rem;
}
.md-typeset .grid-card {
  padding: 1.5rem; border-radius: 12px;
  border: 1px solid rgba(255,255,255,0.08);
  transition: all 0.25s;
}
.md-typeset .grid-card:hover {
  border-color: #6C63FF;
  transform: translateY(-3px);
}
```

### 4. Social Badges Row

```css
.md-typeset .social-row {
  display: flex; flex-wrap: wrap; justify-content: center;
  gap: 0.5rem; margin: 1.5rem 0;
}
.md-typeset .social-row a {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.4rem 1rem; border-radius: 2rem;
  font-size: 0.8rem; font-weight: 600;
  border: 1px solid; transition: all 0.2s;
}
```

### 5. Stats Bar

```css
.md-typeset .stats-bar {
  display: flex; justify-content: center; gap: 2.5rem; margin: 2rem 0;
}
.md-typeset .stats-bar .num { font-size: 1.8rem; font-weight: 700; color: #6C63FF; }
.md-typeset .stats-bar .lbl { font-size: 0.8rem; opacity: 0.5; }
```

## Social Links Config

```yaml
extra:
  social:
    - icon: fontawesome/brands/github
      name: GitHub
      link: https://github.com/username
    - icon: fontawesome/brands/x-twitter
      name: Twitter / X
      link: https://x.com/username
    - icon: fontawesome/brands/instagram
      name: Instagram
      link: https://instagram.com/username
    - icon: fontawesome/brands/telegram
      name: Telegram
      link: https://t.me/username
    - icon: fontawesome/brands/discord
      name: Discord
      link: https://discord.gg/username
```

## Loading extra.css

1. Create `docs/assets/extra.css`
2. Add to mkdocs.yml:
```yaml
extra_css:
  - assets/extra.css
```

## Index Pages for Sections

Create `docs/section/index.md` with `hide: [navigation, toc]` frontmatter to act as a gateway page with grid cards linking to sub-pages.

## Social Icons (SVG Inline)

For inline SVGs in markdown (not using the `extra.social` footer), embed raw SVG:

```html
<svg style="width:16px;height:16px" viewBox="0 0 24 24" fill="currentColor">
  <path d="[SVG path data]"/>
</svg>
```

Common SVG paths:
- **GitHub:** `M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385...`
- **Twitter/X:** `M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z`
- **Telegram:** `M11.944 0A12 12 0 000 12a12 12 0 0012 12 12 12 0 0012-12A12 12 0 0012 0a12 12 0 00-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 01.171.325...`
- **Discord:** `M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495...`
- **Instagram:** `M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849...`

## Web3 / Crypto Branding Tips

- Use gradient hero titles (purple-cyan-red) for "Web3" vibe
- Stats bar showing community metrics (skills, members, workflows)
- Dark mode as default (`scheme: slate`, toggle to light)
- Grid cards with icon emojis for community categories
- Social badges in brand colors:
  - GitHub: `#333` bg
  - Twitter: `#1DA1F2`
  - Instagram: `#E4405F`
  - Telegram: `#0088CC`
  - Discord: `#5865F2`

## Finding Templates on GitHub

Search by stars for mkdocs themes:
```bash
curl -sL "https://api.github.com/search/repositories?q=mkdocs+material+template+blog&sort=stars&per_page=5"
```

For Astro alternatives with blog+portfolio support:
```bash
curl -sL "https://api.github.com/search/repositories?q=astro+blog+template+minimal&sort=stars&per_page=5"
```

## Pitfalls

- **YAML `!!python/name` tags cause build errors** — in older mkdocs-material, `emoji_index: !!python/name:material.extensions.emoji.twemoji` works. In newer versions, just use `- pymdownx.emoji` without index/generator (simpler and avoids YAML tag parsing issues).
- **Grid card links inside markdown** — use `<a href="page/" class="grid-card" style="text-decoration:none;color:inherit">` to make entire card clickable without MkDocs stripping the link.
- **Relative links in index pages** — `[Kembali ke Beranda](..)` doesn't resolve. Use `../index.md` explicitly.
- **Hide nav+toc on gateway pages** — use YAML frontmatter `hide: [navigation, toc]` on index hub pages so they're clean gateways.
- **`navigation.indexes` requires the section index.md to exist** — without it, clicking the tab shows 404.
- **MkDocs 2.0 warning** is harmless — the deprecation banner appears on all builds but the site works fine.
