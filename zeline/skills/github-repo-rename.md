# Github Repo Rename

> Rename a GitHub repository end-to-end — gh CLI rename, local remote update, code reference patches, commit, push, verify. Use when user says 'rename repo', 'ganti nama repo', 'ganti jadi ...', or hands you a new github.com/<owner>/<repo> URL. Class: GitHub admin / repo lifecycle operations.

Full cycle for renaming a GitHub repo from inside a Termux/local working tree where the repo is already cloned and the user is the owner.

## When to use

- User wants to rename their own GitHub repo
- User provides a new `github.com/<owner>/<repo>` slug
- User says "rename", "ganti nama repo", "ganti jadi", "ubah ke", or types a different `<owner>/<repo>` combo

## Pre-flight (fast, parallel)

```bash
gh auth status                              # confirm auth + required scope (delete_repo)
gh repo view <owner>/<old-name> --json name,fullName,defaultBranchRef
```

`gh repo rename` needs **repo admin** scope (`delete_repo` covers it). If `gh auth status` shows wrong account, stop and ask the user — don't switch accounts.

## The seven-step cycle

### 1. Rename on GitHub

```bash
gh repo rename NEW_NAME --yes
```

**Critical flag gotchas (both bite at once):**
- Single positional arg = the new repo name ONLY (not `OLD NEW`)
- `--yes` is current; `--confirm` is **deprecated** and rejected
- The combo `gh repo rename OLD NEW --confirm` errors with `accepts at most 1 arg(s)` AND `Flag --confirm has been deprecated`. Don't combine OLD + NEW; just `NEW --yes`.

### 2. Update local git remote

```bash
cd <repo-dir>
git remote set-url origin https://github.com/<owner>/<new-name>.git
git remote -v   # verify
```

Until this is done, `git push` to the old URL 404s. GitHub auto-redirects the web URL for a while but git+https is unforgiving.

### 3. Grep for in-file references

```bash
grep -rli "<old-name>\|<old-name-dashed>\|<old-name spaced>" \
  --include="*.py" --include="*.md" --include="*.sh" --include="*.yaml" --include="*.yml" \
  --include="*.json" --include="*.txt" --include="*.cfg" --include="*.toml" \
  --include="*.ini" --include="*.example" .
```

Always include `*.example` and `*.txt` — clone instructions and template names often live there.

### 4. Decide what to change vs keep (the hard part)

For each match, classify it:

| Category | Change? | Why |
|---|---|---|
| `github.com/<owner>/<old-name>` URL | **YES** | Repo URL literally moved |
| Title/heading with repo name | **YES** | Visible identity |
| Docstring header like "Repo Picker — …" | **YES** | Title/identity |
| Clone command in README | **YES** | Must match new URL |
| **Software/product name** (e.g. "9Router") | **NO** | It's a different concept |
| Env var prefix (`NINEROUTER_URL`, etc.) | **NO** | API surface, convention |
| Variable/function names referencing product | **NO** | Code identity |
| Description text about the product | **NO** | Product ≠ repo |

**Rule of thumb:** only the **repo name** moves. Anything naming the underlying product, package, env var, or runtime concept stays.

### 5. Patch each occurrence

Use `patch` tool with `mode=replace` per file. Keep `old_string` unique by including 1-2 lines of context. After all patches, re-grep to confirm zero matches for the OLD repo name (allowing for the kept "product name" substrings).

### 6. Commit + push

```bash
git add -A
git commit -m "rename: repo ke <new-name>

- <list of what changed>
- <brief note on what was kept and why>
"
git push origin main
```

### 7. Verify live (run in parallel)

```bash
# Repo exists at new slug
curl -s "https://api.github.com/repos/<owner>/<new-name>" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'name={d[\"name\"]} url={d[\"html_url\"]}')"

# Old slug should 404 (or 301 if redirect window still open)
curl -s -o /dev/null -w "%{http_code}\n" "https://api.github.com/repos/<owner>/<old-name>"

# Local remote points to new URL
git remote get-url origin
```

## Pitfalls

- **`--confirm` is deprecated** → use `--yes`. `gh` will print a deprecation warning but `--confirm OLD NEW` as a pair fails with "accepts at most 1 arg(s)".
- **Two-arg form** `gh repo rename OLD NEW` doesn't work — only the new name goes in. The CLI infers old name from the local `origin` remote.
- **API rename needs a token with `delete_repo` scope**; using `curl -X PATCH /repos/...` returns 401 without one. Prefer `gh` — it has the token.
- **Local remote is NOT auto-updated** by `gh repo rename`. Pushing from an existing clone 404s until step 2.
- **Don't auto-rename the product name** — a repo called `NineRouter-Picker` for software called "9Router" only changes the repo slug, not every mention of "9Router" in the docs.
- **Grep scope misses** — `*.example`, `*.txt`, `*.cfg` files often hold clone URLs; include them in the file glob.
- **Old README clone commands are copy-paste hazards** — if the user clones from the old URL after rename, GitHub's redirect may not survive all clones. Update the README in the same commit.
- **Memory + `~/bin` scripts may reference old repo** — check `~/bin/*.sh` and persistent memory after a rename; update to keep commands working.

## Product rebrand + repo rename

When the repository rename is coupled to a product/CLI rebrand, first lock an exact naming matrix: brand/lab, framework/product, lowercase command, credit line, and whether the internal package remains as a compatibility layer. Repeat the matrix verbatim; never infer near-spellings from earlier context. Work in small verified slices, follow the user's requested order, and exclude unrelated untracked staging directories before commit. See `references/product-rebrand-with-repo-rename.md` for the full sequence and compatibility strategy.

