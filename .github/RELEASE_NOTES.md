## Zeline Release

Zeline is the open-source agentic AI framework by Zerolinear.

### Highlights

- **Tool schemas are no longer re-sent in full on every request** — the schema
  payload was attached to *every* tool round of *every* turn: 17,089 characters
  (~4.3k tokens) on a stock `full` profile, and 25,898 (~6.5k) once MCP servers
  are connected. The new `tool_search` catalogue sends a small core plus a
  one-line summary of every other tool, cutting that to 6,793 characters (60%
  less) and 7,715 (69% less) respectively. Nothing becomes unreachable — every
  tool name stays visible, a catalogued tool can be called directly without a
  lookup round trip, and once a schema is revealed it stays revealed for the life
  of the session. **On by default** in this release, gated by two measured
  floors — a tool count *and* the characters actually saved — so the public
  `safe` profile, where hiding would save less than the round trip costs, keeps
  sending everything. `zeline toolsearch` prints the numbers for your own tool
  set; `zeline toolsearch off` restores the old behaviour byte-for-byte.
- **A real browser, driven over the Chrome DevTools Protocol** — `web_fetch` only
  ever saw raw HTML, which is useless for JavaScript-rendered pages and anything
  behind a login. The new `browser` tool opens, clicks, types, reads text, and
  screenshots a real Chromium/Chrome, discovered automatically on PATH or pinned
  with `tools.browser_binary`. Implemented as a stdlib WebSocket CDP client — no
  Playwright, no Puppeteer, no new dependency.
- **Scheduled work runs inside the gateway, not in system cron** — a system crontab
  entry would need its own copy of the config, provider key, and workspace; the
  gateway process already holds all three. `zeline cron` adds, lists, pauses, and
  removes jobs (interval or cron expression), the scheduler ticks inside the
  running gateway, and results are delivered to the chat that owns the job.
- **Sub-agents run in parallel, with roles and an optional verifier** —
  `delegate_task` accepts several tasks at once, bounded by
  `max_parallel_subagents`, with depth limits that stop recursive fan-out.
- **`code_intel`: ask a real language server about the code** — diagnostics,
  definitions, references, hover, and document symbols over LSP. Servers belong to
  the operator and are only ever discovered on PATH, never downloaded; when no
  language server is installed it degrades to the project's linter instead of
  failing.
- **Operator extension points** — `zeline plugins` loads Python hooks that can
  audit, rewrite, or block any tool call before it runs (one governance point for
  native, MCP, and custom tools alike), and `zeline tools custom` loads plain
  Python files as `custom_*` tools that can never shadow a built-in.
- **Undo for anything the agent wrote** — every write and edit is snapshotted
  first, and `zeline undo` lists and restores them.
- **`ask_user`: human-in-the-loop questions mid-turn** — the agent can ask a real
  question and wait, instead of guessing and being wrong expensively.
- **Token accounting** — `zeline stats` reports per-model usage and cost from
  recorded requests rather than estimates.
- **Sessions are portable** — `zeline session export | import | fork` moves a
  conversation between machines or branches one to try a different approach.
- **Project rules are read automatically** — `ZELINE.md` or `AGENTS.md` in the
  workspace is loaded into the system prompt once per session, so it stays stable
  for prompt caching; `zeline init` scaffolds one.
- **Formatter runs after write and edit** — the project's own formatter, detected
  from its config, so generated code matches the codebase instead of the model's
  habits.
- **`gateway restart` and `gateway update` drain in-flight work** — a turn already
  in progress finishes instead of being cut mid-tool-call.
- **A listening HTTP adapter now reports as connected** — `gateway status` no
  longer claims a healthy gateway is down.
- Versioned, checksum-verified installers for Termux, Linux, macOS, iSH, and
  Windows PowerShell.
- Immutable release artifacts with build provenance.

### Upgrade note

`tools.tool_search` defaults to `true` from this release. Existing installs that
already have `tool_search: false` written into `config.json` keep their setting —
run `zeline toolsearch on` to adopt the saving, or `zeline toolsearch` to see the
measured numbers for your own tool set before deciding. It engages only where it
pays: profiles that would save less than 8,000 characters of schema keep sending
everything, whatever the flag says.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.2.6/docs/installation.md) for checksum-verified commands on every supported platform.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
