# Telegram Inline Picker

> Build standalone Telegram bots with inline-keyboard picker UIs for multi-option selection from dynamic data sources (multi-provider APIs, model catalogs, config groups, tool palettes). Use when user wants `/command` → drill-down menu (category → item → confirm) in chat, NOT a webview mini-app. Co...

Pattern: standalone Telegram bot presenting a hierarchical inline-keyboard picker over a dynamic data source (API, local file, env config). User runs `/command` → sees category buttons → drills into items → confirms. Edit-message-in-place on every callback (no spam of new messages).

## When to Use

Trigger this skill when ANY of these is true:

- User wants a Telegram bot that shows a list of selectable options
- Options come from a **dynamic source** (API endpoint, file, env, list of processes) — not a hardcoded enum
- Sub-grouping by some key in the data makes sense (vendor, namespace, prefix)
- User wants to **scope/filter** the list (only items they have access to, only working, only enabled)
- Setup should be **standalone**, not bolted into an existing framework bot (Zeline gateway, etc.)

**Don't use this skill when:**
- User wants a full web UI inside Telegram → use `telegram-mini-app` (webview) or `telegram-mini-app-web3`
- Options are static (hardcoded) → just use `CallbackQueryHandler` directly, no skill needed
- Selection is a single yes/no or one-of-N from <5 options → inline button row, not multi-level
- User is building inside Zeline gateway → patch `~/.zeline/zeline/plugins/platforms/telegram/adapter.py` instead

## Architecture

Always split into two files:

```
project/
├── picker.py       # core: fetch data, group, filter, state persist
├── bot.py          # Telegram handlers, callbacks, keyboard rendering
├── run.sh          # background starter
├── stop.sh         # PID-based stopper
├── requirements.txt
├── .env.example    # template (safe to commit)
├── .gitignore
└── README.md
```

**Why split:** `picker.py` is testable from CLI without Telegram (`python3 picker.py` prints grouped output). `bot.py` is a thin UI layer. This separation made it possible to catch the "I assumed 14 models, real API has 41 with nested structure" bug at the picker layer before wiring up Telegram.

## Core Picker Pattern

Key design rules — apply ALL of these:

### 1. Auto-detect categories from data, don't hardcode

```python
def _vendor_of(item_id: str) -> str:
    return item_id.split("/", 1)[0] if "/" in item_id else "other"
```

User feedback: *"kalo mau nambahin provider lain bisa? kaya Alibaba gitu nanti masuknya ke picker baru"* — picker must accept new categories without code changes.

Optional override dict for custom labels:
```python
VENDOR_LABELS = {"alibaba": "Aliyun"}  # override only; default = capitalize(prefix)
```

### 2. Handle nested path structure gracefully

APIs often return flat AND nested items in the same response. Example (9Router):
- `bai/glm-5.2` — flat, vendor "bai", tail "glm-5.2"
- `nvidia/deepseek-ai/deepseek-v4-pro` — nested, vendor "nvidia", tail "deepseek-v4-pro"

Different slicing for different purposes:
```python
def _vendor_of(item_id): return item_id.split("/", 1)[0]        # for grouping
def _tail_of(item_id):   return item_id.rsplit("/", 1)[-1]      # for display
```

**Critical lesson:** always fetch and inspect the real API response before designing group logic. I once assumed flat structure; the actual API had mixed nesting and the group function had to be rewritten.

### 3. Show-all-by-default, filter is opt-in

User feedback: *"default model nya semua, tp khusus gua di filter, ya kasih pilihan aja si atau kasih default semua nanti bisa hapus yang ga di pake"*.

- First load: show all items
- Provide `/register` command to **whitelist** (toggle on/off per item)
- When whitelist is non-empty, `/model` filters down to it
- When whitelist is empty, `/model` shows everything (with a hint to use `/register`)

### 4. Persist state to plain JSON, not a database

```python
# registered.json: {"registered": ["item1", "item2"], "updated_at": 1234567890}
# current.json:    {"model": "item1", "ts": 1234567890}
```

Add both to `.gitignore`. They're per-user runtime state, not source.

### 5. Cache external API calls with TTL + manual refresh

```python
CACHE_TTL = 300  # 5 min
# Cache file: .models_cache.json
# Provide a "🔄 Refresh" button in picker that busts cache and re-fetches
```

### 6. Optional "test item" function

For APIs that expose a probe endpoint (chat completion, health check), provide a quick test:
```python
def test_item(item_id: str) -> tuple[bool, str]:
    """Return (ok, error_msg). Used by /current command."""
```

