# Content Scraping & Slang Cleaning

When scraping an Indonesian-language reference site (e.g. soul-guide-gutluc.pages.dev) and rebuilding as custom HTML, the content often contains casual slang that must be cleaned for a professional knowledge base.

## Scraping Pattern

```python
import urllib.request, re, os

def scrape_page(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    html = urllib.request.urlopen(req, timeout=15).read().decode()
    # MkDocs Material: article content inside <article> or .md-content__inner
    m = re.search(r'<article[^>]*>(.*?)</article>', html, re.S)
    if not m:
        m = re.search(r'class="md-content__inner"[^>]*>(.*?)</div>\s*</div>\s*</div>', html, re.S)
    text = m.group(1) if m else html
    # Convert HTML to markdown-ish
    text = re.sub(r'<pre[^>]*><code[^>]*>(.*?)</code></pre>', r'\n```\n\1\n```\n', text, flags=re.S)
    text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.S)
    for i in range(6, 0, -1):
        text = re.sub(f'<h{i}[^>]*>(.*?)</h{i}>', '\n' + '#'*i + r' \1\n', text, flags=re.S)
    text = re.sub(r'<strong>(.*?)</strong>', r'**\1**', text, flags=re.S)
    text = re.sub(r'<em>(.*?)</em>', r'*\1*', text, flags=re.S)
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', text, flags=re.S)
    text = re.sub(r'<th[^>]*>(.*?)</th>', r'| \1 ', text, flags=re.S)
    text = re.sub(r'<td[^>]*>(.*?)</td>', r'| \1 ', text, flags=re.S)
    text = re.sub(r'</tr>', '|\n', text, flags=re.S)
    text = re.sub(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.S)
    text = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', r'> \1', text, flags=re.S)
    text = re.sub(r'<[^>]+>', '', text)
    # HTML entities
    for old, new in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&quot;','"'),('&#39;',"'"),('&mdash;','—'),('&ndash;','–'),('&rarr;','→'),('&nbsp;',' ')]:
        text = text.replace(old, new)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text
```

## Slang Cleaning

Indonesian slang → formal Indonesian. Apply as regex replacements BEFORE converting to HTML.

### Core Slang Replacements

```python
SLANG = [
    (r'\bgue\b', 'sistem'), (r'\bGue\b', 'Sistem'),
    (r'\blo\b', 'anda'),  # MUST be last — "lo" matches many substrings
    (r'\bga\b', 'tidak'), (r'\bgak\b', 'tidak'), (r'\bnggak\b', 'tidak'),
    (r'\bkayak\b', 'seperti'),
    (r'\bbuat\b', 'untuk'),  # context-dependent, but "buat" as "for" is slang
    (r'\btau\b', 'tahu'),
    (r'\bbakal\b', 'akan'),
    (r'\bngerti\b', 'mengerti'),
    (r'\bbikin\b', 'membuat'),
    (r'\bdapet\b', 'mendapat'),
    (r'\bcapek\b', 'lelah'),
    (r'\bemang\b', 'memang'),
    (r'\btetep\b', 'tetap'),
    (r'\bdoang\b', 'saja'),
    (r'\bsih\b', ''), (r'\bdong\b', ''), (r'\bnih\b', 'ini'),
    (r'\btrs\b', 'kemudian'), (r'\btrus\b', 'kemudian'),
    (r'\bmo\b', 'ingin'), (r'\bmau\b', 'ingin'), (r'\bpengen\b', 'ingin'),
    (r'\bgas\b', 'mulai'),  # "gas" as "go" is slang
    (r'\bbln\b', 'bulan'), (r'\bdr\b', 'dari'), (r'\bdgn\b', 'dengan'),
    (r'\budh\b', 'sudah'), (r'\bskrng\b', 'sekarang'),
    (r'\bjg\b', 'juga'),
    # Compound phrases
    (r'kayak gini', 'seperti ini'),
    (r'kayak gitu', 'seperti itu'),
    (r'jadi kayak', 'menjadi seperti'),
    (r'ke-hijack', 'diretas'),
    (r'plin-plan', 'tidak konsisten'),
    # Verb prefixes (Indonesian informal → formal)
    (r'\bngejelasin\b', 'menjelaskan'), (r'\bjelasin\b', 'menjelaskan'),
    (r'\bngelakuin\b', 'melakukan'), (r'\blakuin\b', 'lakukan'),
    (r'\bngecek\b', 'mengecek'), (r'\bngirim\b', 'mengirim'),
    (r'\bnyimpen\b', 'menyimpan'), (r'\bnentuin\b', 'menentukan'),
    (r'\bngaruh\b', 'berpengaruh'), (r'\bnyobain\b', 'mencoba'),
    (r'\bngulang\b', 'mengulang'), (r'\bngebahas\b', 'membahas'),
    (r'\bngajarin\b', 'mengajarkan'), (r'\bngasih\b', 'memberikan'),
    (r'\bngelola\b', 'mengelola'), (r'\bngerancang\b', 'merancang'),
    (r'\bnyari\b', 'mencari'), (r'\bbales\b', 'membalas'),
    (r'\bliat\b', 'melihat'), (r'\bdenger\b', 'mendengar'),
    (r'\bdiemin\b', 'diam'), (r'\bkena\b', 'terkena'),
    (r'\bnurut\b', 'patuh'), (r'\bngotot\b', 'bersikeras'),
    (r'\bribut\b', 'masalah'), (r'\bkerjain\b', 'mengerjakan'),
    (r'\bdiomelin\b', 'dimarahi'),
    # Imperatives
    (r'\bgausa\b', 'tidak perlu'), (r'\bgausah\b', 'tidak perlu'),
    (r'\bgapapa\b', 'tidak masalah'),
]

def clean_slang(text):
    for pattern, replacement in SLANG:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE if len(pattern) < 6 else 0)
    # "lo" replacement MUST be last and case-insensitive
    text = re.sub(r'\blo\b', 'anda', text, flags=re.IGNORECASE)
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text
```

