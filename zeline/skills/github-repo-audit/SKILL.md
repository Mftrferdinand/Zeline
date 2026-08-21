# Github Repo Audit

> Audit and standardize public GitHub repos — consistency checks, English-only enforcement, structure cleanup, missing files. Especially for repos hosting Zeline skills or tools.

Systematic audit of public GitHub repos for consistency, completeness, and quality. Triggered when user asks to "rapihin", "clean up", "audit", or "standardize" their repos.

## When to Use

- User says "rapihin github", "clean up my repos", "check if my repos are consistent"
- User wants to publish/share a repo and wants it polished
- Periodic maintenance pass across all user repos

## Audit Workflow

### 1. Inventory

```bash
gh repo list USERNAME --limit 50
```

For each repo, fetch:
- `gh api repos/OWNER/REPO --jq '{description, topics, default_branch, visibility}'`
- `gh api repos/OWNER/REPO/git/trees/BRANCH?recursive=1 --jq '.tree[].path'`
- README.md, SKILL.md (if present), references/
- LICENSE content (verify copyright name consistency)
- `.gitignore` content (verify it covers secrets)

### 2. Checklist (per repo)

| # | Check | How |
|---|-------|-----|
| 1 | Visibility = public | `gh api repos/O/R --jq .visibility` — set to `public` if not |
| 2 | README.md exists & English | Read content, check language (no Indonesian words) |
| 3 | README no leaked keys/IDs | Scan for `sk-`, `gsk_`, `@email.com`, phone numbers, `rc_proxy_key` |
| 4 | `.env.example` uses dummy values | Must use `CHANGE_ME`, `YOUR_KEY_HERE` — never real-looking defaults |
| 5 | Description in English | `gh api repos/O/R --jq .description` |
| 6 | SKILL.md frontmatter English | Check description field in YAML |
| 7 | SKILL.md body all English | Scan for non-English rules/examples |
| 8 | No duplicate nested dirs | Tree listing — same file in root and nested path |
| 9 | references/ relevant only | No stale/unrelated files |
| 10 | scripts/ paths documented | Both repo path AND install path |
| 11 | LICENSE present | File exists, MIT preferred |
| 12 | LICENSE copyright name consistent | All repos should use same copyright holder name |
| 13 | .gitignore present | File exists, non-empty |
| 14 | .gitignore covers secrets | Must include: `.env`, `*.json` (for state files), `__pycache__/`, `*.pyc`, `venv/` |
| 15 | Topics set (English) | `gh api repos/O/R --jq .topics` |
| 16 | No banned topics | User may forbid certain topics (e.g. `trading`, `forex`). Remove if present |
| 17 | Cross-links to related repos | README mentions sibling repos |
| 18 | No personal/private data | No usernames, API keys, personal scripts, emails, phone numbers |
| 19 | Shebang is portable | Python files use `#!/usr/bin/env python3`, NOT hardcoded paths like `#!/data/data/com.termux/files/...` |
| 20 | README examples are generic | Code examples don't reference environment-specific model IDs, local paths, or personal configs — use `e.g. 'vendor/model-name'` not actual model IDs from your 9Router |
| 21 | No personal-attribution names | Scan for content attributed to a person/handle: "confirmed by NAME", "NAME format v3", "Rules confirmed by NAME". Replace with neutral "— FINAL" / "Confirmed rules:" |
| 22 | No session dates baked into headers | Strip dates that tie content to a specific session: "(confirmed 3 Jul 2026)", "v3 Jul 2026", "(2026-07-03)". Keep the rule, drop the date |
| 23 | No personal-address quotes | Native-language quotes that address a specific person ("Apply now atau review dulu, Kakak?", "…, Bang") → neutral English ("Apply now, or review first?") |
| 24 | No personal account/state leakage | "Remaining credit: ~$1", "my API key", personal balances, quotas — delete |
| 25 | No truncated/partial key fragments | Even a fragment in a heading like `Models Available (via key om-2jKe...)` leaks a real key prefix — strip it |

### 3. Standard Zeline Skill Repo Structure

```
RepoName/
├── .gitignore
├── LICENSE
├── README.md
├── SKILL.md
├── references/
│   └── topic-specific docs
└── scripts/
    └── runnable scripts
```

## Common Fixes

### Security Scan for Leaked Keys in README

After reading README or any public-facing file, scan for these patterns:

