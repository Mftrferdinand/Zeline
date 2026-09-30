# Changelog
## [0.3.6] — 2026-09-30

### Added
- **Connectors framework** — base class + registry for external services ([#286]).
- **GitHub connector** — issues, PRs, repos via API ([#286]).
- **Google connector** — Gmail, Calendar, Sheets, Drive ([#288]).
- **WhatsApp connector** — Business Cloud API (send_text, send_template) ([#289]).
- **Media tools batch** — `tts`, `qr_code`, `transcribe_audio`, `pdf_tool` ([#285]).
- **Edit video tool** — ffmpeg CapCut-style operations (trim, crop, text, speed) ([#284]).
- **Edit image tool** — provider `/images/edits` ([#283]).
- **Generate video tool** — Veo text-to-video ([#282]).
- **Parity batch** — proactive briefing, memory nudges, skill curator, recall digest, crypto wallet fix ([#281]).
- **Provider API key pools** — multiple keys with automatic rotation ([#280]).

### Changed
- **Mid-turn steering**: urgent message interrupts running task with banner; ordinary message injected as steer guidance ([#273], [#290]).
- **Task progress indicator**: `📋 Updating tasks X → status` in the live feed ([#290]).
- **Narration fallback**: silent models now narrate before executing tools ([#290]).
- **/stop behavior**: progress bubble finalized (not deleted) so user can still see context ([#290]).
- **/update reliability**: retry 3x with backoff + health-check after gateway restart ([#291]).
- **Reasoning-content fallback**: thinking models no longer return empty replies ([#276]).

### Fixed
- **Telegram flood-ban**: honor `429 retry_after`, throttle progress edits ([#272]).
- **Telegram cache-bust**: egress-proxy cache on getUpdates polling ([#287]).
- **QR code dependency**: `qrcode[pil]` declared ([#285]).
- **Session amnesia**: `last_topic` tracking — "lanjut" refers to most recent topic, not session start ([#290]).
- **Steer ack icon**: ⏩ → ✈️ ([#290]).
- **Stop message**: `❄️ Stopped — <title>` only, no extra description ([#290]).
- **classify_steer**: context-aware (HARD vs SHORT patterns) — "jangan" in long refinement no longer triggers false interrupt ([#290]).
- **_consume_stop**: actually consumes entry so next turn not blocked ([#290]).
- **on_narration guard**: skip send if cancel_event set ([#290]).

## [0.3.5] — 2026-09-29

### Added
- **`/steer <prompt>` command** (Telegram): steer the running task explicitly from the command menu; behaves like a normal message when no turn is running.
- **Held-task memory across interruptions**: a task parked by an urgent message is remembered so Zeline can offer to resume it after the interruption is handled.
- **New bundled skills**: `voice-reply` (anime-female VN style, Indonesian) ([#275]), `file-converter` ([#271]), `video-downloader` ([#270]), `airdrop-manager` ([#269]).

### Changed
- **Mid-turn steering is now steer-first**: a message sent while a turn is running is injected into the running turn (arrives after the next tool call) instead of aborting the task via a keyword heuristic. The turn keeps running; the user cancels explicitly with `/stop`. Busy-acks are terse, English-only, and debounced to one per 30s.
- **Progress labels rewritten** to short action phrases (`Reading`, `Writing`, `Editing`, `Running code`, `Searching files for …`, `Searching the web for …`); trailing ellipses and filler words removed, emoji icons unchanged.
- **Self-identity locked in**: Zeline consistently knows it is Zeline by Zerolinear, plus its configurable chat name; never answers "I don't know what Zeline/Zerolinear is".

### Fixed
- **Web search no longer fails silently**: `r.jina.ai` reader proxy started rejecting browser User-Agents with HTTP 403, which collapsed Bing+DDG search to Google News + Wikipedia only ("web search failed on all sources"). The reader now uses a bot UA the proxy accepts; verified 0 → 7/9/6 results on representative queries.
- **Telegram flood-ban handling**: honor `429 retry_after` and throttle progress-bubble edits so long tasks with rapid tool calls no longer trip a flood ban.
- **Reply-to context**: quoted-message text is injected as `[Replying to: "..."]` so the model resolves which message "this" refers to (distinguishes replying to the bot / own message / someone else).
- **`sessions.progress()` guarded** for lightweight session stubs in the test suite.

## [0.3.4] — 2026-09-11

### Added
- **Nested Model & Route Picker**: Hierarchical Telegram `/model` selector (`router → route → model`) with compact single-column layout and safe 64-byte callbacks ([#260], [#261], [#262], [#263]).
- **Essential Skills Corpus**: Shipped 10 new bundled skills including `material-design-icons` ([#263], [#265], [#266]).
- **Live Catalog Refresh**: `/model` triggers live catalog rediscovery (`force_refresh=True`) and shortened cache TTL (60s) so provider outage recoveries show up immediately.

### Fixed
- **SSE Non-Stream Retry**: Automatically fallback to non-stream request when provider SSE stream finishes with reasoning-only tokens ([#267]).
- **Bundled Skills & Picker Resilience**: Fixed bundled skill companion folder paths and hardened picker parsing ([#264]).

## [0.3.3] — 2026-09-06

### Added
- Self-learning lessons wired into runtime: auto-capture tool failures, auto-resolve on retry success
- `resolve_lesson` tool (model-callable, full profile) for explicit lesson resolution
- Reflection prompt updated to mention lessons + `resolve_lesson`
- `/lessons` command (Telegram, owner-only) + `zeline lessons` CLI command
- Credential redaction in lessons (`_redact_text`, `_safe_arg_value`, `_escape_prompt_text`)
- Cumulative reflection counter (`_tool_calls_since_reflection`) + `_refresh_system_prompt()`

### Fixed
- **HIGH**: Prompt injection hardening — strip XML-like tags from untrusted error/fix text before system prompt injection
- **MEDIUM**: File handle leak on background `Popen` failure (tools.py)
- **LOW**: Remove dead code `_parse_ddg_html`

Every entry links to the pull request that made the change. Versions follow
[Semantic Versioning](https://semver.org/); the `0.x` line means the public
Python API may still change between minor versions, while the CLI and gateway
configuration are treated as stable and migrated on upgrade.

The install commands for each release are pinned to its tag, so an older
release's documented one-liner keeps working after a newer release ships.

## [Unreleased]

## [0.3.2] — 2026-09-06

### Changed

- Removed the `npm install -g zeline` route and its wrapper package
  (`bin/install.js`, `bin/zeline.js`, `package.json`, `README.npm.md`,
  `.npmignore`). The npm wrapper added a third install path whose complexity
  was not justified — `pip install zeline` (PyPI) and `curl install.sh` /
  `iwr install.ps1` cover every platform. The `Node.js 18+` requirement in
  the docs now refers only to the WhatsApp gateway, not to the Zeline install.
  Documentation across README, installation guide, and both localized readmes
  was simplified to three routes: PyPI (recommended), curl/iwr (fallback).

### Added

- Windows Defender / antivirus troubleshooting section in the installation
  guide. ML-based antivirus engines commonly flag freshly-created Python
  runtime directories; the new section explains why this is a false positive,
  how to whitelist the Zeline folders, and where to find upstream context.

## [0.3.1] — 2026-09-06

### Added

- Zeline is now on PyPI via Trusted Publishing (OIDC). `pip install zeline`
  and `uv tool install zeline` are now supported install routes on every
  platform, documented across the README, installation guide, and both
  localized readmes. The release workflow's `publish-pypi` job uploads the
  same verified wheel and sdist that passed the release gate — no API token
  stored in the repository. `PYPI_PUBLISHED` flipped to `True` in
  `test_community_docs.py`, which now *requires* the PyPI route to be
  documented (previously it forbade it).

## [0.3.0] — 2026-09-06

### Added

- `npm install -g zeline` is now an install route on every platform with
  Node.js ≥ 18. The wrapper is a thin distribution shim — it does not
  reimplement Zeline in JavaScript. It detects Python 3.10+ on your `PATH`,
  downloads the same versioned wheel and `SHA256SUMS` from the matching
  GitHub release, verifies the SHA-256 checksum (same trust path as
  `install.sh` and `install.ps1`), and installs into the same private
  runtime at `~/.local/share/zeline`. The `zeline` bin shim then launches
  `python -m zeline.cli` against that runtime. `curl` and `iwr` remain
  available for machines without Node.js — npm is a parallel route, not a
  replacement. ([#253](https://github.com/Mftrferdinand/Zeline/pull/253),
  [#254](https://github.com/Mftrferdinand/Zeline/pull/254))

### Changed

- The npm wrapper prefers `py -3` on Windows and rejects the Microsoft
  Store `python.exe` stub by checking `sys.executable` for `WindowsApps` —
  the same guard `install.ps1` uses — so a machine without real Python
  gets a clear error instead of a Store popup.

## [0.2.9] — 2026-09-04

### Added

- Every tool the live Telegram feed can report now has a label that names the
  work and its object. Eight of the twenty-nine registered tools had no branch
  and fell through to a catch-all that printed the raw function name —
  `🔧 recall history: lanjut`, `🔧 browser: open`, `🔧 code intel: diagnostics`,
  `🔧 download file: <full URL>` — which is a debug dump, not progress.
  `runtime_info` now reads as a runtime identity check, `browser` and
  `code_intel` get a verb per action plus the page host or the file and line,
  and `download_file` names the destination file. MCP tools were also hitting
  the fallback, where the underscore substitution produced
  `🔧 mcp  mem0  add memory`; they render as `🧩 add memory via mem0`. A test
  asserts no registered tool falls back, so a newly added tool cannot regress
  into it. ([#233])
- The release workflow verifies the built wheel carries the whole bundled
  surface — every skill, every registered tool, and every companion asset under
  `zeline/skills` and `zeline/zenith_tools` — and fails the release rather than
  publishing a thinner install. `[tool.setuptools.package-data]` matches by
  extension, so a skill that gains a `.sh`, `.json`, or `.yaml` companion could
  silently miss the wheel and `zeline update` would then remove a working skill
  from an operator's install. Measured on a clean clone: 495 files, 255 skills,
  109 of them the Zenith corpus, 29 tools.
- Model discovery works against providers that do not follow one response shape:
  `/models`, `/v1/models`, and `/api/tags` are each tried, and `data`, `models`,
  `data_list`, and bare list payloads are all parsed. ([#223])

### Changed

- `agent.max_tool_rounds` defaults to 150 (was 20) and `max_turn_seconds` to
  4500 (was 1800). Twenty rounds is roughly ten read-then-edit cycles, which a
  multi-file change exhausts before it is finished; the clock is kept above
  `rounds × 30 s` so the round budget stays the real limit and the clock stays a
  backstop for a stuck turn. ([#223])
- The Telegram activity feed is a single one-line code card per command instead
  of a stack of tall cards with `COPY CODE` buttons. Two independent things push
  Telegram into the tall variant and both are avoided: a nested
  `<code class="language-…">` draws the language header and copy button, and
  content wider than one rendered line makes the card grow. Commands are
  flattened to one line, capped at 37 characters, and cut at the last word
  boundary with three ASCII dots — the `…` glyph sits flush against the last
  character in the card's monospace font and a mid-token cut reads as a bug.
  ([#224], [#227], [#228], [#229])
- Narration is solution-focused. The system prompt previously mandated an opener
  plus one sentence per tool batch, which produced a running commentary of
  trivial mechanical steps; routine reads and greps now happen silently and the
  agent speaks for a finding, a phase change, a decision with its reason, or the
  answer. ([#224])
- `CONTRIBUTING.md` documents the fork-and-pull-request path. Its opening
  instruction was `git push -u origin <branch>` against this repository, which
  fails with 403 for everyone without push access, and the word "fork" appeared
  nowhere in it or in any README. ([#221])
- The README docs badge points at `zeline.zerolinear.com` rather than a generic
  "Documentation" label. ([#226])
- `ZELINE.md` is now this repository's project-conventions file rather than a
  persona document. It sits first in `RULE_FILENAMES`, so anyone who clones the
  repo and runs `zeline` inside it had ~3k characters injected into the system
  prompt of every turn — and that text claimed "60 bundled Zenith skills" (there
  are 109), referred to a `zeline-zenith-sk*` skill prefix that no longer
  exists, and carried a maintainer credit line. Runtime identity was never
  sourced from it: `config._load_soul()` reads the packaged `zeline/SOUL.md`.
  The file now states what `project_rules` is for — layout, the real build and
  test commands, code and commit conventions, and what not to commit — with
  every claim checked against the tree. ([#232])

### Fixed

- `/stop` lands in under 0.1 s instead of up to 180 s. Cancellation was a flag
  the agent could only notice between provider calls, so a stop issued during a
  blocking request waited for that request to return. `force_cancel()` now
  closes the in-flight response and its socket, and cancellation is also checked
  before a request goes out and inside the model-failover loop. ([#225])
- `lanjut` resumes the session you are actually in. Continuation resolved to the
  newest turns sharing the newest *title*, so a fresh session whose title
  matched an older bucket recalled the previous day's work as if it were in
  progress. ([#230])
- Bundled skills work for whoever installed Zeline, not only on the maintainer's
  phone. Several shipped with hardcoded personal paths, accounts, and site
  names, so for every other user they either failed on the first command or
  pointed somewhere irrelevant. ([#219])
- Public documentation no longer advertises a PyPI install that does not work.
  `pip install zeline` / `uv tool install zeline` were announced in the same
  release whose upload failed on `invalid-publisher`; a single `PYPI_PUBLISHED`
  switch in `tests/test_community_docs.py` now forbids the claim until the first
  upload actually succeeds, and then requires the docs to describe it. ([#214])
- A release no longer reports failure because PyPI has no Trusted Publisher
  configured. `publish-pypi` failed with `invalid-publisher` on every release and
  painted the `pypi` deployment red on the repository page — for a release whose
  assets were built, checksum-verified, attested, and published. The upload is
  now gated on a probe that asks PyPI whether it accepts this workflow's
  identity, so the job is *skipped* with setup instructions when no publisher
  exists and runs normally once one does. A skipped job says "not configured";
  a failed job says "broken". ([#216], [#217], [#218])
- `zeline update` restarts the gateways that were actually running. It read the
  selection after `drain_then_stop()` had already deleted the state file, so an
  operator who started only Telegram got every enabled gateway back — WhatsApp
  and Discord launched on a phone by an unrelated command, with no indication
  why. The selection is now captured from `status()` before the stop and passed
  back to `start()`. ([#215])
- `tests/test_updater.py` no longer drains and relaunches the machine's real
  gateway while it runs. Every test that calls `update()` now stubs
  `zeline.gateway_service`; previously the suite killed a live gateway and left
  a stale PID behind. ([#215])

### Removed

- The mobile-app HTTP surface no longer lives in this repository. The framework
  ships the agent runtime and the messaging gateways that adapt it to a chat
  platform; the app's own REST/SSE server, its agent/session store, its JWT
  auth, and its client-facing event schema are a separate product with a
  separate release cycle, and keeping them here made a framework release gate on
  an app change. Removed: `zeline/gateways/zeline_app.py`,
  `zeline/gateways/zeline_app_runtime.py`, `zeline/app_auth.py`,
  `zeline/app_data.py`, `zeline/tool_events.py`, `run_zeline_app.py`,
  `verify_zeline_app_real.py`, `ARCHITECTURE.md`, `docs/ZELINE_APP_API.md`,
  `docs/SSE_EVENT_SCHEMA.md`, `examples/zeline_app_client.py`, and their two
  test modules. Nothing the CLI or the messaging gateways use is affected — no
  remaining module imported any of them. The `gateways.zeline_app` config
  block and its loopback tool-policy branch are gone with it; a `zeline_app`
  entry left in an existing `config.json` is inert. ([#231])
- Dead files with no importer or reference: `tests/mock_provider.py` (unused
  since the real provider stubs landed) and `assets/zeline-logo.png` /
  `assets/zeline-social-preview.png`, which rendered a pre-rebrand wordmark and
  were referenced by nothing — the README uses `assets/zerolinear-logo.png`.
  ([#231])

## [0.2.8] — 2026-09-01

### Added

- Publish to PyPI from the release workflow using Trusted Publishing (OIDC), so
  no API token is stored in the repository. The job uploads the artifacts that
  already passed checksum and metadata verification rather than rebuilding, so
  the bytes on PyPI are the bytes attested in the GitHub release. The `zeline`
  name is not claimed on PyPI yet, so `pip install zeline` does not work from
  this release — the pipeline is in place and the installer remains the
  supported route. ([#210])
- A correctness lint gate in CI (`ruff check`, selecting undefined names, broken
  f-strings, invalid syntax, mistaken comparisons) plus a non-blocking report of
  remaining style debt. ([#210])
- `agent.max_turn_seconds` is configurable through `zeline setup agent`. ([#211])
- Turn a local OpenAPI document into real tools instead of hand-written
  wrappers. ([#209])

### Fixed

- The per-turn wall clock no longer cuts off work the round limit allows. It was
  a hardcoded 360s while `max_tool_rounds` defaults to 20; since one model call
  takes 7-50s, the clock always expired first, the round limit was unreachable,
  and multi-step tasks were interrupted and forced to summarise. The default is
  now 1800s and the clock is a backstop for a stuck turn rather than the work
  scheduler. ([#211])
- Each Discord connection's heartbeat stays on its own socket. The keepalive
  thread closed over the reconnect loop's locals, so after a reconnect a thread
  from the dead connection wrote frames to the new socket alongside the new
  thread — the bot reported connected and silently stopped receiving
  messages. ([#212])
- `revenue_optimizer` annotated a return type with a name that only existed
  inside a `try` block, so the annotation never resolved. ([#210])
- Telegram: the status line sits below the feed and says when the provider is
  the slow part; a slow greeting is no longer labelled "Working". ([#207], [#208])
- Provider errors report what each HTTP status actually means instead of
  guessing. ([#206])
- `/stop` sends one message, and "lanjut" resumes the most recent thread. ([#205])

### Changed

- Development status is now Beta rather than Pre-Alpha, and the package
  advertises Documentation, Issues, and Changelog URLs on PyPI.
- The repository documents its own process: `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, this changelog, issue and pull request templates, and
  `docs/extending.md` — a written path for adding custom tools, plugin hooks,
  OpenAPI tools, and MCP servers, which previously existed only as docstrings.
- Self-improvement writes through a real skill surface instead of accumulating
  duplicates. ([#204])

## [0.2.7] — 2026-08-31

### Added

- A first-party gateway for the mobile app: REST + SSE on `/api/v1`, sharing the
  same agent runtime as the CLI and Telegram. Token deltas stream as
  `assistant.delta`; tool activity arrives as `tool.started` / `tool.output` /
  `tool.completed` so the client never parses prose to learn what happened.
  ([#202]) *This surface has since moved out of this repository — see
  Unreleased.*
- Oversized tool output is offloaded to disk instead of discarded. ([#199])
- `/version` and `/update` in Telegram, so a phone install never needs a
  shell. ([#197])

### Fixed

- Stop actually stops: cancellation is checked inside the streaming read loop,
  cutting a stop request from 113-211s to 2.7s. ([#202])
- Dangling tool calls are repaired rather than deleted, so a failed turn no
  longer discards completed work. ([#201])
- Evicted turns are archived and replaced with a digest, so trimming history
  stops erasing decisions and file writes. ([#200])

### Changed

- The install documentation is one line, and the hand-copied checksum block is
  gone — it compared the installer against a manifest fetched over the same
  connection from the same release, so it proved nothing. Build provenance
  attestation, which is signed independently by GitHub, is documented
  instead. ([#195])

## [0.2.6] — 2026-08-30

### Added

- Tool schemas are sent lazily behind a `tool_search` catalogue, on by
  default. ([#188], [#194])
- Drive a real browser over the Chrome DevTools Protocol. ([#189])
- Ask a real language server about the code through `code_intel`. ([#190])
- Sub-agents run in parallel with roles and an optional verifier. ([#191])
- Scheduled jobs run inside the gateway. ([#192])
- Operator-supplied Python files load as custom tools. ([#186])
- Plugin hooks can audit, rewrite, or block any tool call. ([#187])
- Snapshot files before writes, with `zeline undo`. ([#185])
- Token usage recording and `zeline stats`. ([#184])
- Export, import, and fork conversation sessions. ([#183])
- Project rules (`ZELINE.md` / `AGENTS.md`) and `zeline init`. ([#182])
- Run the project formatter after write and edit. ([#181])

### Fixed

- A listening HTTP adapter counts as connected. ([#193])

## Earlier releases

Release notes for 0.2.5 and earlier are on the
[releases page](https://github.com/Mftrferdinand/Zeline/releases).

[Unreleased]: https://github.com/Mftrferdinand/Zeline/compare/v0.3.6...main
[0.3.6]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.3.6
[0.3.5]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.3.5
[0.3.4]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.3.4
[0.3.3]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.3.3
[0.3.2]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.3.2
[0.3.1]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.3.1
[0.3.0]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.3.0
[0.2.9]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.2.9
[0.2.8]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.2.8
[0.2.7]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.2.7
[0.2.6]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.2.6
[#269]: https://github.com/Mftrferdinand/Zeline/pull/269
[#270]: https://github.com/Mftrferdinand/Zeline/pull/270
[#271]: https://github.com/Mftrferdinand/Zeline/pull/271
[#272]: https://github.com/Mftrferdinand/Zeline/pull/272
[#273]: https://github.com/Mftrferdinand/Zeline/pull/273
[#275]: https://github.com/Mftrferdinand/Zeline/pull/275
[#276]: https://github.com/Mftrferdinand/Zeline/pull/276
[#280]: https://github.com/Mftrferdinand/Zeline/pull/280
[#281]: https://github.com/Mftrferdinand/Zeline/pull/281
[#282]: https://github.com/Mftrferdinand/Zeline/pull/282
[#283]: https://github.com/Mftrferdinand/Zeline/pull/283
[#284]: https://github.com/Mftrferdinand/Zeline/pull/284
[#285]: https://github.com/Mftrferdinand/Zeline/pull/285
[#286]: https://github.com/Mftrferdinand/Zeline/pull/286
[#287]: https://github.com/Mftrferdinand/Zeline/pull/287
[#288]: https://github.com/Mftrferdinand/Zeline/pull/288
[#289]: https://github.com/Mftrferdinand/Zeline/pull/289
[#290]: https://github.com/Mftrferdinand/Zeline/pull/290
[#291]: https://github.com/Mftrferdinand/Zeline/pull/291
[#260]: https://github.com/Mftrferdinand/Zeline/pull/260
[#261]: https://github.com/Mftrferdinand/Zeline/pull/261
[#262]: https://github.com/Mftrferdinand/Zeline/pull/262
[#263]: https://github.com/Mftrferdinand/Zeline/pull/263
[#264]: https://github.com/Mftrferdinand/Zeline/pull/264
[#265]: https://github.com/Mftrferdinand/Zeline/pull/265
[#266]: https://github.com/Mftrferdinand/Zeline/pull/266
[#267]: https://github.com/Mftrferdinand/Zeline/pull/267
[#181]: https://github.com/Mftrferdinand/Zeline/pull/181
[#182]: https://github.com/Mftrferdinand/Zeline/pull/182
[#183]: https://github.com/Mftrferdinand/Zeline/pull/183
[#184]: https://github.com/Mftrferdinand/Zeline/pull/184
[#185]: https://github.com/Mftrferdinand/Zeline/pull/185
[#186]: https://github.com/Mftrferdinand/Zeline/pull/186
[#187]: https://github.com/Mftrferdinand/Zeline/pull/187
[#188]: https://github.com/Mftrferdinand/Zeline/pull/188
[#189]: https://github.com/Mftrferdinand/Zeline/pull/189
[#190]: https://github.com/Mftrferdinand/Zeline/pull/190
[#191]: https://github.com/Mftrferdinand/Zeline/pull/191
[#192]: https://github.com/Mftrferdinand/Zeline/pull/192
[#193]: https://github.com/Mftrferdinand/Zeline/pull/193
[#194]: https://github.com/Mftrferdinand/Zeline/pull/194
[#195]: https://github.com/Mftrferdinand/Zeline/pull/195
[#197]: https://github.com/Mftrferdinand/Zeline/pull/197
[#199]: https://github.com/Mftrferdinand/Zeline/pull/199
[#200]: https://github.com/Mftrferdinand/Zeline/pull/200
[#201]: https://github.com/Mftrferdinand/Zeline/pull/201
[#202]: https://github.com/Mftrferdinand/Zeline/pull/202
[#204]: https://github.com/Mftrferdinand/Zeline/pull/204
[#205]: https://github.com/Mftrferdinand/Zeline/pull/205
[#206]: https://github.com/Mftrferdinand/Zeline/pull/206
[#207]: https://github.com/Mftrferdinand/Zeline/pull/207
[#208]: https://github.com/Mftrferdinand/Zeline/pull/208
[#209]: https://github.com/Mftrferdinand/Zeline/pull/209
[#210]: https://github.com/Mftrferdinand/Zeline/pull/210
[#211]: https://github.com/Mftrferdinand/Zeline/pull/211
[#212]: https://github.com/Mftrferdinand/Zeline/pull/212
[#214]: https://github.com/Mftrferdinand/Zeline/pull/214
[#215]: https://github.com/Mftrferdinand/Zeline/pull/215
[#216]: https://github.com/Mftrferdinand/Zeline/pull/216
[#217]: https://github.com/Mftrferdinand/Zeline/pull/217
[#218]: https://github.com/Mftrferdinand/Zeline/pull/218
[#219]: https://github.com/Mftrferdinand/Zeline/pull/219
[#221]: https://github.com/Mftrferdinand/Zeline/pull/221
[#223]: https://github.com/Mftrferdinand/Zeline/pull/223
[#224]: https://github.com/Mftrferdinand/Zeline/pull/224
[#225]: https://github.com/Mftrferdinand/Zeline/pull/225
[#226]: https://github.com/Mftrferdinand/Zeline/pull/226
[#227]: https://github.com/Mftrferdinand/Zeline/pull/227
[#228]: https://github.com/Mftrferdinand/Zeline/pull/228
[#229]: https://github.com/Mftrferdinand/Zeline/pull/229
[#230]: https://github.com/Mftrferdinand/Zeline/pull/230
[#231]: https://github.com/Mftrferdinand/Zeline/pull/231
[#232]: https://github.com/Mftrferdinand/Zeline/pull/232
[#233]: https://github.com/Mftrferdinand/Zeline/pull/233