### Important Ordering Rules

1. **"lo" replacement must be LAST** — it matches as substring in many words. Only replace standalone `\blo\b`.
2. **Short patterns (< 6 chars) use `re.IGNORECASE`** — "Ga" and "ga" should both match.
3. **Compound phrases before single words** — "kayak gini" must be replaced before "kayak" → "seperti".
4. **Verb prefixes (nge-, ng-, ny-) before root** — "ngejelasin" before "jelasin".

## Source Verification

User said: "kan gua bilang dr awal isianya copas dari sini kaya step pemasangan bot ini itu informasi nya tapi jangan masukin Lo gua lo gua bahasa bahasa norak, cukup informasi penting seperti step + coddingan + script nya saja".

Translation: Scrape content from the reference site, keep the technical info (steps, code, scripts), but remove casual/slang language. If the reference site doesn't have enough content for a topic, find other valid sources or write it yourself.

## Code Block Corruption Fix

**Critical issue:** The HTML→markdown regex pipeline can convert `<pre><code>` blocks to single-backtick (`` ` ``) instead of triple-backtick (```` ``` ````). Multi-line code becomes inline code, and raw code text (import statements, config, function definitions) leaks into prose paragraphs without `<pre>` wrappers.

**Symptom:** User sees raw code like `BOT_TOKEN=*** floating in paragraph text with no code block styling or copy button. Pages have 0 code-block divs when they should have 10-40.

**Fix:** Run `fix_md_codeblocks.py` on all `.md` files BEFORE generating HTML:

```python
def is_code_line(line):
    indicators = [
        'import ', 'def ', 'class ', 'print(', 'if __', 'return ', 'self.',
        'os.', 'json.', 'async ', 'await ', 'app.', 'logging.',
        'BOT_TOKEN', 'OPENAI', 'OWNER', 'AGENT', 'TELEGRAM',
        'pip install', 'npm install', 'apt install', 'pkg install',
        'cd ', 'mkdir ', 'chmod ', 'echo ', 'curl ', 'git ',
        'sudo ', 'systemctl', 'docker', 'wsl ',
        'cat >', 'cat <<', 'EOF', 'PYEOF',
        'from ', 'for ', 'while ', 'try:', 'except',
        '# ===', '# ',
        'AGENT_DIR', 'MEMORY_FILE', 'SOUL_FILE',
        'SECRET_PATTERNS', 'redact_secrets', 'pending_commands',
        'MAX_AGENTIC_STEPS', 'MAX_HISTORY',
        'OPENAI_BASE_URL', 'OPENAI_MODEL', 'OWNER_TELEGRAM_ID',
        'config.yaml', '.env',
    ]
    return any(ind in line for ind in indicators)

def fix_md_codeblocks(md):
    """Detect code-like lines not in triple-backtick blocks and wrap them."""
    # Walk lines, detect code starts (single backtick + code starter, or raw code),
    # collect consecutive code lines, output as ```bash or ```python fenced blocks.
    # See ~/zeline-guide/fix_md_codeblocks.py for full implementation.