```python
import re
content = read_file(...)

patterns = [
    (r'sk-[A-Za-z0-9]{20,}', 'OpenAI key'),
    (r'gsk_[A-Za-z0-9]{20,}', 'Groq key'),
    (r'[a-zA-Z0-9._%+-]+@(?!example\.com|test\.com)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', 'real email'),
    (r'62\d{9,12}', 'Indonesian phone number'),
    (r'rc_proxy_key', '9Router key (unless CHANGE_ME nearby)'),
    (r'[0-9]{9,10}:[A-Za-z0-9_-]{35,}', 'Telegram bot token'),
    (r'-----BEGIN.*PRIVATE KEY-----', 'private key'),
]
```

For `.env.example` files, check that all values are `CHANGE_ME`, `YOUR_KEY_HERE`, or similar dummies — never real-looking defaults. A `rc_proxy_key` default is borderline since it's the 9Router factory default; replace with `CHANGE_ME` for safety.

**Fix pattern:**
1. Replace leaked tokens with `YOUR_KEY_HERE` or `CHANGE_ME`
2. Replace leaked emails with `user@example.com`
3. Replace leaked phone numbers with `+62xxxxxxxxxx`
4. Commit with message `fix: sanitize example values in public files`

### Sanitizing Skill Files for Public Repos (private/personal quotes vs technical content)

When the task is specifically "sanitize personal/private quotes and profanity but preserve ALL technical content," the hard part is drawing the line. Sanitize aggressively; preserve technical content exactly.

**REMOVE / NEUTRALIZE (private, personal, or leaky):**
- Profanity in any language (Indonesian: `anjing`, `kontol`, `goblok`, `tolol`, `bangsat`, `memek`, `jancok`, `asu`, `kampret`, `bego`; English: `fuck`, `shit`, etc.)
- Religious exclamations used as interjections (`Astaghfirullah`, `Wallah`, etc.)
- Personal names / handles attributing decisions ("confirmed by icibos", "icibos format v3") → "— FINAL" / "Confirmed rules:"
- Personal address in quotes ("…, Kakak?", "…, Bang") → neutral English
- Session dates that only pin content to one day ("3 Jul 2026", "v3 Jul 2026", "(2026-07-03)")
- Personal account state ("Remaining credit: ~$1")
- API key fragments even when truncated (`om-2jKe...`)
- "User will complain if X" / "removed by user" framing → neutral requirement ("Requirement: incomplete if X" / "removed")
- Quoted native-language design/UX feedback embedded in pitfall or change-log text ("buat warna loadingnya jd putih aja", "jangan ada gradient di manapun", "kalo di klik jangan muncul sedang dalam perbaikan") → restate as a neutral English requirement that keeps the exact spec ("Requirement: make the loading spinner white", "no gradients anywhere is a hard rule", "clicking an inactive method must not show a maintenance message"). Keep every concrete value (colors, px, ms, class names) verbatim — only the quoted human sentence gets rewritten.
- Session-narrative attributions inside skill bodies ("User said X", "the user explicitly asked", "user reported", "USER FRUSTRATION SIGNAL", "(Jul 16 session)") → drop the attribution and date, keep the rule. "USER FRUSTRATION SIGNAL" headers in particular pair with profanity/religious quotes — strip the whole framing, keep only the technical requirement.

**KEEP EXACTLY (technical, even if non-English):**
- Bahasa Indonesia persona/tone/format instructions that are genuine technical requirements ("Langsung gas, no small talk", "jangan nanya", output-format rules, section-order rules)
- Trigger phrases the agent must literally match ("jangan masukin data", "analisa xauusd") — these are technical inputs, not casual speech. Only reword the surrounding English framing ("When the user says" → "When instructed with")
- All commands, code blocks, API endpoints, RR ratios, CSS values, pitfalls, config

Search patterns to run across each target file (case-insensitive), then read every hit in context before editing — most benign "user"/"complain" hits are legitimate technical prose and must be left alone:
```
anjing|kontol|goblok|bangsat|tolol|memek|jancok|asu|kampret|bego|Astag|Wallah    # profanity/religious
icibos|Kakak|Bang\b|@\w+                                                          # personal attribution/address
Remaining credit|om-[A-Za-z0-9]|sk-[A-Za-z0-9]|by user|User akan komplain          # leaks / user-framing
\b\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)|v\d+ \w+ 20\d\d          # session dates
\((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}[^)]*\)|Jul \d{1,2}       # bare "(Jul 16)" / "Jul 16 session" headers
User said|the user (said|asked|reported|explicitly|caught|complained|rejected|wanted|chose)|USER FRUSTRATION   # session-narrative attribution
```
Note: "This tool doesn't work" style negative claims are NOT sanitization targets — leave technical caveats intact. Also leave bare "the user" / "user preference" prose alone when it's generic guidance ("when the user works on multiple projects") — only strip it when it's attributing a specific past quote or decision.

