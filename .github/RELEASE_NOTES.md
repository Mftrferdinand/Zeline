# Zeline v0.4.1

Zeline is the open-source agentic AI framework by Zerolinear.

This release brings the reviewed runtime and Telegram improvements on `main`
into a versioned distribution. It builds on the retained v0.3.4 baseline; it is
not a wholesale restoration of the removed v0.3.5–v0.3.9 code line.

## Highlights

- **Bounded, resumable tool I/O.** Read files in bounded pages, resume with
  validated cursors, and preserve complete process output in bounded spools.
  Invalid UTF-8 and incomplete capture remain visible failures rather than
  silently truncated success. Process ownership and cleanup respect cancellation.
- **Evidence-preserving context.** Session compaction and stored context windows
  retain tool-call/result integrity and continuation evidence rather than merely
  trimming the newest messages.
- **Safer self-improvement.** Internal reflection proposes skill changes instead
  of silently overwriting or deleting active procedures. SkillForge supports
  observed usage, revision proposals, test gates, explicit review and backups.
  Routine operator-requested skill management remains available.
- **Truthful learning phases.** 📙 Self-Enhancement, 📘 Self-Improvement and
  📗 Self-Refinement are emitted only for supported actions and evidence. Empty,
  cancelled or failed reviews do not fabricate completed phases; incomplete
  reviews remain visible. Substantial research can qualify for review.
- **Updated Telegram activity UI.** Restored activity icons, including
  `📂 Executing browser script…`, compact letter-choice questions with durable
  receipts, separate post-task learning messages, unquoted live narration,
  preserved final-answer attribution and explicit stop confirmation. Progress
  favors findings, blockers and decisions over repetitive routine narration.
- **Runtime and cron hardening.** Reviewed execution-policy, cancellation,
  continuation and scheduler fixes, including macOS cleanup race handling.
  Subprocess cleanup never group-signals a shared or foreign group; owned
  descendants and Windows tree termination retain their cleanup behavior.
  This defensive fix does not establish the cause of any past host outage.
- **Optional MCP integrations.** Documented Parallel Search and local Sem code
  lookup. These are optional external servers, not new built-in runtime tools;
  their access and side effects are documented in the integration guide.

## Verification

The integrated core passed the complete configured CI matrix, including
Python 3.10–3.13, Windows and macOS. Release metadata, installers, source
archives and wheel contents are checked again for this version. The release
workflow verifies checksums and bundled assets and generates build provenance.
PyPI uses Trusted Publishing (OIDC), not a long-lived API token stored in this
repository. GitHub Actions uploads the verified distributions without rebuilding.

## Install or upgrade

PyPI, inside a virtual environment:

```sh
python -m pip install --upgrade zeline==0.4.1
```

Or install in an isolated tool environment:

```sh
uv tool install zeline==0.4.1
```

POSIX installer — Termux, Linux, macOS and iSH:

```sh
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.1/install.sh && bash install.sh
```

Windows PowerShell:

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.1/install.ps1 -OutFile install.ps1; .\install.ps1
```

The installers download the matching versioned wheel and verify its SHA-256
checksum. Run `zeline setup` after a new install. Existing installations can use
`zeline update`, or `/update` in Telegram.

## Upgrade notes

- Back up your existing configuration before upgrading. No manual configuration
  migration is required by this release; explicit operator settings are retained.
- Live code changes require a gateway restart. The updater drains active work
  and preserves the gateway selection. Updating a source checkout alone does
  not update a separately installed launcher environment.
- The mobile app remains a separate product and release cycle.
- This release does not claim to eliminate all bugs or provider/network errors.

## Documentation

- [Installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.4.1/docs/installation.md)
- [Changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.4.1/CHANGELOG.md)
- [Bounded runtime I/O](https://github.com/Mftrferdinand/Zeline/blob/v0.4.1/docs/bounded-tool-runtime.md)
- [Optional MCP setup](https://github.com/Mftrferdinand/Zeline/blob/v0.4.1/docs/extending.md)

## Release assets

- `zeline-0.4.1-py3-none-any.whl`
- `zeline-0.4.1.tar.gz`
- `install.sh` and `install.ps1`
- `SHA256SUMS`

Artifacts are built from the immutable release tag in merged `main` history.
GitHub Release and PyPI receive the same wheel and source archive.

*Agent-generated release notes.*