```

**Detection heuristics:**
- Line starts with single backtick followed by a code starter keyword (`cat >`, `cd`, `import`, `pip install`, etc.)
- Line contains code indicators (`import`, `def`, `=`, `os.`, `BOT_TOKEN`, `systemctl`, etc.)
- Two or more consecutive lines that look like code (run detection)
- Lines starting with `EOF` or `PYEOF` (shell heredoc markers)

**Post-process verification:** After generating HTML, count `<div class="code-block">` divs per page. A tutorial page should have 30-50 code blocks. If 0, the fix didn't work.

## Pipe-Delimited Table Corruption

**Critical issue:** Scraped markdown tables often lose their `|---|---|` separator row during HTML→markdown conversion. Without the separator, `md_to_html()` treats each `| text |` line as a separate paragraph, and pipe characters appear as literal text in the rendered page.

**Symptom:** User sees broken text like:
```
| Pengaruh dari Identity
|
| Communication
| Tone (santai/formal), bahasa, register
|
```
floating in paragraphs instead of a proper HTML table.

**Fix (3 steps):**

1. **Fix markdown source:** Detect consecutive lines starting with `|` that lack a `---` separator row, and insert one after the first row:
```python
def fix_md_tables(md):
    lines = md.split('\n')
    new_lines = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i].strip())
                i += 1
            has_sep = any('---' in tl for tl in table_lines)
            if not has_sep and len(table_lines) >= 2:
                # Parse cells, build proper table with separator
                parsed = [[c.strip() for c in tl.split('|') if c.strip()] for tl in table_lines]
                max_cols = max(len(r) for r in parsed)
                new_lines.append('| ' + ' | '.join(parsed[0]) + ' |')
                new_lines.append('|' + '|'.join(['---'] * max_cols) + '|')
                for row in parsed[1:]:
                    new_lines.append('| ' + ' | '.join(row) + ' |')
                new_lines.append('')
            else:
                new_lines.extend(table_lines)
            continue
        new_lines.append(lines[i])
        i += 1
    return '\n'.join(new_lines)
```

2. **Fix HTML post-generation:** Find `<p>| text</p>` sequences and convert to `<table>`:
```python
def fix_html_pipe_paragraphs(html_content):
    # Find consecutive <p> tags with pipe content
    pattern = r'(<p>\s*\|[^<]*</p>\s*){2,}'
    for match in reversed(list(re.finditer(pattern, html_content))):
        texts = re.findall(r'<p>\s*\|([^<]*)</p>', match.group(0))
        texts = [t.strip() for t in texts if t.strip()]
        # Pair into 2-column rows
        rows = [[texts[i], texts[i+1]] for i in range(0, len(texts)-1, 2)]
        table = '<table><tr><th>Aspek</th><th>Deskripsi</th></tr>'
        for row in rows:
            table += '<tr>' + ''.join(f'<td>{html.escape(c)}</td>' for c in row) + '</tr>'
        table += '</table>'
        html_content = html_content[:match.start()] + table + html_content[match.end():]
    return html_content
```

3. **Clean remaining pipe fragments:** Remove standalone `<p>|</p>` and strip leading pipes from `<p>| text</p>`:
```python
html = re.sub(r'<p>\s*\|\s*</p>', '', html)
html = re.sub(r'<p>\s*\|\s*([^<|]+?)\s*</p>', r'<p>\1</p>', html)
```

**Verification:** After all fixes, scan for `<p>` tags starting with `|`:
```python
pipe_ps = len(re.findall(r'<p>\s*\|', html))
# Must be 0 for every page
```

## Raw Code Leaking into `<p>` Tags

**Critical issue (2026-07-13):** After scraping + markdown→HTML conversion, code lines that were in single-backtick blocks or weren't properly fenced end up as paragraph text inside `<p>` tags. The `md_to_html()` parser treats them as regular paragraphs.

**Symptom:** User sees raw code like `resp = client.chat.completions.create(...)` or `return run_command(cmd)` floating in prose with no code block styling or copy button. Pages have fewer `<div class="code-block">` divs than expected.

**Fix (post-generation, on HTML files):**

Step 1 — Find single `<p>` tags containing strict code keywords and convert to code-block divs:

```python
CODE_KEYWORDS = [
    'return run_command', 'return run_shell', 'return None',
    'return f"', "return f'",
    'return text', 'return results', 'return False',
    'pending_commands[chat_id]',
    'log_action(chat_id',
    'search_duckduckgo(',
    're.compile(',
    'pattern = re.compile',
    'client = OpenAI(',
    'return f"Unknown tool',
    'resp = client',
    'answer = resp',
]

for keyword in CODE_KEYWORDS:
    for match in reversed(list(re.finditer(r'<p>\s*([^<]*?)\s*</p>', html_content))):
        text = match.group(1).strip()
        if keyword in text:
            code_escaped = html_mod.escape(text)
            replacement = f'<div class="code-block"><pre><code>{code_escaped}</code></pre><button class="copy-btn">Copy</button></div>'
            html_content = html_content[:match.start()] + replacement + html_content[match.end():]
