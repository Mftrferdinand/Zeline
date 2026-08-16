# Build Llm Agent From Scratch

> Build a minimal AI agent engine from scratch in Python — the LLM + loop + tools core, tool-calling, persona, CLI. Use when the user wants to create their own agent (like Zeline) rather than delegate to an existing CLI or rebrand Zeline.

For when the user wants to *understand and own* an agent engine, not just use one. The whole idea in one line: **an agent = an LLM inside a LOOP that is allowed to call TOOLS.** Everything else is polish.

## When to use
- User says "I want to build my own AI agent like Zeline / from scratch / to learn."
- They pick the "build the engine" path over "rebrand Zeline" (fast, no-code) or "delegate to Claude Code/Codex CLI."
- Target is Python (richest LLM ecosystem, most beginner-friendly). Same architecture ports to TS/Node.

If they instead want a working agent *now* with minimal code → rebrand Zeline. If they want to offload coding to an existing agent CLI → see `autonomous-ai-agents` category.

## Architecture — 4 files, separated for learnability
1. **config.py** — settings + persona (system prompt). Keep secrets outside source (user config, environment, or a secret manager), never hardcode them. For a personal local experiment, reading an existing config can avoid duplication; for a distributable/public package, importing another app's credentials must be an explicit opt-in migration—not automatic behavior.
2. **tools.py** — capabilities. Each tool = a *schema* (OpenAI function-calling JSON, tells the LLM the tool exists) + a *Python function* (the real code). Keep a `TOOLS` dict (name→fn) and a `TOOL_SCHEMAS` list.
3. **agent.py** — the core loop. THIS is the agent.
4. **main.py** — CLI: banner, input loop, `on_tool` callback to show tool calls, `keluar/exit` to quit.

## The core loop (agent.py) — the essential algorithm
```
messages = [ {system: persona} ]
def send(user_input):
    messages.append(user)
    for _ in range(MAX_TOOL_ROUNDS):   # cap ~12, prevents infinite loops
        msg = call_llm(messages, tools=TOOL_SCHEMAS, tool_choice="auto")
        if not msg.tool_calls:          # (a) plain text → final answer
            messages.append(assistant msg); return content
        # (b) LLM wants tools:
        messages.append(assistant msg WITH tool_calls)   # MUST save this first
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = TOOLS[name](**args)
            messages.append({role:"tool", tool_call_id: tc.id, content: str(result)})
        # loop again — ask LLM with tool results in hand
```
Critical ordering rule: after the LLM requests tools you must append the assistant message *with its tool_calls* BEFORE appending the `role:"tool"` results, and each tool result must carry the matching `tool_call_id`. Wrong order = API 400.

## Pitfalls learned (Termux + 9Router)
- **Don't fight `pydantic-core` on Termux.** The `openai` SDK pulls pydantic → pydantic-core, which needs a Rust compile and hangs/fails on Termux. Skip the SDK: POST to `/v1/chat/completions` directly with `requests`. Payload is identical standard-OpenAI shape, so the learning transfers 1:1. (`annotated_types` may also be missing — `pip install annotated_types` is cheap, but the real fix is avoiding the SDK.)
- **9Router returns `content-type: text/event-stream` even with `stream:false`.** `r.json()` then throws `JSONDecodeError: Extra data`. Fix: parse the first JSON object yourself — strip a leading `data:` if present, try `json.loads`, on failure use `json.JSONDecoder().raw_decode(text)` and take the first object. See `references/nine-router-agent-notes.md`.
- Add `"stream": False` explicitly in the payload anyway.
- **Probe model routes before committing a default — router route health drifts.** A model that worked last week can 410 (gone) or 403 (upstream balance exhausted, e.g. "预扣费额度失败, 剩余额度 $0.11, 需要 $0.20") today. Also: if you read the model from an existing config (Zeline `custom_providers.<name>.model`), that value can be stale/dead too and will silently override your default — precedence must be **env > your hardcoded DEFAULT_MODEL > config-file model** (or skip the config model entirely). Before wiring the default, POST a minimal `{model, messages:[hi], stream:false}` to each candidate and keep the first 200. `Vibe/`-prefixed routes on this router have been the healthy ones.

