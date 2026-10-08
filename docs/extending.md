# Extending Zeline

Four ways to give the agent new capabilities without forking this repository.
Pick by what you are trying to do:

| You want to | Use |
| --- | --- |
| add a capability written in Python | [custom tools](#custom-tools) |
| audit, rewrite, or block existing tool calls | [plugin hooks](#plugin-hooks) |
| call an HTTP API you already have a spec for | [OpenAPI tools](#openapi-tools) |
| reuse a tool server someone else wrote | [MCP servers](#mcp-servers) |

All four load **only on the `workspace` and `full` profiles**. They run arbitrary
local Python or reach local processes, so a public messaging gateway on the
default `safe` profile never sees them. Check where you are:

```bash
zeline tools profile          # with no argument: prints the current profile
zeline tools list             # every native tool, its state, and the profile
```

---

## Custom tools

A Python file in `~/.zeline/tools/` turns every public function into a tool named
`custom_<function>`.

```bash
zeline tools custom-init my_tools.py   # writes a working starter file
zeline tools custom-path               # print the directory
zeline tools custom                    # list what actually loaded
```

```python
# ~/.zeline/tools/my_tools.py

def jira_issue(key: str, verbose: bool = False) -> str:
    """Fetch a Jira issue by key.

    key: issue key such as PROJ-123
    verbose: include the full description
    """
    ...
    return summary
```

That becomes `custom_jira_issue`. The JSON schema comes from the signature:
annotations give types, defaults decide what is optional, and the docstring
supplies both the tool description (first line) and the per-argument
descriptions (the `name: text` lines). There is no manifest to keep in sync,
because a manifest that can drift from the signature is a bug waiting to happen.

Annotations accepted: `str`, `int`, `float`, `bool`, `dict`, `list`. Anything else
is rejected with a clear message rather than guessed at — a wrong schema makes the
model send arguments your function cannot accept.

Four behaviours worth knowing:

- **One bad file cannot take down the agent.** Import errors, syntax errors, and
  unsupported signatures are collected per file and reported; every other file
  still loads.
- **Names are prefixed and cannot shadow a native tool.** `custom_` makes the
  origin obvious in transcripts, and guarantees a local file never silently
  replaces `write_file`.
- **Return a string.** The provider protocol requires one, so returns are coerced
  and exceptions become `ERROR ...` text instead of escaping into the turn.
- **Export a subset** with `ZELINE_TOOLS = ["jira_issue"]` at module level when a
  file also holds helpers you do not want the model to call.

## Plugin hooks

Custom tools *add* capabilities. Hooks *govern* the ones that already exist — a
different job, so a different mechanism. A file in `~/.zeline/plugins/`:

```bash
zeline plugins init 10-audit.py   # starter file
zeline plugins list               # loaded hooks, in run order
zeline plugins path
```

```python
# ~/.zeline/plugins/10-policy.py
from zeline.plugins import deny


def on_tool_before(name, args):
    if name == "run_shell" and "rm -rf /" in str(args.get("command", "")):
        return deny("blocked by local policy")
    return None


def on_tool_after(name, args, result):
    token = os.environ.get("COMPANY_TOKEN")
    return result.replace(token, "[redacted]") if token else None
```

This is the only place you get an audit trail of every tool call, argument
rewriting (inject a default, clamp a limit), and redaction of tool output
*before* it enters the model's context.

A hook sits on the path of every tool call, so a careless one is more damaging
than a careless custom tool. Hence:

- **A broken hook never breaks the tool call.** Exceptions are captured, the hook
  is skipped, the call proceeds.
- **Blocking is explicit.** Only a `deny(...)` sentinel stops a call. `None`, a
  wrong type, or no return at all means "no opinion", so a hook cannot block by
  accident — a silent, baffling failure mode.
- **Rewrites must be type-correct or they are ignored.** `on_tool_before` returns
  a dict to change arguments; `on_tool_after` returns a string to change output.
- **Order is deterministic:** sorted filename order, so `10-audit.py` runs before
  `20-redact.py` and you control the pipeline.

## OpenAPI tools

If the API you want already has an OpenAPI 3 document, you do not need to write
wrappers for it:

```bash
zeline tools openapi-add ./petstore.yaml       # copies it into ~/.zeline/openapi/
zeline tools openapi                           # list the tools it produced
zeline tools openapi-path
```

Each operation becomes one `api_<file>_<operationId>` tool with parameters
derived from the document. `.yaml`, `.yml`, and `.json` are supported, along with
local `#/...` references.

**Credentials never appear in a tool schema.** They are read from `~/.zeline/.env`
under a name derived from the file and the security scheme:

```
ZELINE_OPENAPI_<FILE>_<SCHEME>
```

So `petstore.yaml` with a security scheme named `apiKey` reads
`ZELINE_OPENAPI_PETSTORE_APIKEY`. If a required credential is missing, the tool
says which variable to set instead of sending an unauthenticated request. When a
document lists several security alternatives, the first one whose credentials are
actually present is used.

A broken document is reported without hiding the tools from every other file.

## MCP servers

Model Context Protocol servers expose their tools automatically — stdio for a
local command, streamable HTTP for a URL:

```bash
zeline mcp add filesystem --command "npx -y @modelcontextprotocol/server-filesystem ~/"
zeline mcp add openconnector --url http://localhost:3000/mcp
zeline mcp test filesystem      # connect and list the tools it offers
zeline mcp list
zeline mcp remove filesystem
```

`zeline mcp test` before relying on a server: it proves the transport works and
shows exactly which tools arrive, rather than leaving you to find out mid-turn.
A stdio server is a local process launched by Zeline, so the same
`workspace`/`full` restriction applies.

### Trusting an MCP server (risk cap)

Every MCP tool starts at the strictest risk class — **Destructive**, always
asking for approval. Zeline cannot audit what an external tool really does
(the name and description are the server's own claims), so an unclassified
tool must never run silently. If you *know* a server is harmless (say, a
docs-search server that only reads), you can lower that default with an
explicit trust statement in your config file (`~/.zeline/config.json`):

```json
{
  "mcp": {
    "servers": {
      "docs": {
        "transport": "stdio",
        "command": "npx -y @modelcontextprotocol/server-docs",
        "trust": {"risk_cap": "read"}
      }
    }
  }
}
```

`risk_cap` is one of `read`, `write`, `network`, `install`, `destructive`
and caps how the server's tools are treated by the approval gate. The
convenience flag sets it at add time:

```bash
zeline mcp add docs --command 'npx -y @modelcontextprotocol/server-docs' --trust-risk-cap read
```

Three rules keep this honest:

1. **Config file only, never from chat.** The model cannot talk you into
   trusting a server, and neither can a tool description.
2. **Fail closed.** A missing, mistyped, or unknown cap value keeps the
   Destructive default — a typo can never silently widen permissions.
3. **Trust is per server, not per tool.** If the server updates and one of
   its tools changes behaviour (read → write), the cap will not catch it.
   Keep caps as narrow as the server's actual job, and re-run
   `zeline mcp test` after updates.

---

## Choosing between them

Reach for a **custom tool** when the logic is yours and small — a lookup, a
calculation, a call to an internal service. Reach for **OpenAPI** when a spec
already exists; hand-writing wrappers for a documented API only creates drift.
Reach for **MCP** when someone has already built and maintained the integration.
Reach for a **hook** when the capability exists and what you need is control over
it.

## Model routing

Off by default. When enabled, each user turn is classified into a category and
sent to the model configured for that category — so cheap chit-chat can go to
a small model while code, research, and long-context turns stay on the strong
default. Classification is a deterministic heuristic (no LLM call, no extra
cost); only strong signals trigger a category, and anything doubtful stays on
the default model.

| Category | Typical trigger | Suggested model class |
| --- | --- | --- |
| `quick` | short greeting or simple factual question | small / fast |
| `code` | code fence, code keywords, file extensions, code tools in history | strong code model |
| `research` | search / news / price-comparison keywords | web-enabled model |
| `reasoning` | long prompt *and* analysis keywords | strong reasoning model |
| `long_context` | estimated context (text + history) above 32k chars | large-context model |
| *(no match)* | anything else | default model (unchanged) |

Precedence is `long_context` > `code` > `research` > `reasoning` > `quick`:
a specific signal always beats `quick`, so "halo, tolong debug kode ini"
routes as `code`. A category with no configured route falls back to the
default model — routing fails closed towards quality, never towards the
cheapest model. The heuristics are deliberately conservative: they do not
aggressively push heavy work onto cheap models.

Config file (`~/.zeline/config.json`):

```json
{
  "routing": {
    "enabled": true,
    "routes": {
      "quick": "provider/flash-model",
      "code": "provider/code-model"
    }
  }
}
```

Environment variables (always win over the file):

```bash
ZELINE_ROUTING_ENABLED=1        # 1/true/yes/on to enable; setting it to 0/false disables even if the file says enabled
ZELINE_ROUTE_QUICK=provider/flash-model
ZELINE_ROUTE_CODE=provider/code-model
ZELINE_ROUTE_REASONING=provider/thinking-model
ZELINE_ROUTE_RESEARCH=provider/search-model
ZELINE_ROUTE_LONG_CONTEXT=provider/large-context-model
```

Setting a `ZELINE_ROUTE_*` variable to an empty string removes that route
(useful to unset a route defined in the file). Unknown category names in the
file's `routes` are ignored rather than crashing.

When a turn is routed, the chat shows a one-line note such as
`🔀 Routing ke provider/flash-model (kategori: quick)`; unrouted turns show
nothing. Usage statistics are recorded under the model that actually served
the turn, so cost tracking stays accurate. See `zeline/routing.py` for the
full heuristic documentation and thresholds.

## Semantic memory retrieval

`MemoryStore.retrieve()` scores candidates with a hybrid of semantic
similarity and keyword match when a local embedding model is available:

    score = (SEM_W × sem_norm + KW_W × kw_norm) × recency × confidence

with `sem_norm = max(0, cosine(query_vec, fact_vec))`. Semantic similarity
catches paraphrases that share no keywords ("kapan terakhir gue ke dokter
gigi?" finds "appointment dokter gigi 3 Okt jam 10"); the 40% keyword weight
keeps exact terms (names, numbers, dates) winning when they match. Weights
are configurable and not normalized — `SEMANTIC=1.0, KEYWORD=0.0` is pure
semantic.

The embedding provider (`zeline/embeddings.py`) is local and light: ONNX via
`fastembed`, no torch, no GPU. Default model
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (~241 MB,
downloaded once on first use) was chosen after measuring clean separation
for Indonesian: unrelated pairs score 0.02–0.28, true paraphrases 0.54–0.81.
An English-centric model was rejected after measurement showed its noise
floor (~0.5–0.6) overlapping genuine paraphrases. Embeddings are computed
lazily, cached per fact in `<memory-dir>/embeddings/<hash>.<model>.json`
(0600), and never block the keyword path: any embedding failure degrades to
plain keyword retrieval, and `retrieve()` never raises.

**Install** (optional dependency):

```bash
pip install fastembed   # pulls onnxruntime; no torch needed
```

**Environment variables**

| Variable | Default | Meaning |
|---|---|---|
| `ZELINE_EMBEDDINGS_ENABLED` | `1` | `0`/`false`/`no`/`off` disables entirely → keyword-only retrieval, behavior identical to before this feature existed |
| `ZELINE_EMBEDDING_MODEL` | paraphrase-multilingual-MiniLM-L12-v2 | fastembed model name override |
| `ZELINE_EMBEDDING_CACHE_DIR` | *(unset → fastembed default)* | cache directory override passed to fastembed; when unset, model downloads use the HuggingFace hub cache (`~/.cache/huggingface`, or `HF_HUB_CACHE` if set) and fastembed's working dir defaults to `/tmp/fastembed_cache` (overridable via `FASTEMBED_CACHE_PATH`) |
| `ZELINE_EMBEDDING_LOAD_TIMEOUT` | `180` | model load timeout in seconds (daemon thread); a hung download fails safe to keyword-only instead of blocking forever. Increase on very slow networks; decrease for faster failover in latency-sensitive setups |
| `ZELINE_HYBRID_SEMANTIC_WEIGHT` | `0.6` | semantic component weight (clamped ≥ 0) |
| `ZELINE_HYBRID_KEYWORD_WEIGHT` | `0.4` | keyword component weight (clamped ≥ 0) |

**Trade-off**: one embedding per query plus one per fact (cached after the
first retrieval). Cost is local CPU time, not tokens. **Security invariant**:
facts synced from external sources (`*-sync`) are always rendered in the
`<untrusted_external_data>` block no matter how high their semantic score —
scoring never promotes untrusted data to trusted.

## Memory rollup

Old or low-confidence facts can be rolled up into extractive summaries with
full provenance (`zeline/memory_rollup.py`). A rollup groups facts by
(kind, source), writes one deterministic zero-token summary per group, and
records which source facts it came from — the originals are never modified
or deleted silently. Trigger is always explicit; nothing runs on a schedule
unless the operator sets it up:

```python
from zeline import memory_rollup
memory_rollup.rollup("telegram:123", dry_run=True)  # preview first
memory_rollup.rollup("telegram:123")               # facts >90 days old or confidence <0.5
memory_rollup.list_rollups("telegram:123")         # audit provenance
memory_rollup.unroll("telegram:123", "<rollup_id>") # undo (summary goes to trash)
```

Rollup is idempotent (a second run reports nothing to do) and summaries
derived from synced facts keep an untrusted source (`rollup-sync`), so they
render in the untrusted block — rollup never "launders" external data into
trusted memory. An LLM-based summarizer exists (`rollup_llm`) but is fully
opt-in (`use_llm=True` plus an explicit summarizer callable): it costs
tokens per group, is non-deterministic, and sending synced facts to a model
opens a prompt-injection surface the extractive path does not have. To
disable rollup entirely, simply never call it — the module registers no
cron jobs, hooks, or background threads.

## Self-improving skills

Zeline learns which skills actually help, from usage — not from vibes. Three
cooperating pieces:

**1. Usage telemetry** (`zeline/skill_telemetry.py`). Every `load_skill` call is
counted, and every background worker's verified outcome (supervisor
verification: pass/fail) is attributed to the skills it used, under the
owner's identity. Storage: `~/.zeline/skill-telemetry/<id>.json` (0600).
Privacy rule: *metadata only* — skill name, counters, durations, and a
normalized error *category*. Runs of 4+ digits are masked, so phone numbers,
PINs, and similar can never be reconstructed from disk; callers must pass a
fixed category (e.g. `verify_failed`), never free text. Conversation content
and raw error text are never stored. Telemetry is fail-safe: if recording
breaks, skill loading keeps working.

**2. Periodic review** (`zeline/skill_review.py`, tool `review_skills`). Scores
each private skill from telemetry and proposes one of:
- `promote` — consistently helpful (≥5 loads, ≥80% success): listed first.
- `demote` — mostly failing: listed last, still usable.
- `archive` — never used and stale, or failing 5× in a row: moved to
  `.archive/` (restorable, never deleted).
- `report_overlap` — possible duplicates are *reported only*, never auto-merged.

Archiving touches the *shared* skill directory, so it needs *global*
evidence: a skill is only archived for disuse when **no** identity uses it,
and only archived for failures when it is failing everywhere — not just in
one identity's context (that case gets a per-identity demote instead).

`review_skills` is a dry-run. `apply_skill_review` applies the plan — it is
`INSTALL`-class, so the operator always sees the exact plan in the approval
question first, and the handler executes **exactly that shown plan**: it never
recomputes silently between approval and execution, and it refuses outright
when there is no fresh approved plan (fail-closed, including for unattended
cron runs). Every change is written to the curator ledger and can be
undone with `rollback_skill_change` (itself `INSTALL`-class — rolling back a
proposal rewrites skill content, so it also needs operator approval).

**3. Fix proposals** (`zeline/skill_proposals.py`, tools `propose_skill_fix` /
`apply_skill_proposal`). Content fixes are *proposals*, never silent rewrites:
`propose_skill_fix` only records the intended diff (the old text must match
exactly once, or the proposal is rejected). `apply_skill_proposal` is
`INSTALL`-class — the approval question shows the full diff (quoted, so skill
file content cannot mimic the approval UI) — and re-verifies
the file before patching; if the file changed since the proposal, the patch
is refused. A session-wide "allow" covers only the exact proposal id that was
approved, never a different proposal later. Files are checkpointed before patching, and every applied
proposal can be rolled back.

Hard boundaries: built-in public skills are never auto-archived; no code path
rewrites skill content without operator approval; the review never deletes.

## Voice (text-to-speech & voice notes)

Voice is a core gateway feature, not a skill: `zeline/voice.py` (TTS),
`zeline/voice_prefs.py` (per-chat preferences), wired into the Telegram
gateway. The old `voice-reply` skill is now documentation plus a thin CLI
wrapper over `zeline.voice` — there is no duplicated logic.

**Inbound (automatic).** A voice note is downloaded, then transcribed
*directly in the gateway* via the provider's `/audio/transcriptions`
endpoint (`zeline/transcribe.py`) — the transcript becomes the user's message
to `sessions.send`, tagged as coming from a voice note. If transcription
fails or no transcription model is configured, the gateway falls back to the
old behavior (the agent is asked to transcribe via `analyze_media`); the
voice path never becomes a dead end.

**Outbound (opt-in per chat, default is text).** `/voice` in Telegram:

- `/voice mirror` — voice note in → voice note out, text in → text out
- `/voice always` — every reply attempted as a voice note (when it fits)
- `/voice text` — back to plain text (default)
- `/voice style <name>` — fixed voice preset for this chat
- `/voice voices` — list presets (`emma-anime` default, `ava-anime`,
  `gadis-anime`, `gadis`, `ana`, `nanami`)

Preferences live in `~/.zeline/voice-prefs/` (one JSON per identity, 0600,
same storage pattern as goals/tasks).

Eligibility per reply: non-empty, ≤ 600 characters (`MAX_TTS_CHARS` in
`zeline/voice.py`), no fenced code blocks (code read aloud sounds broken).
Longer replies are sent as text — never silently truncated into a 5-minute
voice note. If synthesis fails, the text reply is still sent with a short
note saying the voice reply failed; the chat is never left hanging.

**Requirements:** `edge-tts` (`pip install edge-tts`) and `ffmpeg` for
outbound TTS (edge-tts needs internet — it fetches neural voices from
Microsoft's servers); a configured provider plus transcription model
(`ZELINE_AUDIO_MODEL`, e.g. `whisper-1`) for inbound transcription. Missing
tools/models produce plain-language errors and graceful fallbacks, never
crashes.

**Programmatic use:** `zeline.voice.synthesize(text, style=..., out_dir=...)`
returns the `.ogg` (Opus) path, or `.mp3` if Opus conversion fails; every
failure raises `VoiceError` with a usable message.

## WebChat UI

A minimal web chat dashboard (`zeline/gateways/webchat.py`) — a stdlib
`ThreadingHTTPServer` serving a single inline HTML page (no external
dependencies/CDN, works fully offline) that talks to the same
`sessions.send()` as every other gateway.

```bash
zeline gateway setup webchat   # interactive: bind host, port, token (shown once)
zeline gateway enable webchat  # non-interactive: localhost, port 8787, safe profile
zeline gateway run              # starts it alongside other enabled gateways
```

Endpoints:

- `GET /health` — no-auth status, no secrets (same as the webhook gateway)
- `GET /` — authenticated: full chat page; unauthenticated: token login
  page only (no data, no chat panel); wrong token: 401
- `POST /api/message` — auth required, JSON `{"chat_id": "...", "text": "..."}`,
  session identity `webchat:<chat_id>`, replies `{"reply": ...}`
- `GET /api/status` — auth required, JSON with the active model, a compact
  list of running/queued workers, and today's token usage; never leaks the
  token or API keys

**Security model.** One shared bearer token (`Authorization: Bearer` or
`X-Zeline-Token`, constant-time compare) and a caller-chosen `chat_id` cannot
prove owner identity, so WebChat is **safe-only**: `validate_config` and the
gateway tool-policy validator both reject any `tool_profile` other than
`safe`. Default bind is `127.0.0.1`; to expose it, put an HTTPS reverse
proxy in front (proxy to `127.0.0.1:8787`) and use a long random token.
The token lives in the browser's `sessionStorage` (no cookies, no CSRF
surface), and all user/reply text is rendered via `textContent` — never
`innerHTML` — so a reply containing `<script>` cannot become XSS.

## Changing Zeline itself

Adding a *native* tool — one that ships in the package and appears on the `safe`
profile — is a change to this repository, and it is not a one-file change: the
`ToolDef` in `zeline/tools.py`, its handler, a title in the Telegram and app
gateway progress renderers, and an entry in the compaction artifact map all have
to agree. See [CONTRIBUTING.md](../CONTRIBUTING.md).
