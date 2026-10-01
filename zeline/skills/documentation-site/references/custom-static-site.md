# Custom Static Site Pattern (no framework)

When the user rejects MkDocs output and wants a polished, animated site, build a custom static site with pure HTML/CSS/JS. No framework, no build step, no npm install.

## When to use this pattern

- User says "bikin kayak [slick reference site]" (e.g. soul-guide-gutluc.pages.dev)
- User explicitly rejects MkDocs: "jelek", "gada animasi", "full emoji", "jangan ada 1 pun emoji"
- User wants: "sebagus mungkin, gapopa lama yang penting bagus"
- User provides a detailed UI/UX spec with palette, typography, nav structure, page layouts
- Target deploy: Cloudflare Pages or any static host

## Core rules

1. **ZERO emoji.** Use inline SVG icons for ALL visual elements. Define a Python dict of SVG path strings keyed by topic name. Inject into HTML via f-string.
2. **Animations required.** At minimum: animated gradient background orbs, floating particles, scroll-triggered reveal (IntersectionObserver), stat counter. Static pages feel cheap.
3. **Dark theme default.** User prefers dark slate background with gradient accents.
4. **Python generate.py.** Single script, shared partials, writes all pages. Faster than hand-writing N HTML files.

## Structure

```
project/
├── generate.py          # Python script — holds all content, writes site/
├── site/                # Build output (static, deployable)
│   ├── index.html
│   ├── section/
│   │   ├── index.html
│   │   └── page.html
│   └── assets/
│       ├── css/style.css
│       └── js/main.js
```

## generate.py skeleton

```python
#!/usr/bin/env python3
"""Generate all HTML pages for a custom static site."""
import os

BASE = "/path/to/project/site"

# SVG icons — zero emoji
ICONS = {
    "bolt": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z"/></svg>',
    "user": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 4-6 8-6s8 2 8 6"/></svg>',
    # ... add as needed
}

def nav_html(active=""):
    """Shared nav bar. `active` = section id for active link highlight."""
    links = [("beranda", "Beranda", "index.html"), ...]
    prefix = "../" if active else ""
    items = ""
    for id_, label, href in links:
        cls = "nav-link active" if id_ == active else "nav-link"
        items += f'<a href="{prefix}{href}" class="{cls}">{label}</a>'
    return f'''<nav class="nav">
      <a href="{prefix}index.html" class="nav-brand">Site Name</a>
      <div class="nav-links">{items}</div>
    </nav>'''

def footer_html():
    return '''<footer class="footer">...</footer>'''

def page(path, title, content, active=""):
    """Write a full HTML page with correct relative paths."""
    parts = [p for p in path.replace("index.html", "").strip("/").split("/") if p]
    depth = len(parts)
    prefix = "../" * depth if depth > 0 else ""
    html = f'''<!DOCTYPE html>
<html lang="id"><head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
  <title>{title} — Site Name</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800;900&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{prefix}static/assets/css/style.css">
</head><body>
  {nav_html(active)}
  {content}
  {footer_html()}
  <script src="{prefix}static/assets/js/main.js"></script>
</body></html>'''
    os.makedirs(os.path.dirname(os.path.join(BASE, path)), exist_ok=True)
    with open(os.path.join(BASE, path), "w") as f: f.write(html)
```

## Relative path calculation (critical)

```python
parts = [p for p in path.replace("index.html", "").strip("/").split("/") if p]
depth = len(parts)
prefix = "../" * depth if depth > 0 else ""
```

- `"index.html"` → depth 0 → prefix `""` → `static/assets/css/style.css`
- `"profil/index.html"` → depth 1 → prefix `"../"` → `../static/assets/css/style.css`
- `"profil/sosial/index.html"` → depth 2 → prefix `"../../"` → `../../static/assets/css/style.css

Getting this wrong = CSS/JS 404 in subdirectory pages. Always test with `curl -sI` on a subdir page.

## Bilingual `t()` function

For sites with ID/EN translate toggle, use a Python helper that generates dual-span HTML:

```python
def t(id_text, en_text):
    return f'<span class="lang-id-inline">{id_text}</span><span class="lang-en-inline">{en_text}</span>'