## Memory + Skills subsystems (persistence, verified pattern)
Once the loop runs, add persistence so the agent remembers across sessions and can load procedures on demand. Same architecture Zeline uses, minimal form:
- **memory.py** — JSON file (`memory.json`), functions `add_memory(fact)` (dedupe), `remove_memory(substring)`, `list_memory()`, plus `memory_block()` → `\n## Yang kamu ingat:\n- ...` injected into the system prompt **at session start** (Zeline class __init__), so every new session starts knowing the facts. Tools registered: `add_memory`, `remove_memory`, `list_memory`.
- **skills.py** — skills = markdown files in `skills/`. Convention: line 1 `# Judul` = name, line 2 `> deskripsi` = description, rest = procedure. List only name+description in the system prompt (token-cheap); full content lazy-loaded via a `load_skill(name)` tool when the agent actually needs it. Also `save_skill(name, content)` so the agent can write its own procedures. `skills_block()` joins the list into the system prompt.
- System prompt = persona + memory_block() + skills_block() + a rule line ("use add_memory for important facts; call load_skill before doing skill-related work").
- Test cross-session: session 1 "ingat fakta X" → assert memory.json contains it; NEW Zeline() (simulates restart) → "kamu ingat siapa aku?" → must answer from memory, not from chat history.

### Bundling an external skill corpus (batch import, verified)
When the user hands you a large third-party skill pack (e.g. a zipped agent framework with 80+ `.md` skills) to bundle as built-in public skills:
- **Copy into the package's bundled skills dir, not the user's data dir.** `seed_skills()` copies `zeline/skills/*.md` → `~/.zeline/skills/public/` (never overwriting customized ones). So drop the imported files into the source `zeline/skills/` and they seed on next `setup` or an explicit `skills.seed_skills()` call. Seeding is idempotent — returns count of newly-copied files.
- **Dedup before importing.** Skill packs ship reference-only stubs marked `# ⚠️ MERGED into X.md` on line 1, plus legacy duplicate variants of the same content (e.g. `m*`/`x*` mirrors of the canonical `sk*` series). Skip both: filter the MERGED stubs by first-line check, and keep only the canonical series to avoid a doubled corpus. State clearly to the user which variants you dropped and offer to include them if wanted.
- **Namespace + normalize on import.** Prefix names (`zeline-zenith-<stem>`) so they don't collide with the agent's own skills, and rewrite each file's first heading + `>` blockquote into the agent's expected skill format (`# <desc>` / `> <desc>`), since `_parse()` reads line-1 `# ` as name and first `>` as description for `skills_block()`. Do this in one `execute_code` pass over the files, not file-by-file.
- **Rework the system prompt into a runtime persona** when bundling a persona-driven pack: make it execution-first + skill-aware (explicit "call load_skill before acting" rule) + anti-fabrication, and add a regression test asserting the persona wording is present in `SYSTEM_PROMPT` and the seeded corpus is loadable via `load_skill`.

### Restarting the gateway after code changes — use the framework's own start/stop
After editing agent/gateway code you must restart the running gateway. **Do NOT `kill <pid>` + `nohup ... run &` manually.** The pidfile is written by `gateway_service.start()` and carries the verified `pid + /proc start-ticks` pair; a manual kill leaves the OLD pidfile in place, so `gateway status` keeps reading the stale PID and reports "not running" (or worse, a reused PID) even though a real process is up. Correct sequence:
1. `zeline gateway stop` (clears stale/mismatched pidfile — it fails closed and removes unverified state).
2. Re-seed if you added bundled skills: `python -c "from zeline import skills; print(skills.seed_skills())"`.
3. `zeline gateway start` (spawns background service, writes fresh verified pidfile, prints new PID + log path).
4. `zeline gateway status` + `zeline gateway log` to confirm the new PID is alive and the platform connected (e.g. "@bot terhubung via polling").
A transient `polling error: ConnectionError` line right after start is usually a one-off network blip, not a fatal — confirm via a fresh `status`/`log` rather than assuming failure.

## Public framework mode — installable by other people

When the requirement changes from “make my bot work” to “other people must install it like Zeline and connect their own Telegram/WhatsApp,” treat the deliverable as a **public framework release**, not a one-off bot script.

### Required design changes

- Package it with `pyproject.toml`, a console command, and state under `~/.<agent>/` (or a test-friendly home override). Source code, secrets, memory, logs, and runtime PID state must not live together.
- Setup wizard collects the user’s own OpenAI-compatible provider and platform credentials. Secrets use hidden prompts and are masked in normal status output.
- Namespace all session/memory identities by platform (`telegram:<id>`, `whatsapp:<jid>`, `webhook:<id>`, `cli:local`) and lock/bound the session cache.
- Gateway users default to `safe` capabilities. Only a local owner `full` profile receives shell access; workspace file access must stay beneath a resolved configured root.
- Separate bundled/public skills from owner/private runbooks. Safe gateways cannot list or load private skills.
- Make memory untrusted data in the system prompt, not instructions; limit its per-user and global storage footprint.
- Adapter registry contract: `info()`, `validate_config(cfg)`, and blocking `start(session_store, cfg, stop_event)`. Validate before spawning a background service.
- Ship lifecycle commands: foreground `gateway run`, plus background `start|stop|status|log` without a shell wrapper. Persist PID/log state privately and clean stale state.
- **Never trust a PID alone:** PID reuse can make `stop` terminate an unrelated process. Store `pid` plus Linux/Android `/proc/<pid>/stat` start-time ticks; before status, duplicate-start detection, or SIGTERM, require the live process start-time to match. Old state without a verified identity must fail closed and be removed—not signalled.
- Gateway specifics: Telegram persistent offset + message split; localhost-token-protected webhook; WhatsApp Baileys bridge with runtime bridge-token authentication and `fromMe` anti-loop.

