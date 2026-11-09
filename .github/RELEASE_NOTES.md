# Zeline v0.4.5

Zeline is the open-source agentic AI framework by Zerolinear.

This scoped maintenance release builds on v0.4.4 with targeted stability
improvements: task follow-up scheduling with race-safe cross-process locking,
MCP stdio deadline hardening, checkpoint restore at capacity, bounded offline
evaluation worker I/O, and private persona separation. Agent responses, UI, and
Telegram behavior are preserved from the installed v0.4.4 baseline.

## Highlights

- **Task follow-up scheduling.** `followups.py` and `scheduler.py` gain
  cross-process SQLite `BEGIN IMMEDIATE` locking (5 s wait), one Himalaya
  snapshot per tick, at most two deliveries per tick, and Telegram follow-up
  `timeout=8` with `attempts=1`. New jobs default to `timeout=60`.
- **MCP stdio deadline.** `mcp.py` enforces a read deadline on stdio
  transports, preventing indefinite hangs on unresponsive servers.
- **Checkpoint restore at capacity.** `checkpoints.py` correctly restores when
  the checkpoint store is at its configured maximum.
- **Bounded offline evaluation.** `offline_evaluation.py` streams worker output
  through a pipe reader capped at `MAX_RESULT_BYTES + 1`, replacing unbounded
  `TemporaryFile` growth.
- **Private persona separation.** `config.py` supports a validated per-instance
  SOUL override outside the package directory, fail-closed when empty. The
  public `SOUL.md` in the wheel is the neutral canonical persona.
- **Evidence-backed self-learning.** Carried forward from v0.4.4 — memory,
  lesson and skill mutations emit versioned receipts with record IDs and
  revisions.
- **Checksums and pricing.** Adds the workspace-bound `hash_check` tool,
  corrects longest-prefix usage pricing for dated model names, and escapes
  checksum progress arguments for Telegram HTML.
- **Optional publishing MCP documentation.** Adds deed.page setup guidance with
  explicit retention semantics: pages are permanent unless expiry is requested.
- **Cross-platform verification.** CI matrix covers Python 3.10–3.13,
  Windows and macOS. Windows locking, process spawning, SQLite handle closure,
  and private-file writes are portable; macOS teardown no longer masks useful
  evaluation results when process-group signaling is denied.

## Verification

The release workflow verifies that the tag belongs to merged `main`, matches
package metadata, builds the wheel and source archive, checks their metadata and
checksums, compares bundled skills/tools/assets with the source tree, and
generates build provenance. PyPI uses Trusted Publishing (OIDC), not a
long-lived API token stored in this repository. GitHub Actions uploads the
verified distributions without rebuilding.

## Install or upgrade

PyPI, inside a virtual environment:

```sh
python -m pip install --upgrade zeline==0.4.5
```

Or install in an isolated tool environment:

```sh
uv tool install zeline==0.4.5
```

POSIX installer — Termux, Linux, macOS and iSH:

```sh
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.5/install.sh && bash install.sh
```

Windows PowerShell:

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.5/install.ps1 -OutFile install.ps1; .\install.ps1
```

The installers download the matching versioned wheel and verify its SHA-256
checksum. Run `zeline setup` after a new install. Existing installations can use
`zeline update`, or `/update` in Telegram.

## Upgrade notes

- This maintenance release also includes the checksum tool and optional MCP
  documentation listed above; other audited feature PRs remain deferred.
- Back up existing configuration before upgrading. No manual configuration
  migration is required; explicit operator settings are retained.
- Live code changes require a gateway restart. The updater drains active work and
  preserves the gateway selection.
- The mobile app remains a separate product and release cycle.
- Task follow-up jobs created under v0.4.4 with the old 45 s timeout will
  continue to run; new jobs default to 60 s. No state migration is required.

## Documentation

- [Installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.4.5/docs/installation.md)
- [Task follow-ups](https://github.com/Mftrferdinand/Zeline/blob/v0.4.5/docs/task-followups.md)
- [Private persona override](https://github.com/Mftrferdinand/Zeline/blob/v0.4.5/docs/private-persona.md)
- [Evidence-backed self-learning](https://github.com/Mftrferdinand/Zeline/blob/v0.4.5/docs/evidence-backed-self-learning.md)
- [GEPA training bridge](https://github.com/Mftrferdinand/Zeline/blob/v0.4.5/docs/gepa-training.md)
- [Static reverse engineering](https://github.com/Mftrferdinand/Zeline/blob/v0.4.5/docs/reverse-engineering.md)
- [Bounded runtime I/O](https://github.com/Mftrferdinand/Zeline/blob/v0.4.5/docs/bounded-tool-runtime.md)
- [Changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.4.5/CHANGELOG.md)
- [Optional MCP setup](https://github.com/Mftrferdinand/Zeline/blob/v0.4.5/docs/extending.md)

## Release assets

- `zeline-0.4.5-py3-none-any.whl`
- `zeline-0.4.5.tar.gz`
- `install.sh` and `install.ps1`
- `SHA256SUMS`

Artifacts are built from the immutable release tag in merged `main` history.
GitHub Release and PyPI receive the same wheel and source archive.

*Agent-generated release notes. Publication is gated by repository CI and artifact verification.*