```

Step 2 — Find consecutive `<p>` tags containing code indicators (groups of 2+ in a row) and wrap as single code block:

```python
pattern = r'((?:<p>\s*[^<]*?(?:import |def |return |os\.|resp|answer =|temperature|chat_id|save_history|client\.)[^<]*?</p>\s*){2,})'
for match in reversed(list(re.finditer(pattern, html_content, re.S))):
    texts = re.findall(r'<p>\s*([^<]*?)\s*</p>', match.group(0))
    texts = [t.strip() for t in texts if t.strip()]
    code_text = '\n'.join(texts)
    code_escaped = html_mod.escape(code_text)
    replacement = f'<div class="code-block"><pre><code>{code_escaped}</code></pre><button class="copy-btn">Copy</button></div>'
    html_content = html_content[:match.start()] + replacement + html_content[match.end():]
```

Step 3 — Find `<p>` tags containing tree-structure characters (├──, └──, │) and wrap:

```python
pattern2 = r'<p>\s*([^<]*?(?:├──|└──|│)[^<]*?)\s*</p>'
for match in reversed(list(re.finditer(pattern2, html_content))):
    text = match.group(1).strip()
    code_escaped = html_mod.escape(text)
    replacement = f'<div class="code-block"><pre><code>{code_escaped}</code></pre><button class="copy-btn">Copy</button></div>'
    html_content = html_content[:match.start()] + replacement + html_content[match.end():]
```

**Verification scan (run after ALL fixes):**

```python
m = re.search(r'<div class="prose">(.*?)</div>\s*</div>\s*</div>', html, re.S)
if m:
    prose = m.group(1)
    clean = re.sub(r'<div class="code-block">.*?</div>', '', prose, flags=re.S)
    clean = re.sub(r'<code>.*?</code>', '', clean, flags=re.S)
    text = re.sub(r'<[^>]+>', '\n', clean)
    strict_patterns = [r'^return\s+\w', r'^pending_commands\[', r're\.compile\(', r'OpenAI\(', r'\.choices\[0\]', r'os\.environ']
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped and len(stripped) < 200:
            for pat in strict_patterns:
                if re.search(pat, stripped):
                    print(f"CODE LEAK: {stripped[:80]}")
                    break
```

Any match = code still leaking = bug. Zero tolerance.

This fix runs AFTER `clean_all_slang.py` and `fix_all_issues.py` as a separate post-processing pass. The correct pipeline order is:

```
1. python3 generate_v8.py              # Home + soul-guide sections
2. for script in section_*.py; do [ -f "$script" ] || continue; python3 "$script" || exit; done                # Deep content sections
3. python3 clean_all_slang.py          # Clean slang from ALL HTML files
4. python3 fix_all_issues.py           # Fix double-escaped entities, "Copy" leaks, raw backticks
5. python3 fix_tables.py               # Fix pipe-delimited text → proper HTML tables
6. python3 fix_raw_code.py             # Fix raw code in <p> tags → code-block divs
7. Verify: char count per page + slang scan + pipe-<p> scan + double-escaped scan + code leak scan
```

## Post-Generation Slang Cleaning

Even if `clean_slang()` runs during markdown loading, section scripts that import generate_v5/v7 regenerate pages from raw content, re-introducing slang. **Always run `clean_all_slang.py` as a POST-PROCESS step** on all generated HTML files AFTER all generate scripts complete.

The `clean_all_slang.py` script:
1. Walks all `.html` files in `site/`
2. Applies the full slang regex replacement list directly to HTML output
3. Writes cleaned files back
4. Catches slang from ALL sources (scraped content, section scripts, inline content)

Verify with a slang scan after cleaning:
```python
for root, dirs, files in os.walk("site"):
    for f in files:
        if f.endswith('.html'):
            text = re.sub(r'<[^>]+>', ' ', html).lower()
            for word in [' lo ', ' gue ', ' gua ', ' kayak ', ' gak ', ' sih ', ' dong ', ' bikin ', ' emang ']:
                if word in text:
                    print(f"SLANG found: {path} '{word}'")
```

Zero tolerance — if ANY slang word is found, the page needs re-cleaning.

## Working Example

The `~/zeline-guide/scrape_soul.py` script + `clean_slang()` function in `generate_v7.py` demonstrate the full pipeline:
1. Scrape 14 pages from soul-guide-gutluc.pages.dev (urllib + regex HTML→markdown)
2. Clean slang from each .md file (regex replacements)
3. Convert cleaned markdown to HTML (md_to_html function)
4. Generate styled HTML pages with custom CSS theme

Total scraped content: 14 pages, ~100K chars. After cleaning, content is professional Indonesian with code blocks, tables, and step-by-step instructions intact.