### Release gate

Do not call it ready until tests cover tool ordering, history trim at a user boundary, capability profiles, scoped skills, memory isolation/limits, gateway validation, webhook authentication, and service lifecycle. Build a wheel, inspect it for leaked local state, install it into an isolated location, and run a real smoke flow using a fake local provider:

```text
gateway start → /health → authenticated message → reply → status → stop
```

See `references/public-agent-framework.md` for the complete reusable checklist, adapter patterns, and Termux installer notes.

## Telegram gateway (make it public, "like Zeline")

Once the CLI agent works, the user will ask to make it usable publicly — that means a Telegram bot, same as Zeline. Pattern verified: raw Bot API long-polling with plain `requests`, NO `python-telegram-bot` (it pulls httpx → pydantic → pydantic-core, the same Termux compile trap as the `openai` SDK).

- **config.py**: add a tiny `.env` loader (`os.environ.setdefault` per line, project-local file) then `TELEGRAM_TOKEN = os.environ.get("ZELINE_TG_TOKEN", "")`. Bot token comes from @BotFather; user pastes it into `~/<project>/.env` — never into code.
- **telegram_bot.py**: long-poll `getUpdates` with `offset` + `timeout=30` (HTTP timeout must exceed it, e.g. 65s — poll timeout is NOT an error), one `Zeline` session per chat_id (isolated histories), `sendChatAction(typing)` before processing, split replies at ~4000 chars (Telegram hard cap 4096).
- **Allowlist**: `TELEGRAM_ALLOWED` env (comma-separated chat IDs); empty = public. For a personal bot set it to the owner's user id — default-open means anyone who finds the handle burns your router credit.
- **Ops**: `start-tg.sh start|stop|status|log` (nohup + PIDFILE); `install.sh` writes a global `zeline-tg` launcher and supports `--pack`/`--unpack` tar backup so the whole agent folder (memory + skills included) migrates to another device — polling dies with Termux, so 24/7 public use means moving the folder to a VPS.
- **Document uploads**: distinguish the raw download cap from extracted text/context caps. A 256 KB pre-download cap rejects ordinary project ZIPs long before Telegram or the provider sees them. A practical download cap is 20 MB; allow a practical archive member count (e.g. 512), while traversal validation, per-entry size, and total extracted text remain tightly bounded. **Budget the final agent input, not just extracted text:** filename, caption, markdown fences, and headers consume characters too. Build the complete document prompt first (or reserve its overhead) and truncate it to the agent's exact input limit (e.g. 16,000) before calling `sessions.send`; otherwise the agent returns its own “message too long” error. Add mocked 729 KB-download, >64-member ZIP, and final-prompt-at-limit regression tests, then restart the gateway and verify its new PID. See `references/telegram-document-size-policy.md`.
- Full loop code, message splitting, and pitfalls: `references/telegram-gateway-raw-botapi.md`.

## Verify for real
Write a throwaway `_test.py` that exercises 3 paths and RUN it (delete after):
1. plain chat (no tool) → persona replies
2. write_file then read_file → confirm the file actually exists on disk (`cat` it)
3. run_shell (e.g. `date`) → confirm real output
Only claim it works after seeing real tool output + the created file.

## Next-step menu to offer the user
streaming (typewriter output) · web-search tool · multi-agent/delegation · voice (TTS/STT). Telegram gateway is done once you have a bot token (see above).

Supporting files: `references/nine-router-agent-notes.md` — exact event-stream parser + working file layout · `references/telegram-gateway-raw-botapi.md` — raw Bot API gateway loop, message splitting, ops scripts · `references/bundling-skill-corpus.md` — importing a large third-party skill pack as built-in public skills (dedup, namespace, normalize, persona rework, gateway restart).


---

## Lampiran: `references/bundling-skill-corpus.md`

# Bundling a large external skill corpus into the agent

Session-verified recipe for importing a third-party skill pack (80+ `.md` files
from a zipped agent framework) as built-in public skills. Applied to Zeline
importing the Zeline Zenith pack.

## How the agent's skill seeding works (Zeline reference)
- `zeline/skills.py::seed_skills()` copies `zeline/skills/*.md` (package-bundled)
  → `~/.zeline/skills/public/`, **never overwriting** files the user customized.
  Returns count of newly copied files. Idempotent.