```

CSS toggles visibility based on `html.lang-en` class. JS toggles the class on `<html>` and swaps button label ID↔EN.

## Dark/Light Mode Toggle (v8)

Use CSS custom properties + `data-theme` attribute on `<html>`:

```css
/* Dark (default) */
:root { --bg: #0B1120; --text: #cbd5e1; --yellow: #FACC15; --blue: #2563EB; }
/* Light */
[data-theme="light"] { --bg: #FFFFFF; --text: #1e293b; --surface: #F8FAFC; }
```
```js
btn.addEventListener('click', () => {
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  if (isLight) { document.documentElement.removeAttribute('data-theme'); localStorage.setItem('theme','dark'); }
  else { document.documentElement.setAttribute('data-theme','light'); localStorage.setItem('theme','light'); }
});
```

Load saved preference on page load. Sun/moon SVG icon in nav button.

## Mega-Menu Dropdown (v8)

Full-screen dark overlay with categorized links. Toggle via menu button. Close with X or Escape.

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

## Search Overlay (v8)

Fixed overlay with input + live results. Triggered by nav search button or Ctrl+K. Esc closes.

```js
if ((e.metaKey || e.ctrlKey) && e.key === 'k') { overlay.classList.add('open'); }
```

Results are a hardcoded JS array of `{title, url, desc}` objects filtered by input text.

## Progress Bar (v8)

Fixed 3px bar tracking scroll position. Only on long tutorial pages.

```js
const progress = (window.scrollY / (document.documentElement.scrollHeight - window.innerHeight)) * 100;
fill.style.width = Math.min(progress, 100) + '%';
```

## Feedback Widget (v8)

Simple yes/no buttons at bottom of content pages. Replaces with thank-you on click.

## CSS animation toolkit

### Animated background orbs
```css
.bg-canvas { position: fixed; inset: 0; z-index: -2; overflow: hidden; }
.bg-glow { position: absolute; border-radius: 50%; filter: blur(100px); opacity: .12; }
.bg-glow-1 { width: 400px; height: 400px; background: var(--yellow); top: -100px; animation: glow1 30s ease-in-out infinite; }
@keyframes glow1 { 0%,100%{transform:translate(0,0)} 50%{transform:translate(60px,40px)} }
```

### Floating particles (JS-generated, max 15)
```js
for (let i = 0; i < 15; i++) {
  const p = document.createElement('div');
  p.className = 'particle';
  p.style.left = Math.random() * 100 + '%';
  p.style.animationDuration = (Math.random() * 15 + 15) + 's';
  container.appendChild(p);
}
```

### Scroll reveal (IntersectionObserver)
```js
const obs = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add('visible'); obs.unobserve(e.target); }
  });
}, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });
document.querySelectorAll('.reveal').forEach(el => obs.observe(el));
```

### Stat counter
```js
const obs = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    const el = e.target;
    const target = parseInt(el.dataset.count);
    const suffix = el.dataset.suffix || '';
    let current = 0;
    const step = Math.ceil(target / 25);
    const tick = () => {
      current += step;
      if (current >= target) { el.textContent = target + suffix; return; }
      el.textContent = current + suffix;
      requestAnimationFrame(tick);
    };
    tick();
    obs.unobserve(el);
  });
}, { threshold: 0.5 });
document.querySelectorAll('.stat-num[data-count]').forEach(s => obs.observe(s));
```

### Card tilt effect (3D perspective, max ±8deg)
```js
card.addEventListener('mousemove', (e) => {
  const rect = card.getBoundingClientRect();
  const rotX = ((e.clientY - rect.top - rect.height/2) / (rect.height/2)) * -6;
  const rotY = ((e.clientX - rect.left - rect.width/2) / (rect.width/2)) * 6;
  card.style.transform = `perspective(600px) rotateX(${rotX}deg) rotateY(${rotY}deg) translateY(-3px)`;
});
```

## Serve on Termux

```bash
cd project/site && python3 -m http.server 8081
# Use background=true in terminal tool
```

Health check:
```bash
curl -sI http://localhost:8081
curl -sI http://localhost:8081/profil/        # test subdir
curl -sI http://localhost:8081/static/assets/css/style.css  # test CSS path
```

## Post-Generation HTML Fix Pipeline (CRITICAL)

After all generate scripts run, fix scripts MUST run in this exact order:

```
1. python3 generate_v8.py              # Home + soul-guide sections
2. for script in section_*.py; do [ -f "$script" ] || continue; python3 "$script" || exit; done                # Deep content sections (agents, llm, prompt, etc.)
3. python3 clean_all_slang.py          # Clean slang from ALL HTML files
4. python3 fix_all_issues.py           # Fix double-escaped entities, "Copy" leaks, raw backticks
5. python3 fix_tables.py               # Fix pipe-delimited text → proper HTML tables
6. python3 fix_format.py               # Fix single-word/broken <p> tags → code blocks
7. Verify: char count per page + slang scan + pipe-<p> scan + double-escaped scan + code leak scan
```

### Verification scan (run after all fixes)
```python
import re, os
SITE = "site"
for root, dirs, files in os.walk(SITE):
    for f in sorted(files):
        if not f.endswith('.html'): continue
        path = os.path.join(root, f)
        with open(path) as fh: html = fh.read()
        pipe_ps = len(re.findall(r'<p>\s*\|', html))
        double_esc = len(re.findall(r'&amp;mdash;|&amp;nbsp;|&amp;lt;|&amp;gt;|&amp;amp;|&amp;rarr;', html))
        text = re.sub(r'<[^>]+>', '\n', html)
        copy_leaks = text.count('Copy')
        # Any non-zero = bug