## Verification checklist

- [ ] Exact brand/product/CLI spellings were confirmed before global replacement
- [ ] Rebrand progress was reported in small verified checkpoints rather than one long audit flood
- [ ] Unrelated untracked staging/import directories were excluded from the commit
- [ ] `gh repo view <owner>/<new-name>` returns the repo
- [ ] `git remote -v` shows new URL
- [ ] `grep -r "<old-name>" .` returns no repo-name matches (product-name substrings OK)
- [ ] `git status` is clean after the commit
- [ ] `git push` succeeds
- [ ] API live check shows `name=<new-name>`

## Related

- `references/2026-07-12-9router-picker-rename.md` — full transcript of the canonical session this skill was extracted from


---

## Lampiran: `references/2026-07-12-9router-picker-rename.md`

# Session: 9Router-Picker → NineRouter-Picker rename

**Date:** 2026-07-12
**Repo:** `user/9Router-Picker` → `user/NineRouter-Picker`
**Type:** Public repo, owner = user

## Why

User wanted the repo slug to read `NineRouter-Picker` (not `9Router-Picker`). The underlying product is still "9Router" — that name did NOT change anywhere.

## What ran (in order)

```bash
# 1. confirm auth + scope
gh auth status
# ✓ user, scope includes 'delete_repo'

# 2. rename (single arg, --yes)
gh repo rename NineRouter-Picker --yes
# silent on success

# 3. update local remote
cd ~/ninerouter-picker
git remote set-url origin https://github.com/user/NineRouter-Picker.git

# 4. grep for old references
grep -rli "9Router-Picker\|9Router Picker" \
  --include="*.py" --include="*.md" --include="*.sh" \
  --include="*.yaml" --include="*.yml" --include="*.json" \
  --include="*.txt" --include="*.cfg" --include="*.toml" \
  --include="*.ini" --include="*.example" .

# 5. 5 matches across 2 files:
#   README.md L1, L25
#   picker.py L3, L322, L359

# 6. patch all 5 (kept all "9Router" software-name mentions)
# 7. commit + push
git add README.md picker.py
git commit -m "rename: repo ke NineRouter-Picker ..."
git push origin main
# 97a4ff7..0f349ae  main -> main

# 8. verify live
curl -s "https://api.github.com/repos/user/NineRouter-Picker" \
  | python3 -c "..."  # name=NineRouter-Picker, full_name ok
```

## What was kept vs changed

**Changed (5 sites):**
- `README.md` H1: `# 9Router Picker` → `# NineRouter Picker`
- `README.md` L25: `git clone ...9Router-Picker` → `...NineRouter-Picker`
- `picker.py` L3: docstring header
- `picker.py` L322: `**9Router Picker**` in `render()`
- `picker.py` L359: argparse `description=`

**Kept (7+ sites in README + 2 in picker.py):**
- Body text "9Router" — software name
- `.env` variable `NINEROUTER_URL` / `NINEROUTER_KEY` — env var convention
- Reference to "9Router/endpoint" as the type of thing picker talks to

## Gotchas hit

1. First try: `gh repo rename user/9Router-Picker user/NineRouter-Picker --confirm`
   - Two errors at once: `accepts at most 1 arg(s)` and `Flag --confirm has been deprecated`
2. Second try: `gh repo rename NineRouter-Picker --yes` — silent success
3. `curl -X PATCH` for the rename returned 401 (no token in hand) — `gh` was the right path

## Post-session memory fix

The persistent memory entry had `user/ninerouter-picker` (lowercase) — wrong casing of the GitHub-side slug. Updated to `user/NineRouter-Picker` to match the actual repo after rename.



---

## Lampiran: `references/product-rebrand-with-repo-rename.md`

# Product rebrand coupled to a GitHub repo rename

## Durable workflow lessons

When a repository rename is part of a broader product rebrand, lock the naming system before global edits:

```text
Brand/lab: <exact spelling>
Framework/product: <exact spelling>
CLI command: <exact lowercase spelling>
Credit: <exact display text>
Internal compatibility package: keep or rename
```

Repeat this matrix verbatim and get confirmation if any spelling conflicts with earlier messages. Do not infer near-spellings such as `Zeline` vs `Zenline`, or `Zerolinear` vs `Zeroline`.

Apply changes in small vertical slices and report each checkpoint:

1. Banner/visible identity.
2. CLI entry point and parser.
3. Package metadata and installer.
4. Runtime text, gateway output, docs, assets.
5. Data/env migration with compatibility fallbacks.
6. Full tests/build/spelling scan.
7. GitHub repo rename, remote update, PR, CI, merge.

Keep internal import paths temporarily when a full package rename would break integrations. Make the new public API canonical and retain old names only as documented compatibility aliases.

Before staging, explicitly exclude unrelated untracked directories. A broad `git add -A` can accidentally publish temporary import/staging corpora.

If docs point to the new repository slug, rename the live GitHub repository before publishing those docs; otherwise install URLs can temporarily 404. After rename, update `origin`, installer URLs, package URLs, README clone commands, and verify the live repository endpoint.

## Communication preference learned

For this user's rebrand work, avoid a long audit flood. Give short progress updates after each small verified slice, and follow the user's requested sequence (for example, complete rebrand before unrelated CLI features).