- Public skills are readable by `safe` gateway profiles; owner runbooks live in
  `~/.zeline/skills/private/` and are only reachable by the `full` CLI profile.
- `_parse(markdown)` reads **line-1 `# Title`** as the skill name shown in the
  registry and the **first `> ...` blockquote** as its description. `skills_block()`
  lists only name+description in the system prompt (token-cheap); full body is
  lazy-loaded via `load_skill(name)`.

## Import steps
1. **Extract** the pack somewhere out of the repo working tree (e.g. a scratch
   `.incoming-*/` dir).
2. **Filter dupes** in one pass:
   - Skip files whose first line contains `MERGED into` (reference-only stubs).
   - Keep only the canonical series (`sk*`), drop legacy mirror variants
     (`m*`, `x*`) that duplicate the same content under a different prefix.
3. **Normalize + namespace** each kept file, writing to the package bundled dir
   `zeline/skills/zeline-zenith-<stem>.md`:
   - New heading line: `# <human description> [Zeline Zenith]`
   - New blockquote: `> <human description> — modul ... (sumber: <stem>).`
   - Strip the original `# skX — ...` heading from the body to avoid a dup H1.
   - Derive `<human description>` by stripping the `skX — ` prefix and trailing
     `(ZELINE ZENITH)` / `(... consolidated)` tags from the original heading.
4. **Seed**: `python -c "from zeline import skills; print(skills.seed_skills())"`
   — should report the count you imported (e.g. 60).

## Persona rework (when the pack is persona-driven)
- Rewrite `config.py::SYSTEM_PROMPT_TEMPLATE` into a runtime persona:
  execution-first, **skill-aware** (explicit "call `load_skill` before acting"),
  language-mirroring, anti-fabrication ("never claim done before tool confirms"),
  owner-only safety defaults (no third-party creds, confirm irreversible ops,
  never log/print secrets).
- Add regression tests asserting the persona wording is in `SYSTEM_PROMPT` and a
  seeded corpus skill is loadable (`load_skill("zeline-zenith-sk0")` returns real
  content).

## Result shape from the applied session
- 76 files kept after filtering; 5 MERGED stubs skipped; 16 legacy `m*`/`x*`
  dupes dropped → **60 canonical `sk*` skills** bundled.
- Fresh `setup` then seeds **64 built-in skills** (4 original + 60 imported).
- All tests green (44), gateway restarted via `gateway stop` → `start`.



---

## Lampiran: `references/nine-router-agent-notes.md`

# 9Router agent-from-scratch — session notes

## Environment
- Termux, Python 3.13. `openai` v2.44 + `httpx`/`requests` present but `pydantic-core` missing → SDK import chain fails (`ModuleNotFoundError: annotated_types`, then `pydantic_core`). Compiling pydantic-core on Termux hangs (>180s). Decision: bypass SDK, use raw `requests`.
- 9Router local proxy at `http://localhost:20128/v1`. Creds live in `~/.zeline/config.yaml` under `custom_providers` where name contains "nine". Read them programmatically instead of copying keys.

## Reading 9Router creds from Zeline config
```python
import yaml
cfg = yaml.safe_load(open(f"{HOME}/.zeline/config.yaml"))
for p in cfg.get("custom_providers", []):
    if "nine" in p["name"].lower():
        base, key, model = p["base_url"], p["api_key"], p.get("model")
```
`/v1/models` returned 77 models: `nvidia/deepseek-ai/deepseek-v4-pro`, `nvidia/z-ai/glm-5.2`, `cx/gpt-5.6-*`, `nvidia/moonshotai/kimi-k2.6`, etc.

**Route health changed within days — re-probe before use.** Later session: `nvidia/deepseek-ai/deepseek-v4-pro` → 410 (gone), `nvidia/minimaxai/*` → 410, `cx/gpt-5.4-mini` → 429 (limit), `Gr/claude-opus` route → 403 (upstream balance exhausted: `剩余额度 $0.119, 需要预扣费额度 $0.20`). Healthy: `Vibe/ds/deepseek-v4-pro` and `Vibe/fg/deepseek-v4-flash` (200). Probe snippet:
```python
for model in ["Vibe/ds/deepseek-v4-pro", "Vibe/fg/deepseek-v4-flash", "cx/gpt-5.4-mini"]:
    r = requests.post(f"{BASE}/chat/completions",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": "hi"}], "stream": False}, timeout=45)
    print(model, r.status_code)
```
Also: Zeline `config.yaml` stores `model: Gr/claude-opus-4-8` under the Nine Router provider — reading that blindly overrides a healthy default with a dead route. Precedence: env `ZELINE_MODEL` > code DEFAULT_MODEL > config-file model (or ignore config model).