User uses this to verify the active item still works (rate limit, billing, upstream issue).

### 7. Skip noisy items by substring (optional, configurable)

```python
SKIP_FREE = os.environ.get("SKIP_FREE", "true").lower() in ("1", "true", "yes")
def _is_skipped(item_id: str) -> bool:
    return SKIP_FREE and "free" in item_id.lower()
```

## Telegram Bot Pattern

- Use **`python-telegram-bot` v21+** (async API). v20+ uses `async/await`; v13 was sync. Don't mix.
- **Callback data format**: `<action>:<id>` (e.g., `v:bai` for vendor drill, `m:bai/glm-5.2` for model pick, `rr:foo` for register toggle).
- **64-byte limit** on `callback_data`. For long ids, store id→short_index in state, or truncate.
- **Render in 2-column rows.** Max 8 buttons per page (split into multiple messages if more).
- **Edit message in-place** on every callback. Don't send a new message per tap — that spams the chat.
- **Always include `« Back` and `✗ Cancel`** in sub-menus. `Back` callback should be `back:main` and re-render the root picker.
- **Auth check early**: if `ALLOWED_USER_ID` env var is set, reject other users before any handler logic. Cheap denial of service prevention.

Skeleton:
```python
async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    if not _is_authorized(query.from_user.id): return
    data = query.data or ""
    if data == "cancel": return await cb_cancel(...)
    if data.startswith("v:"): return await cb_vendor(query, data.split(":", 1)[1])
    # ...
```

## Security Checklist — Pre-Push to GitHub (CRITICAL)

Bot projects have high-value secrets (Telegram bot tokens, upstream API keys). User explicit concern: *"serius aman? token bot gua sama 9router gua ga di publik kan, nanti gua kenapa kenapa lagi"*.

Mandatory checklist before `git push`:

1. **`.gitignore` MUST exclude**:
   - `.env`, `.env.*` (but NOT `.env.example`)
   - `*.log`, `logs/`
   - State files: `registered.json`, `current.json`, `*.cache.json`
   - `__pycache__/`, `*.pyc`, `.venv/`, `venv/`

2. **`.env.example` is safe to commit** — placeholder values only, with comment "fill in your own".

3. **Pre-push verification commands** (run these BEFORE pushing):
   ```bash
   git status                 # .env must NOT appear in red (untracked)
   git diff --cached | grep -E 'BOT_TOKEN|rc_proxy_key|ghp_|sk-'   # must be empty
   # Optional: pip install detect-secrets && detect-secrets scan .
   ```

4. **Use GitHub Secret Scanner as backup, not primary** — it auto-blocks pushes with `ghp_*` and similar patterns, but don't rely on it. Keep secrets out of history from day one.

5. **Repo default to private** for bot projects with API keys. `gh repo create OWNER/REPO --private`.

6. **Termux gotcha**: `/usr/bin/env` does not exist (only `/bin/env` symlink to toybox). Scripts with `#!/usr/bin/env bash` crash silent. Use absolute path `#!/data/data/com.termux/files/usr/bin/bash` in `run.sh` / `stop.sh`. Always `head -1 <script>` to verify shebang before running.

## Build → Push Workflow

```bash
# 1. Scaffold
mkdir ~/project && cd ~/project
python3 -m venv venv
source venv/bin/activate
pip install httpx python-telegram-bot
pip freeze > requirements.txt

# 2. Write code (picker.py, bot.py)
# 3. Test picker standalone (no Telegram needed)
python3 picker.py    # prints grouped output, verifies fetch + group + filter

# 4. Wire up Telegram
cp .env.example .env
nano .env             # fill real values
python3 bot.py        # smoke test interactively (Ctrl-C after /start works)

# 5. Push
git init -b main
git add .
git status            # verify .env NOT in list
git diff --cached | grep -E 'rc_proxy_key|BOT_TOKEN'   # must be empty
git commit -m "Initial commit"
gh repo create OWNER/REPO --private --description "..." --source=. --push
```

## Pitfalls

