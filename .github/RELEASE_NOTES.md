## Zeline Release

Zeline is the open-source agentic AI framework by Zerolinear.

### Zeline is on PyPI

This is the first release published to [PyPI](https://pypi.org/project/zeline/),
so on any machine that already has Python tooling the install is:

```bash
uv tool install zeline
```

Publishing runs from the release workflow through PyPI Trusted Publishing (OIDC)
— no API token is stored in this repository. The job uploads the exact artifacts
that already passed checksum and metadata verification instead of rebuilding, so
the bytes on PyPI are the bytes attested in this GitHub release. The installer
one-liners below still work unchanged for machines with no Python tooling.

### Highlights

- **A turn is no longer cut short by a clock that could never agree with the
  round limit.** The per-turn wall clock was a hardcoded 360 s while
  `max_tool_rounds` defaults to 20. One model call takes 7–50 s, so the clock
  always expired first: the round limit was unreachable and long multi-step
  tasks were interrupted mid-work and forced to summarise. The default is now
  1800 s — a backstop for a genuinely stuck turn rather than the work scheduler
  — and it is configurable with `zeline setup agent`.
- **A local OpenAPI document becomes real tools.** Point Zeline at an OpenAPI 3
  file and its operations load as namespaced `api_*` tools with schemas derived
  from the document, instead of hand-written wrappers that drift from the API.
  Credentials come from `~/.zeline/.env`, never from the model-visible schema,
  and only `workspace`/`full` profiles load them.
- **Self-improvement writes through a real skill surface.** `manage_skill`
  replaces the old save-only path: `create`, `patch`, `write_file`, `delete`, and
  `list`. Skills are folders (`SKILL.md` plus `references/`, `templates/`,
  `scripts/`, `assets/`), a bundled skill is repaired by copy-on-write into a
  private override that survives upgrades, and a duplicate can be merged away
  with `absorbed_into` instead of accumulating three files for one lesson.
- **A Discord bot no longer goes quiet after a reconnect.** The keepalive thread
  closed over the reconnect loop's locals, so after a reconnect a thread from the
  dead connection wrote heartbeat frames to the *new* socket alongside the new
  thread. Two writers interleave frames, Discord drops the link, and the next
  reconnect leaks another thread — a self-feeding failure whose symptom is a bot
  that reports connected and silently stops receiving messages. Each heartbeat
  now belongs to exactly one connection, and the loop joins it before
  reconnecting.
- **Telegram status tells the truth about who is slow.** The activity feed sits
  above and the status line below it, and once the wait for the provider passes
  20 s the line says so explicitly instead of implying Zeline is busy. A slow
  greeting that ran no tools is no longer labelled "Working".
- **Provider errors say what the status actually means.** `403` is provider quota
  exhausted, not a bad API key; `402` is payment required; `404` is an unknown
  model; `429` is rate limiting. One status table is shared by the agent, the
  CLI, Telegram, media analysis, and image generation.
- **`/stop` sends one message**, and `lanjut` / `terusin` / `gas` resume the most
  recent thread rather than an older topic that happened to share more keywords.
- **CI blocks on correctness lint.** `ruff check` selects undefined names, broken
  f-strings, invalid syntax, and mistaken comparisons — bugs, not taste — so the
  gate can be blocking without a mass reformat. Remaining style debt is reported
  without failing the build and promoted into the gate as it is cleared.

### For contributors

The repository now documents its own process: `CONTRIBUTING.md`,
`CODE_OF_CONDUCT.md`, `CHANGELOG.md`, issue and pull request templates, and
`docs/extending.md` — a written path for adding your own tools, plugin hooks,
OpenAPI tools, and MCP servers, which previously existed only as docstrings in
`zeline/custom_tools.py` and `zeline/plugins.py`.

### Security

Custom tools, plugin hooks, and MCP stdio servers are arbitrary local Python in
the agent's process and load only on the `workspace`/`full` profiles — a public
gateway on `safe` never reaches them. The app gateway binds `127.0.0.1` by
default; exposing a routable interface exposes an agent that can run shell
commands, so put it behind a TLS terminator with its own auth and rotate the
gateway token if it leaks. Provider API keys never appear in any response.

### Upgrade note

No configuration changes are required. Existing installs can upgrade in place
with `zeline update`, or `/update` from Telegram.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.2.8/docs/installation.md) for install commands on every supported platform, and the [changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.2.8/CHANGELOG.md) for the full list of changes with links to every pull request.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