## The event-stream gotcha + parser
9Router responds with `content-type: text/event-stream` even for a non-streaming request, and the body is one full JSON object possibly followed by extra bytes. `requests` `.json()` raises `JSONDecodeError: Extra data: line 1 column 1048`. Working parser:
```python
import json
def _parse_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("data:"):
        text = text[5:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        obj, _ = json.JSONDecoder().raw_decode(text)  # first object only
        return obj
```
Then `data["choices"][0]["message"]`.

## Working file layout (~/zeline)
- config.py — DEFAULT_MODEL (`Vibe/ds/deepseek-v4-pro`), reads BASE_URL/API_KEY/MODEL (env override → Zeline config fallback, but env > DEFAULT_MODEL only), MAX_TOOL_ROUNDS=12, SYSTEM_PROMPT persona.
- tools.py — read_file (20k char cap), write_file (mkdir parents), run_shell (subprocess, 60s timeout, 12k output cap) + TOOL_SCHEMAS. Session 2 added memory/skill tools: `add_memory`, `remove_memory`, `list_memory`, `load_skill`, `save_skill`.
- memory.py — JSON persistence (`memory.json`): add/remove/list + `memory_block()` injected into system prompt at __init__.
- skills.py — markdown skills in `skills/` (`# Judul` line 1, `> deskripsi` line 2); list in system prompt, `load_skill(name)` lazy-loads full content.
- agent.py — Zeline class: `_parse_response`, `_call_llm` (requests.post, stream:false, 180s timeout), `_run_tool`, `send(user_input, on_tool)` running the loop.
- main.py — ANSI-colored CLI, banner, on_tool preview, exit words `keluar/exit/quit/q`.

## Verified working
Session 1: all 3 test paths passed after the parser fix — persona reply, write+read hello.txt (file confirmed on disk), run_shell `date`.
Session 2 (memory+skills): "ingat fakta X" → add_memory called, memory.json updated; NEW Zeline() → list_memory called, answered from persisted memory; load_skill read `check-server-health.md` content back correctly.



---

## Lampiran: `references/public-agent-framework-release.md`

# Releasing a Personal Agent as a Public Multi-Platform Framework

Use this when the request changes from “make my agent reachable on Telegram” to “let other people install it, configure their own model, and connect their own Telegram/WhatsApp accounts like Zeline.”

## 1. Reframe the architecture

A public framework is not one shared bot. Keep these boundaries explicit:

- **Package code**: installable Python package with a console entry point and package data.
- **Per-install state**: use `~/.<agent>/` (or an overridable home variable) for config, secrets, logs, sessions, memory, skills, and gateway state.
- **Per-platform identity**: namespace every conversation, e.g. `telegram:<chat_id>`, `whatsapp:<jid>`, `webhook:<caller_id>`.
- **Platform adapters**: adapters translate platform messages into one common `SessionStore.send(identity, text, tool_profile)` call; they must not instantiate ad-hoc agents or share one mutable history across users.

## 2. Configuration and migration

- Make fresh installs provider-agnostic: prompt for OpenAI-compatible `base_url`, API key, and model during setup.
- Never silently read API keys/tokens from another agent’s config (such as Zeline). Offer an explicit opt-in migration command such as `setup --from-zeline` instead.
- Environment secrets should override runtime config, but config-edit commands must not copy environment secrets into files.
- Write config/state atomically with private permissions where the OS supports them. Mask secrets in ordinary `config show`, `doctor`, and gateway status output; expose a secret only through an explicit owner command.

## 3. Public gateway security baseline

A public chat user must not receive the host owner’s shell or private files.

- Define tool profiles:
  - `safe`: memory plus public skill loading only; default for Telegram, WhatsApp, and webhooks.
  - `workspace`: safe plus files constrained to an approved workspace.
  - `full`: workspace plus shell; reserve for the local owner CLI.
- Resolve paths and reject traversal/symlink escapes outside the workspace.
- Store memory by hashed identity and cap facts, character count, and total identity files to limit abuse.
- Frame injected memory as **untrusted data**, explicitly instructing the model not to follow directives contained in it.
- Split skills into `public/` and `private/`; public gateways may load only public skills. Owner-created/runbook skills are private by default.

## 4. Adapter checklist

All adapters should provide a common shape: metadata for setup, config validation, and `start(sessions, cfg, stop_event)`.

### Telegram

- Verify the token with `getMe` before the polling loop.
- Persist update offsets only after an update is handled, preventing duplicate responses after restart.
- Support `/start`, `/new`, and status without bypassing session isolation.
- Empty allowlist may mean public access only if the tool profile remains `safe`.

### WhatsApp / Baileys