- **Always fetch real API first, design after.** I assumed 14 models and flat structure; actual API was 41 models with mixed flat/nested. Re-architect is expensive after UI is wired up.
- **`gh repo create --source=.` requires `git init` first.** Error "current directory is not a git repository" if you skip. Don't combine in one call.
- **Callback data >64 bytes silently fails.** Telegram API rejects; the button does nothing. Pre-validate all callback_data strings against id length.
- **Don't re-fetch on every callback.** Cache list with 5-min TTL; provide explicit "🔄 Refresh" button. Otherwise the picker hammers the API on every Back/Forward tap.
- **Nesting depth matters.** 1 level (vendor → model) is comfortable. 2 levels (vendor → org → model) is OK. 3+ levels gets users lost — flatten or pre-group.
- **Test on real data, not mocks.** API response quirks (nested paths, special chars, duplicates) only show up live. Run `python3 picker.py` once before pushing.
- **Termux background process needs `nohup` + `&`** — Termux kills child processes on app suspend otherwise. `run.sh` must do `nohup python3 bot.py >> logs/bot.log 2>&1 &` and write PID to `logs/bot.pid`.
- **Don't assume the user's data layout.** When user says "all models", they often mean "all models **I have access to**". Default to show-all, but make filter discoverable.

## See Also

- `references/9router-picker.md` — full case study: Telegram picker for 9Router (multi-provider LLM aggregator, 41 models exposed, ~14 accessible on free tier). Source of every design decision in this skill.
- `templates/picker-template.py` — forkable starter skeleton (copy, replace `fetch_items` and label dicts, done).
- `scripts/pre-push-check.sh` — pre-`git push` security scan: verifies `.env` untracked, no secret patterns in staged diff, Termux-safe shebangs.
- `telegram-mini-app` — if user wants a full web UI inside Telegram (webview) instead of inline keyboards
- `telegram-commerce-bot` — full-auto shop bot with QRIS payment (Tripay), auto delivery, stock management. Uses this inline-keyboard pattern for the catalog UI.
- `zeline-custom-provider-setup` — if the picker should be part of Zeline gateway config (not standalone)
- `9router-management` — for managing the 9Router Node.js app itself (not clients of it)


---

## Lampiran: `references/9router-picker.md`

# Case Study: 9Router Picker

Live implementation reference for `telegram-inline-picker`. Project: `~/ninerouter-picker/` (also published as private repo `user/ninerouter-picker`).

## Context

- **Tool**: 9Router (Node.js, port 20128). Multi-provider LLM aggregator exposing OpenAI-compatible API.
- **Upstream providers** (July 2026): BAi (33 models flat), Nvidia (8 models with `org/name` nesting). Total 41 exposed. User has free-tier access to ~14.
- **Picking problem**: 41 options is too many to scroll. User wants Telegram drill-down: vendor → model, filtered to what works.
- **Zeline gateway already has a `/model` picker** with `_build_9router_vendor_keyboard` patch. But user wanted a standalone bot they could control without depending on Zeline updates.

## Files

```
ninerouter-picker/
├── picker.py   # fetch_models, group_by_vendor, load/save_registered, test_model, _self_test
├── bot.py      # cmd_start/help/model/register/current, cb_vendor/pick/toggle/refresh
├── run.sh      # Background starter, nohup + PID file
├── stop.sh     # Kill via PID file
├── .env.example
├── .gitignore  # excludes .env, logs/, registered.json, current.json, .models_cache.json
└── README.md
```

## Key Design Decisions

1. **Auto-detect vendor from prefix before first `/`**. So `nvidia/deepseek-ai/deepseek-v4-pro` and `bai/glm-5.2` both work without per-provider code.

2. **Two-mode picker**: `/model` (filtered by registered) and `/register` (toggle whitelist). When registered is empty, `/model` shows everything + a hint to use `/register`. Direct user request: "default model nya semua, tp khusus gua di filter".

3. **Skip-free toggle** via `SKIP_FREE_MODELS=true` env var. Default true. Substring match on "free" in id.

4. **Two-state JSON files** (`registered.json`, `current.json`) instead of SQLite. User can hand-edit if needed. Both gitignored.

5. **5-min model cache** to avoid hammering 9Router's `/v1/models` on every tap. "🔄 Refresh" button busts cache.

6. **Inline keyboard layout**: 2 columns for buttons, last row is `« Back` / `✗ Cancel`. Drill down via `v:<vendor>` → `m:<model_id>`. Edit-message-in-place every callback.

## What I Got Wrong First

- **Assumed 14 models.** Memory said "9Router 14 models curated". Actually 41 exposed by API. Fetch first, design after.
- **Assumed flat structure.** All `bai/*` are flat (`bai/glm-5.2`), but `nvidia/*` are double-nested (`nvidia/deepseek-ai/deepseek-v4-pro`). Two different slicing functions: `_vendor_of` (first slash) vs `_tail_of` (last slash) for display label.
- **`_is_registered` lambda defined after `if mode == "model"` check** that referenced it. Moved the lambda up to scope.

## Test Workflow Used

