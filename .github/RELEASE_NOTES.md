## Zeline Release

Zeline is the open-source agentic AI framework by Zerolinear.

### Installing

One line on every POSIX platform — Termux, Linux, macOS, iSH:

```bash
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.9/install.sh && bash install.sh
```

The installer downloads the versioned wheel and verifies it against
`SHA256SUMS` itself, so there is nothing to check by hand.

Every release carries the whole bundled surface — this one ships **255 skills**
(109 of them the Zenith corpus) and **29 tools** — and the release workflow now
diffs the built wheel against the source tree and fails rather than publishing an
install that is missing any of it.

### Highlights

- **The live Telegram feed names the work, not the function.** Eight of the
  twenty-nine registered tools had no label and fell through to a catch-all that
  printed the raw tool name: `🔧 recall history: lanjut`, `🔧 browser: open`,
  `🔧 code intel: diagnostics`, `🔧 download file: <full URL>`. That is a debug
  dump. `runtime_info` now reads as a runtime identity check, `browser` and
  `code_intel` get a verb per action plus the page host or the file and line, and
  `download_file` names the destination. URLs are reduced to their host, because a
  raw URL can carry a query token or `user:pass@host` credentials. MCP tools were
  also hitting the fallback, whose underscore substitution produced
  `🔧 mcp  mem0  add memory`; they render as `🧩 add memory via mem0`.
- **`/stop` lands in under 0.1 s**, down from up to 180 s. Cancellation used to be
  a flag the agent could only notice between provider calls, so a stop issued
  during a blocking request waited for that request to return. It now closes the
  in-flight response and its socket, and checks cancellation before a request
  goes out and inside the model-failover loop.
- **A long task is no longer cut off at ten edit cycles.** `max_tool_rounds`
  defaults to 150 (was 20) and `max_turn_seconds` to 4500 (was 1800). Twenty
  rounds is about ten read-then-edit cycles, which a multi-file change exhausts
  before it is done; the clock stays above `rounds × 30 s` so the round budget is
  the real limit and the clock is a backstop for a genuinely stuck turn.
- **The activity feed is one compact line per command.** A stack of tall code
  cards with `COPY CODE` buttons is replaced by a single one-line card: no nested
  language label, content flattened and capped, and the cut placed at the last
  word boundary so a command reads as abbreviated rather than severed.
- **Narration speaks for findings, not for every step.** The old prompt mandated
  an opener plus a sentence per tool batch, which produced a running commentary of
  trivial mechanical actions. Routine reads and greps now happen silently.
- **`lanjut` resumes the session you are actually in.** Continuation resolved to
  the newest turns sharing the newest *title*, so a fresh session whose title
  matched an older bucket recalled the previous day's work as if it were still in
  progress.
- **Bundled skills work for whoever installed Zeline.** Several shipped with
  hardcoded personal paths, accounts, and site names, so for every other user
  they failed on the first command or pointed somewhere irrelevant.
- **Model discovery no longer assumes one response shape.** `/models`,
  `/v1/models`, and `/api/tags` are each tried, and `data`, `models`,
  `data_list`, and bare list payloads all parse.
- **A release no longer reports failure because PyPI has no Trusted Publisher.**
  Publishing goes through Trusted Publishing (OIDC), so no API token is stored in
  this repository — but a publisher lives in a PyPI account the workflow cannot
  see, and the upload used to fail with `invalid-publisher` on every release and
  paint the `pypi` deployment red for a release whose assets were built,
  verified, attested, and published. The upload is now gated on a probe that asks
  PyPI whether it accepts this workflow's identity, so the job is *skipped* with
  setup instructions when no publisher exists and runs normally once one does. A
  skipped job says "not configured"; a failed job says "broken". `pip`/`uv`
  installs become available with the first release whose upload completes; until
  then use the installer above.
- **`zeline update` restarts the gateways that were actually running.** It read
  the selection after the stop had already deleted the state file, so an operator
  who started only Telegram got every enabled gateway back.

### For contributors

`CONTRIBUTING.md` now documents the fork-and-pull-request path. Its opening
instruction used to be `git push -u origin <branch>` against this repository,
which fails with 403 for anyone without push access, and the word "fork" appeared
nowhere in it or in any README. `ZELINE.md` is this repository's project
conventions — layout, real build and test commands, commit style, what not to
commit — rather than a persona document, with every claim checked against the tree.

### Removed

The mobile-app HTTP surface has moved out of this repository. The framework ships
the agent runtime and the messaging gateways that adapt it to a chat platform; the
app's REST/SSE server, session store, JWT auth, and client event schema are a
separate product with a separate release cycle, and keeping them here made a
framework release gate on an app change. Nothing the CLI or the messaging gateways
use is affected; a `gateways.zeline_app` entry left in an existing `config.json`
is inert.

### Security

Custom tools, plugin hooks, and MCP stdio servers are arbitrary local Python in
the agent's process and load only on the `workspace`/`full` profiles — a public
gateway on `safe` never reaches them. Provider API keys never appear in any
response, and the progress feed prints a URL's host only, never the full URL or a
proxy's credentials.

### Upgrade note

No configuration changes are required. Existing installs can upgrade in place
with `zeline update`, or `/update` from Telegram. If you had raised
`agent.max_tool_rounds` yourself, your value is kept — the change is to the
default for installs that never set it.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.2.9/docs/installation.md) for install commands on every supported platform, and the [changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.2.9/CHANGELOG.md) for the full list of changes with links to every pull request.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