- Treat it as an unofficial multi-device bridge; document the policy/account risk.
- Ignore `fromMe` events to prevent reply loops.
- Use a random, per-runtime token for the local Node↔Python callback; bind bridge and callback ports to loopback.
- Bound inbound body sizes, disable groups by default, and apply optional JID allowlists.
- Actually install the declared Baileys version and run `node --check` on the generated bridge before claiming support.

### Generic webhook

- Bind to `127.0.0.1` by default.
- Require a strong token on every message endpoint and compare it with `hmac.compare_digest`.
- Enforce JSON/body limits and use caller-provided `chat_id` only as a namespaced session identity after authentication.
- Tell users to place HTTPS reverse proxy/tunnel infrastructure in front of it when exposing externally.

## 5. Gateway lifecycle

Provide both foreground and background operations:

```text
<agent> gateway run             # foreground; suited to systemd/tmux
<agent> gateway start|stop|status|log
```

Do not trust a PID alone. A stale PID can be reused by an unrelated process:

1. Spawn the child in a new session.
2. Store `pid` plus Linux/Android `/proc/<pid>/stat` start-time ticks.
3. Before `status`, `start` conflict detection, or `stop`, require the stored start-time to match the live process.
4. Treat state without a valid identity as stale and fail closed; never signal an unverified PID.

## 6. Release verification

Before publishing:

1. Run regression tests for agent protocol, public-safe tool profiles, memory/skill isolation, each adapter’s validation, and background lifecycle.
2. Build a wheel from the final source, not an old `dist/` artifact.
3. Inspect wheel contents: it must contain all new modules and must not contain `.env`, user memory, `~/.agent`, test state, or build leftovers.
4. Install the wheel into a clean target/home and run `--version`, `config path`, and gateway help.
5. Test the actual remote installer URL in a fresh HOME sandbox after the Git repository is public.
6. Add CI for multiple Python versions and wheel-content leak checks.
7. Publish a tagged release with the wheel and SHA-256 checksum, then download that release asset and verify the checksum before declaring it ready.

## Common failure modes

- A prototype adapter still uses `dict.setdefault()` after the core migrated to a `SessionStore`; adapter requests crash or bypass isolation.
- A source fix was made after a wheel was built; releasing the stale wheel makes the CLI fail at import time.
- A generic `skills/` directory exposes owner runbooks to public chat users.
- `gateway stop` trusts an old PID and can terminate an unrelated process.
- A local installer succeeds because the source checkout is on `PYTHONPATH`, but the remote installer/wheel was never tested in a clean HOME.



---

## Lampiran: `references/public-agent-framework.md`

# Turning a Personal Agent into a Public Installable Framework

Use this when the requirement changes from “make my bot work” to “other people must install it like Zeline and connect their own accounts.” The correct unit is now a framework release, not a single Telegram script.

## Architecture boundary

Keep source code, user state, and platform credentials separate:

```text
package source/
  pyproject.toml
  agent core + gateway adapters

~/.zeline/                 # or $ZELINE_HOME for tests/containers
  config.json              # provider + gateway configuration
  memory/                  # identity-namespaced JSON memory files
  skills/public/           # bundled/safe skills exposed to public bots
  skills/private/          # owner-only runbooks/procedures
  logs/gateway.log
  state/                   # persistent gateway offsets
  gateway.pid
```

- Package with `pyproject.toml` and an `zeline = "zeline.cli:main"` console entry point.
- `zeline setup` collects an OpenAI-compatible base URL, API key, model, and the user’s own gateways.
- Secrets use hidden prompts and config/status output masks them.
- Never silently copy secrets from Zeline or another app. Offer an explicit migration flag only.
- Use atomic config/state writes and private file modes where supported.

## Per-user isolation

One gateway process handles several people. Always namespace identities by platform:

```text
telegram:<chat_id>
whatsapp:<jid>
webhook:<external_chat_id>
cli:local
```

Every identity must receive separate mutable history, memory, and tool executor. Use a locked LRU session store so concurrent requests cannot corrupt message order and unbounded public chats cannot consume RAM indefinitely.

Memory is user-supplied untrusted data:

- frame it in a `<user_memory>` data block;
- explicitly say not to follow instructions contained in memory;
- bound fact length, facts per identity, total bytes per identity, and total identity files;
- do not use a shared global memory file for public users.

## Capability profiles

Public bot users must not receive the machine owner’s shell or filesystem access.

| Profile | Surface | Allowed capability |
|---|---|---|
| `safe` | public Telegram, WhatsApp, webhook | scoped user memory and public skill reading |
| `workspace` | trusted integrations | safe + read/write only below configured workspace |
| `full` | local owner CLI | workspace + shell and private skill authoring |