1. `python3 picker.py` — verify fetch + group + filter prints correctly
2. `picker.test_model("bai/glm-5.2")` — verify the test function (it returned 403 from 9Router with "Deposit required" — which was correct: confirms filter matters)
3. `python3 -c "import bot"` — verify all imports resolve
4. `git status` before push — verify `.env` is untracked, no secrets in diff

## Publishing Result

- 2 commits, clean tree, no secrets.
- `gh repo create --private --source=. --push` worked first try after `git init -b main`.
- User requested installing on Termux: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt && cp .env.example .env`. User then fills `BOT_TOKEN` in `.env` and runs `./run.sh`.

## Reusable Components

The exact `picker.py` and `bot.py` files are not in this skill (they live in the published repo). For a fresh picker for a different data source, copy them and replace:
- `fetch_models()` → your data source
- `VENDOR_LABELS` / `MODEL_LABELS` → your overrides
- `test_model()` → optional, your probe endpoint
- `_vendor_of()` / `_tail_of()` → your grouping key extraction

## Memory Note

User memory contained "9Router 14 models curated" — this is now stale. Real exposed count is 41. If this session re-resumes, query `curl http://127.0.0.1:20128/v1/models` instead of trusting the cached number.



---

## Lampiran: `templates/picker-template.py`

```py
# Standalone Telegram inline-keyboard picker template.
# Copy and modify for your data source.
#
# Layout:
#   project/
#   ├── picker.py   # <-- REPLACE: your fetch_models, group, filter
#   ├── bot.py      # <-- minor edits: only if your callback flow differs
#   ├── run.sh
#   ├── stop.sh
#   ├── requirements.txt
#   ├── .env.example
#   ├── .gitignore
#   └── README.md
#
# Steps to fork:
#   1. cp -r telegram-picker-template/ mypicker/ && cd mypicker/
#   2. Edit picker.py:
#        - API_URL -> your endpoint
#        - fetch_items() -> your fetch
#        - CATEGORY_LABELS / ITEM_LABELS -> your overrides
#        - test_item() -> your probe (or remove /current command)
#   3. Edit .env.example: rename to your vars
#   4. Edit README.md: replace domain-specific sections
#   5. Add shebang at top of run.sh that points to your venv python

# --- picker.py skeleton ---

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

# === CONFIG (override via env) ===
API_URL = os.environ.get("API_URL", "http://127.0.0.1:PORT").rstrip("/")
API_KEY=os.env...Y", "")
SKIP_KEYWORD = os.environ.get("SKIP_KEYWORD", "free").lower()  # set "" to disable

# Custom labels (optional). Unlisted keys = auto "Capitalize(prefix)".
CATEGORY_LABELS: Dict[str, str] = {
    # "alibaba": "Aliyun",
}
ITEM_LABELS: Dict[str, str] = {
    # "category/item-name": "Display Name",
}

STATE_DIR = Path(__file__).parent
REGISTERED_PATH = STATE_DIR / "registered.json"
CURRENT_PATH = STATE_DIR / "current.json"
CACHE_PATH = STATE_DIR / ".cache.json"
CACHE_TTL = 300


def fetch_items() -> List[str]:
    """Fetch list of item ids from YOUR API. Override for non-OpenAI shape."""
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text())
            if time.time() - cache.get("ts", 0) < CACHE_TTL:
                return cache["items"]
        except Exception:
            pass
    headers = {"Authorization": f"Bearer {API_KEY}"}
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{API_URL}/v1/YOUR_ENDPOINT", headers=headers)
        r.raise_for_status()
        data = r.json()
    items = sorted({i["id"] for i in data.get("data", [])})
    try:
        CACHE_PATH.write_text(json.dumps({"ts": time.time(), "items": items}))
    except Exception:
        pass
    return items


def _category_of(item_id: str) -> str:
    return item_id.split("/", 1)[0] if "/" in item_id else "other"

def _tail_of(item_id: str) -> str:
    return item_id.rsplit("/", 1)[-1]

def _category_label(cat: str) -> str:
    return CATEGORY_LABELS.get(cat, cat.capitalize())

def _item_label(item_id: str) -> str:
    return ITEM_LABELS.get(item_id, _tail_of(item_id))

def _is_skipped(item_id: str) -> bool:
    return bool(SKIP_KEYWORD) and SKIP_KEYWORD in item_id.lower()


def group_by_category(items: List[str], registered: Optional[List[str]] = None) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for i in items:
        if _is_skipped(i):
            continue
        if registered is not None and i not in registered:
            continue
        out.setdefault(_category_of(i), []).append(i)
    for k in out:
        out[k].sort()
    return dict(sorted(out.items()))


# State I/O
def load_registered() -> List[str]:
    if not REGISTERED_PATH.exists():
        return []
    try:
        return json.loads(REGISTERED_PATH.read_text()).get("registered", [])
    except Exception:
        return []

def save_registered(ids: List[str]) -> None:
    REGISTERED_PATH.write_text(
        json.dumps({"registered": sorted(set(ids)), "updated_at": time.time()}, indent=2)
    )

def load_current() -> Optional[str]:
    if not CURRENT_PATH.exists():
        return None
    try:
        return json.loads(CURRENT_PATH.read_text()).get("item")
    except Exception:
        return None

def save_current(item_id: str) -> None:
    CURRENT_PATH.write_text(json.dumps({"item": item_id, "ts": time.time()}, indent=2))


# Optional probe
def test_item(item_id: str) -> Tuple[bool, str]:
    """Quick probe -- return (ok, error_msg). Remove if not applicable."""
    return True, ""


if __name__ == "__main__":
    items = fetch_items()
    registered = load_registered()
    grouped = group_by_category(items, registered=registered if registered else None)
    print(f"Total: {len(items)}, Registered: {len(registered)}")
    for cat, ms in grouped.items():
        print(f"\n[{_category_label(cat)} ({len(ms)})]")
        for m in ms:
            print(f"  - {_item_label(m):30s}  ({m})")

```