```

## Data-Driven Generate Pattern

For 20+ page sites, use function-based generation:

```python
def section_index(section, title_id, title_en, desc_id, desc_en, cards_data, active):
    cards_html = "".join([card(h, i, l, d) for h, i, l, d, *b in cards_data])
    content = f'...hero + grid...'
    page(f"{section}/index.html", title_id, content, active=active)

def content_page(section, filename, title_id, title_en, body_html, active):
    content = f'...page-header + prose...'
    page(f"{section}/{filename}", title_id, content, active=active)
```

## Markdown-to-HTML Converter

When scraping a reference MkDocs site, use `md_to_html()` to convert scraped markdown to HTML. Handles: headings, fenced code blocks, tables, lists, blockquotes, bold/italic, inline code, links. Wrap code blocks in `<div class="code-block">` with copy button.

## Solid Brand Logo Colors

Every brand logo and social icon MUST have a solid background with the real brand color:

| Brand | Background | Icon Color |
|-------|-----------|------------|
| OpenAI | `#10a37f` | `#fff` |
| Anthropic | `#d97757` | `#fff` |
| Google | `#4285f4` | `#fff` |
| Meta | `#0668e1` | `#fff` |
| NVIDIA | `#76b900` | `#000` |
| DeepSeek | `#4d6bfe` | `#fff` |
| Mistral | `#ff7000` | `#fff` |
| GitHub | `#24292e` | `#fff` |
| Twitter/X | `#000` | `#fff` |
| Instagram | `linear-gradient(135deg, #f09433, #e6683c, #dc2743, #cc2366, #bc1888)` | `#fff` |
| Telegram | `#0088cc` | `#fff` |

## Mobile Horizontal Overflow Fix

```css
html { overflow-x: hidden; max-width: 100vw; }
body { overflow-x: hidden; max-width: 100vw; }
.prose table { display: block; overflow-x: auto; white-space: nowrap; max-width: 100%; -webkit-overflow-scrolling: touch; }
.prose pre { overflow-x: auto; max-width: 100%; }
```
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
```

## Navy/Blue/Yellow Palette (v8)

User specified: Navy `#0B1120` (navbar, footer, mega-menu), Blue `#2563EB` (links, icons, focus), Yellow `#FACC15` (CTA, highlight, logo, active state), White `#FFFFFF` (content bg in light mode), Grey `#64748B` (secondary text). Text on yellow buttons must be dark (navy) for WCAG AA contrast.

## Typography (v8)

- Heading: Plus Jakarta Sans or Poppins (bold, tegas)
- Body text: Inter or Manrope
- Code/monospace: JetBrains Mono or Fira Code

## Pitfalls

1. **Python import side-effect trap.** When splitting a site generator into multiple scripts, if a section script does `from generate_v5 import ...`, Python executes generate_v5.py as a side effect — regenerating ALL pages with shallow content. Fix: import from the clean version that doesn't have the shallow sections, or define functions independently.
2. **Scraped code block corruption.** `<pre><code>` blocks can be converted to single-backtick instead of triple-backtick during scraping. Run `fix_md_codeblocks.py` to detect and fix.
3. **Pipe-delimited table corruption.** Scraped markdown tables without `|---|` separator render as broken `<p>| text</p>`. Fix with `fix_tables.py` post-processor.
4. **Double-escaped HTML entities.** `&mdash;` becomes `&amp;mdash;` through multiple processing stages. Fix in post-processing.
5. **"Copy" button text leaking into prose.** Fix with `re.sub(r'>Copy<', '><', html)`.
6. **Content must be DEEP.** 800-1500 words per page minimum. Bullet points are NOT content.
7. **Don't delegate content generation to subagents.** Work directly.
8. **Always test subdir pages.** CSS/JS path must include `../` prefix.
9. **Translate toggle needs Python helper + CSS + JS in sync.** Missing the CSS rule = both languages show at once.
10. **Nav brand link path.** For subdir pages, brand link needs `../` prefix too.
11. **Single-word `<p>` tag corruption.** Scraped content produces broken formatting where code lines become individual `<p>` tags (e.g. `<p>EOF</p>`, `<p>↓</p>`, `<p>source venv/bin/activate</p>`). User sees this as "1kata langsung paragraph karena terkesan eror". Fix with `fix_format.py` post-processor that detects code-like `<p>` content and wraps in `<div class="code-block">`.
12. **Name replacement in scraped content.** Reference sites use different proper names (e.g. "Kai", "Gutluc"). Replace with user's preferred names using `sed` with `\b` word boundaries on BOTH HTML and markdown files. Handle compound names (`kai-agent` → `aes-agent`) before bare names. Verify no false positives in normal words containing the substring (e.g. "pakainya" contains "kai" but must not be replaced).