Implement file-path containment with resolved paths and reject traversal/symlink escapes. `run_shell` should exist only in `full`. If public users need a new action, add a narrow purpose-built tool rather than enabling arbitrary shell.

## Skills: public vs private

Bundled skills can be public, but owner-created runbooks may reveal paths, architecture, or credentials. Store and load them separately:

```text
skills/public/   # visible to safe profile
skills/private/  # visible only to full profile
```

When migrating a legacy flat skill directory, move legacy skills into `private/` by default. A safe session must neither list nor load private skill content.

## Gateway adapter contract

Use a registry with a small adapter interface:

```python
info()
validate_config(cfg) -> list[str]
start(session_store, cfg, stop_event)  # blocking loop
```

Validate enabled adapters before starting a background process. One adapter crash must not kill other adapter threads.

### Telegram

- Raw Bot API long polling works without a heavy SDK on Termux.
- Persist `getUpdates` offset after handling updates.
- Give each chat a separate session identity.
- Split messages below 4096 chars.
- Support `/start`, `/new`, and an optional allowlist.
- An empty allowlist may mean public only when `safe` profile remains mandatory.

### Webhook

- Bind `127.0.0.1` by default.
- Require a long random token via Bearer authorization or a dedicated header.
- Add a body-size limit and a `/health` endpoint.
- Owners expose it through their own HTTPS proxy/tunnel; do not bind openly by default.

### WhatsApp/Baileys

- Treat Baileys as a local Node bridge, not the official Meta Business API.
- Require Node/npm and create the bridge in the user state directory.
- Authenticate both Python→Node and Node→Python localhost calls with a fresh runtime bridge token.
- Ignore `fromMe` messages or the bot will reply to itself forever.
- Pairing is not verified until the owner scans the live QR code.
- Verify the exact npm package version name before pinning it; prerelease naming can differ (`rc14` vs `rc.14`).

## Service lifecycle

Keep `gateway run` foreground-friendly for tmux/systemd/containers. Add convenience `start|stop|status|log` that spawns:

```python
[sys.executable, "-m", "zeline.cli", "gateway", "run"]
```

without `shell=True`. Store PID state privately, clean stale PID files, and reject start when no configured gateway is enabled or any enabled gateway is invalid. Use private logs. PID state is an operational convenience; a production daemon manager should eventually own restart policy.

## Agent-loop correctness checks

- Append `assistant` tool calls before their corresponding `tool` results.
- Each tool result needs the matching `tool_call_id`.
- Snapshot messages before HTTP dispatch (`copy.deepcopy`) so mutable history cannot alter request payloads later.
- When trimming history, cut only at a `user` turn boundary; never begin the retained context with an orphan `assistant` or `tool` message.
- On Termux, direct `requests` POSTs to OpenAI-compatible APIs avoid `pydantic-core` build trouble. Some routers emit `text/event-stream` even for non-stream mode, so parse the first JSON object defensively.

## Release gate

Before publishing a public install:

1. Tests: tool ordering, history trim, provider error redaction, identity/memory isolation, memory limits, safe/full tool profiles, public/private skills, gateway validation, webhook authentication, and service lifecycle.
2. Build a wheel and inspect contents; reject `.env`, `memory.json`, user state, logs, and auth files.
3. Install the wheel into an isolated target outside the source tree and run CLI smoke commands.
4. Run an end-to-end test with a local fake OpenAI-compatible provider:
   `gateway start → /health → authenticated /message → reply → status → stop`.
5. For WhatsApp, separately install the bridge and run `node --check` plus export checks; live QR scan remains an owner step.
6. Add CI for supported Python versions plus a wheel-leak audit.

## Termux installer notes

- A generic `pip install --user` entry point often lands in `~/.local/bin`, which may not be on Termux PATH.
- If Termux `$PREFIX/bin` is writable, a small wrapper executing `python -m zeline.cli "$@"` gives a Zeline-like global command.
- Otherwise print the exact PATH export needed; do not assume a system-wide writable binary directory.



---

## Lampiran: `references/telegram-document-size-policy.md`

# Telegram document intake: layered limits

Use separate limits for separate risks:

| Layer | Purpose | Example |
|---|---|---|
| Raw download | Prevent excessive network/disk use | 20 MB |
| ZIP member count | Prevent archive fan-out | 512 files |
| Per-entry eligibility | Block traversal/binary/oversize content | normalized relative text files only |
| Extracted text | Protect context budget | bounded total, e.g. 256 KB |
| Final agent input | Match `Agent.send()` validation | 16,000 chars, inclusive of all wrapper text |

## Critical ordering

1. Download and validate raw file size.
2. Validate ZIP members before extraction (no absolute paths or `..`; allowed text types only).
3. Assemble extracted text within its total byte budget.
4. Construct the *entire* agent prompt: filename, optional caption, header, code fences, and file text.
5. Truncate that final string to the agent input limit before calling the session/agent.
6. Test the boundary with a large document and a long filename/caption.

