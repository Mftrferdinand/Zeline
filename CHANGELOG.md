# Changelog

Every entry links to the pull request that made the change. Versions follow
[Semantic Versioning](https://semver.org/); the `0.x` line means the public
Python API may still change between minor versions, while the CLI and gateway
configuration are treated as stable and migrated on upgrade.

The install commands for each release are pinned to its tag, so an older
release's documented one-liner keeps working after a newer release ships.

## [Unreleased]

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
  entry left in an existing `config.json` is inert.
- Dead files with no importer or reference: `tests/mock_provider.py` (unused
  since the real provider stubs landed) and `assets/zeline-logo.png` /
  `assets/zeline-social-preview.png`, which rendered a pre-rebrand wordmark and
  were referenced by nothing — the README uses `assets/zerolinear-logo.png`.

### Fixed

- A release no longer reports failure because PyPI has no Trusted Publisher
  configured. `publish-pypi` failed with `invalid-publisher` on every release and
  painted the `pypi` deployment red on the repository page — for a release whose
  assets were built, checksum-verified, attested, and published. The upload is
  now gated on a probe that asks PyPI whether it accepts this workflow's
  identity, so the job is *skipped* with setup instructions when no publisher
  exists and runs normally once one does. A skipped job says "not configured";
  a failed job says "broken".
- `zeline update` restarts the gateways that were actually running. It read the
  selection after `drain_then_stop()` had already deleted the state file, so an
  operator who started only Telegram got every enabled gateway back — WhatsApp
  and Discord launched on a phone by an unrelated command, with no indication
  why. The selection is now captured from `status()` before the stop and passed
  back to `start()`.
- `tests/test_updater.py` no longer drains and relaunches the machine's real
  gateway while it runs. Every test that calls `update()` now stubs
  `zeline.gateway_service`; previously the suite killed a live gateway and left
  a stale PID behind.

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

[Unreleased]: https://github.com/Mftrferdinand/Zeline/compare/v0.2.8...main
[0.2.8]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.2.8
[0.2.7]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.2.7
[0.2.6]: https://github.com/Mftrferdinand/Zeline/releases/tag/v0.2.6
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