### Mixed Language in SKILL.md

Public repos must be 100% English. Scan for:
- Frontmatter `description` in native language
- Rules/instructions in native language (e.g. "jangan nanya", "langsung output")
- Examples with native-language commentary
- Section headers or parenthetical notes in native language

Replace all with clean English equivalents while preserving the original intent and logic.

### Duplicate Nested Directories

Old skill structures sometimes leave `skills/Name/SKILL.md` mirroring root `SKILL.md`. Delete them:

```bash
rm -rf skills/
git add -A && git commit -m "Remove duplicate nested skills directory"
```

### Stale References

Files in `references/` may reference personal scripts, unrelated tools, or outdated APIs. Remove irrelevant entries, keep only what's useful for external users.

### Script Path Ambiguity

When a script exists both in the repo and at a Zeline install path, document both:

```markdown
## Script
- **GitHub repo:** `scripts/add_history.py`
- **Zeline install path:** `~/.zeline/skills/research/trade-data-tracker/scripts/add_history.py`
```

### Hardcoded Termux Shebang

Code developed on Termux often has a shebang pointing to the Termux-specific venv path:
```python
#!/data/data/com.termux/files/home/.zeline/venv/bin/python
```
This breaks for everyone else. Fix:
```python
#!/usr/bin/env python3
```
Scan all `.py` files (and any executable scripts) for shebangs containing `/data/data/com.termux/` or `/com.termux/` and replace with `#!/usr/bin/env python3`.

### Brand-Scrub: Rebrand Upstream Runtime Name → User's Own Brand

Repos built as agent skills can leak an **upstream runtime name** into public-facing text: install commands, data paths, and "works with X" lists. When the user's public brand must be their own (e.g. Zeline/Zerolinear — no upstream runtime, lab, or other-brand names anywhere), scrub every occurrence across ALL files, not just the ones you touched.