Truncating only `file_text` is insufficient: prompt wrapper overhead can cause an otherwise 16,000-character payload to be rejected by the agent as too long.

## Regression tests

- mocked document download at 729 KB succeeds;
- ZIP with 65 safe text members succeeds when the configured cap is 512;
- a ZIP containing 20,000 text characters produces a final prompt `<= 16,000` characters;
- a traversal entry such as `../escape.txt` is excluded.



---

## Lampiran: `references/telegram-gateway-raw-botapi.md`

# Telegram Gateway — raw Bot API long-polling (zero extra deps)

Pattern verified building "Zeline" (2026-08-08): making a from-scratch agent
reachable publicly on Telegram, exactly like Zeline, WITHOUT `python-telegram-bot`
(which pulls httpx + pydantic → pydantic-core, the same Termux compile trap as
the `openai` SDK). Plain `requests` long-polling is enough.

## Architecture

- `config.py` — add `.env` loader (project-local, `os.environ.setdefault` so real
  env vars win) + `TELEGRAM_TOKEN = os.environ.get("ZELINE_TG_TOKEN", "")` and
  optional allowlist `ZELINE_TG_ALLOWED` (comma-separated chat IDs; empty = public).
- `telegram_bot.py` — gateway: poll → per-chat agent session → reply.
- `start-tg.sh` — start/stop/status/log wrapper with PIDFILE + nohup logfile.
- `install.sh` — writes global launchers `zeline` (CLI) and `zeline-tg` (gateway
  manager) into `$PREFIX/bin`, plus `--pack`/`--unpack` tar backup for migrating
  the whole agent folder to another device.

## Core loop (the essential shape)

```python
API = f"https://api.telegram.org/bot{TOKEN}"

def tg(method, **params):                 # one Bot API call, tolerant
    r = requests.post(f"{API}/{method}", json=params, timeout=65)
    return r.json() if r.json().get("ok") else None

class Gateway:
    def __init__(self):
        self.sessions = {}                # chat_id -> Zeline instance
        self.offset = 0

    def run(self):
        while True:
            try:
                r = requests.get(f"{API}/getUpdates",
                    params={"offset": self.offset, "timeout": 30}, timeout=65).json()
                for upd in r.get("result", []):
                    self.handle(upd)
                    self.offset = upd["update_id"] + 1
            except requests.exceptions.Timeout:
                continue                  # long-poll timeout is NORMAL — never an error
            except Exception as e:
                print(f"error: {e}", flush=True); time.sleep(3)
```

- Long-poll `timeout` param (30s) MUST be less than the HTTP request `timeout` (65s),
  else every poll cycle raises Timeout and reconnects.
- Process each update BEFORE advancing `offset`; offset = `update_id + 1`.

## Per-chat sessions

One `Zeline` per chat_id keeps histories isolated and persistent in memory:

```python
def session(self, chat_id):
    if chat_id not in self.sessions:
        self.sessions[chat_id] = Zeline()
    return self.sessions[chat_id]
```

`/new` command → `self.sessions.pop(chat_id, None)` (reset history).

## Handling flow

1. Access gate: if `self.allowed` non-empty and chat_id not in it → "Akses ditolak." return.
2. `/start` `/help` → static intro; `/new` → reset.
3. `sendChatAction(chat_id, "typing")` BEFORE calling `agent.send()` (fast feedback
   while the LLM thinks; also re-send per tool call via `on_tool`).
4. Reply → `sendMessage`. Long replies MUST be split — Telegram hard-caps 4096
   chars per message (split on line boundaries, not mid-line).

## Pitfalls

- **Message cap**: `split_message(text, limit=4000)` keeps breathing room under the
  4096 hard cap. Split on `\n` boundaries, accumulate buffer.
- **Gateway dies with Termux**: polling needs the process alive. `nohup python3
  telegram_bot.py >> log 2>&1 &` + PIDFILE for management. For 24/7 public use,
  migrate the folder to a VPS — `install.sh --pack` / `--unpack` tar transfer is
  the whole story (folder is self-contained: config auto-reads keys from Zeline
  config or its own `.env`).
- **Token hygiene**: never paste the bot token into code. `~/zeline/.env` →
  `ZELINE_TG_TOKEN=...`; gitignore it if the project goes public.
- **Allowlist default = open**: forgetting `ZELINE_TG_ALLOWED` means anyone who
  finds the bot handle can use it (and burn your 9Router credit). For a personal
  bot, set `ZELINE_TG_ALLOWED=<user_id>`; Zeline user id was `<OWNER_CHAT_ID>`.