---

## Lampiran: `scripts/pre-push-check.sh`

```sh
#!/data/data/com.termux/files/usr/bin/bash
# Pre-push security check. Run before git push on any bot project.
# Exits non-zero if any real secret is found in staged content.
set -e
cd "$(dirname "$0")/.."

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

echo "=== Bot project: pre-push security check ==="

# 1. .env must be untracked
if git ls-files --error-unmatch .env 2>/dev/null; then
    echo -e "${RED}FAIL: .env is tracked in git! Remove with: git rm --cached .env${NC}"
    exit 1
fi
echo -e "${GREEN}[1/5] .env not tracked${NC}"

# 2. .gitignore covers secrets
MISSING=0
for pat in ".env" "logs/" "registered.json" "current.json" "*.cache.json"; do
    if ! grep -qF "$pat" .gitignore 2>/dev/null; then
        echo -e "${YELLOW}[2/5] WARN: .gitignore missing '$pat'${NC}"
        MISSING=$((MISSING+1))
    fi
done
if [ $MISSING -eq 0 ]; then
    echo -e "${GREEN}[2/5] .gitignore covers all expected patterns${NC}"
fi

# 3. No real secrets in staged content
SECRET_PATTERNS=("ghp_*" "sk-[A-Za-z0-9]{20,}" "rc_proxy_key" "AKIA[0-9A-Z]{16}")
FOUND=0
for pat in "${SECRET_PATTERNS[@]}"; do
    HITS=$(git diff --cached 2>/dev/null | grep -E "$pat" || true)
    if [ -n "$HITS" ]; then
        echo -e "${RED}[3/5] FAIL: pattern '$pat' found in staged content${NC}"
        echo "$HITS" | head -3
        FOUND=1
    fi
done
if [ $FOUND -eq 0 ]; then
    echo -e "${GREEN}[3/5] No secret patterns in staged content${NC}"
fi

# 4. .env.example exists
if [ -f .env.example ]; then
    echo -e "${GREEN}[4/5] .env.example present${NC}"
else
    echo -e "${YELLOW}[4/5] WARN: .env.example missing${NC}"
fi

# 5. Termux shebang check
if [ -f run.sh ] || [ -f start.sh ]; then
    for f in run.sh start.sh; do
        [ ! -f "$f" ] && continue
        SHEBANG=$(head -1 "$f")
        if [[ "$SHEBANG" == *"/usr/bin/env"* ]]; then
            echo -e "${YELLOW}[5/5] WARN: $f uses /usr/bin/env (broken on Termux)${NC}"
            echo "  Use absolute path: #!/data/data/com.termux/files/usr/bin/bash"
        else
            echo -e "${GREEN}[5/5] $f shebang OK${NC}"
        fi
    done
else
    echo "[5/5] No run.sh / start.sh to check"
fi

if [ $FOUND -eq 1 ]; then
    echo -e "${RED}=== CHECK FAILED — DO NOT PUSH ===${NC}"
    exit 1
fi
echo -e "${GREEN}=== All checks passed — safe to push ===${NC}"

```