Ordered replacements (specific → general; run in THIS order so a general rule doesn't mangle a specific one first):

```python
replacements = [
    ("Install for Zeline", "Install for Zeline"),
    ("### For Zeline",     "### For Zeline"),
    ("zeline skills install",    "zeline skills install"),
    ("(Zeline, Claude, ChatGPT", "(Zeline, Claude, ChatGPT"),  # keep the other AI names
    ("as a Zeline agent",        "as a Zeline agent"),
    ("as a Zeline skill",        "as a Zeline skill"),
    ("Zeline install path",      "Zeline install path"),
    ("Zeline local",             "Zeline local"),
    ("~/.zeline/skills",         "~/.zeline/skills"),
    ("~/.zeline/scripts",        "~/.zeline/scripts"),
    ("~/.zeline/market-history", "~/.zeline/market-history"),
    ("~/.zeline/",               "~/.zeline/"),
    ("Zeline",             "Zeline"),
]
```

Why the ordering: `~/.zeline/` would swallow `~/.zeline/skills` if run first, losing the more specific rewrite. Always apply the longest/most-specific strings before the catch-all.

`~/.zeline/` is a correct rewrite here (not just cosmetic): the user's CLI really is `zeline` and `~/.zeline/` exists on their system, so the paths stay valid.

**KEEP (not a brand leak):** the "works with Claude, ChatGPT, Codex, etc." compatibility list is a selling point — leave the third-party AI names. Only the upstream runtime name is the leak.

**Environment mentions (Termux) — genericize, don't delete the caveat:** "may timeout from Termux" → "from some networks"; "runnable on bare Termux" → "on a bare terminal". Keep the technical point, drop the environment name.

**Verification (mandatory — local diff is NOT enough):** after committing + pushing, re-fetch every file from the **live master** and scan for zero brand matches. A clean local tree can still leave stale content on the remote if a push failed or a branch mismatched. See `scripts/brand_scan.py`. Hunt terms case-insensitively: retired runtimes, labs, tools, internal codenames, personal handles, and host-specific paths that do not belong to the public brand. Expect a literal `0 matches` before reporting the scrub done.

### Environment-Specific README Examples

When a repo is developed against a personal 9Router or API endpoint, README Python/CLI examples often contain hardcoded model IDs from that specific environment:
```python
pick("gpt")         # → bai/gpt-5.6-sol       ← specific to aes's 9Router
pick("claude")      # → bai/claude-sonnet-5   ← specific to aes's 9Router
```
External users cloning the repo won't have those exact models. Replace with generic descriptions:
```python
pick("gpt")         # → latest gpt
pick("claude")      # → latest claude
```

### Missing LICENSE & .gitignore

Add MIT LICENSE and standard .gitignore.

**MIT LICENSE template** (place verbatim in a `LICENSE` file):

```
MIT License

Copyright (c) 2026 Mftrferdinand

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

**`.gitignore` template (standard):**

```
# OS
.DS_Store
Thumbs.db

# Editor
*.swp
*.swo
*~
.vscode/
.idea/

# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/

# Secrets — NEVER COMMIT (specific patterns for this project)
.env
credentials.json
*.pem
*.key

# Local state / tracking data
*.json           # if repo generates state files (registered.json, current.json, *.cache.json)
*.log
logs/

# Output
*.html           # if repo generates HTML output files
```

### Rename a Repo

```bash
gh repo rename NEW_NAME --repo OWNER/OLD_NAME
```

After rename, update the local remote URL:
```bash
git remote set-url origin https://github.com/OWNER/NEW_NAME.git
```

### Set / Remove Topics

Set topics:
```bash
gh repo edit --add-topic python --add-topic cli 2>&1
# alternative via API (replaces ALL topics at once):
gh api repos/OWNER/REPO/topics -X PUT \
  -f 'names[]=python' -f 'names[]=cli' -f 'names[]=ai-agent'
```

Remove specific banned topics (user may forbid `trading`, `forex`, etc.):
```bash
# Get current topics
gh api repos/OWNER/REPO/topics --jq '.names[]'
# Filter out banned ones, set remaining
gh api repos/OWNER/REPO/topics -X PUT \
  -f 'names[]=allowed-topic-1' -f 'names[]=allowed-topic-2'
```

### Create .gitignore on Remote-Only Repo

When you don't have the repo cloned locally, create `.gitignore` via GitHub API:

```python
import base64, subprocess

content = """.DS_Store
__pycache__/
*.pyc
.venv/
.env
*.json
*.log
"""

encoded = base64.b64encode(content.encode()).decode()
result = subprocess.run([
    "gh", "api", f"repos/OWNER/REPO/contents/.gitignore",
    "-X", "PUT",
    "-F", "message=add .gitignore",
    "-F", f"content={encoded}",
    "-f", "branch=master"  # or main
], capture_output=True, text=True, timeout=10)
```

If the file already exists (empty), get its SHA first:
```bash
gh api repos/OWNER/REPO/contents/.gitignore --jq '.sha'
```
Then include `-F "sha=THATSHAVALUE"` in the PUT to update it.

### Update Repository Description

```bash
gh api repos/OWNER/REPO --method PATCH -f description="New English description here"
```

### Translate README to English

When a README contains mixed Indonesian/English, scan for these common Indonesian words and replace their containing sentences: `buat`, `yang`, `kalo`, `pake`, `aja`, `gue`, `gua`, `dengan`, `untuk`, `sama`, `dari`, `di`, `ke`, `udah`, `gak`, `ga`. Keep all markdown structure, code blocks, and data intact. Only change language.

## Pitfalls

- **/tmp doesn't exist on Termux** — clone repos to `$HOME/repo-name` or `$TMPDIR` (Termux sets `$TMPDIR` to `.../usr/tmp`, which always exists) instead. Never hardcode `/tmp/` on Android/Termux; `cd /tmp` fails with `No such file or directory`. For remote file creation use inline Python `execute_code` with base64 encoding.
- **git config not set** — run `git config --global user.email` and `user.name` before first commit on fresh installs
- **Case-sensitive repo URLs** — GitHub may redirect (lowercase → actual case). Push still works but shows a warning. After rename, search README for old repo name references and update them.
- **README clone links break after rename** — grep for `git clone ...OLD_NAME` in README and replace with new name after repo rename. Missed this → README tells users to clone a nonexistent URL.
- **Don't push before confirming** — user may want to review changes before push. Ask if unsure
- **Zeline-local skill vs GitHub repo** — the local skill at `~/.zeline/skills/` may have Indo text that's intentional for personal use. The GitHub version must be English. These are different files with different audiences
- **`.env.example` is flagged by secret scanners** — this is expected and fine as long as all values inside are `CHANGE_ME`/`YOUR_KEY_HERE`. The file itself is not dangerous; its content determines safety.
- **Remote-only repos** — some repos may not be cloned locally. Use `gh api repos/OWNER/REPO/contents/FILE` and PUT for edits when you can't use terminal + git.

## Cross-Link Pattern

For related repos, each README should link to the other:

```markdown
## Related
- [RepoB](https://github.com/USER/RepoB) — description of what it does
```

And the README introduction should mention the workflow connection between repos.