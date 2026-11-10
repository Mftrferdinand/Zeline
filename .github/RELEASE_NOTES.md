# Zeline v0.4.6

This release packages the runtime updates merged in PR #358, preserving the
existing Zeline interface and operator configuration.

## Highlights

- Task continuity and message attribution, including progress messages first
  created during finalization.
- Durable session recovery and private recovery snapshots, with native Windows
  owner-only ACL validation.
- Bounded Telegram transport, delivery receipts, typing refresh and task-scoped
  progress; uncertain deliveries are not automatically replayed.
- Best-effort memory retrieval telemetry that does not wait behind a writer.
- Per-database transaction coordination for concurrent task-store instances,
  retaining the existing five-second contention budget and atomic transitions.
- Cross-platform SAT child resource limits and same-PID exec/cleanup verification.

## Verification

The integrated runtime passed all 20 CI checks, including Linux, Windows and
macOS. Its final local suite completed with 2679 passed and 5 skipped. The
v0.4.6 preparation changes version metadata, release notes, installer pins and
matching version contracts; release artifacts receive separate build,
metadata, checksum and installation verification.

The tag-triggered workflow verifies merged-main ancestry, tag/package version,
archive metadata and bundled-file parity. Publication status is reported by the
release workflow; these notes do not imply PyPI publication before it completes.

## Install or upgrade

```sh
python -m pip install --upgrade zeline==0.4.6
```

```sh
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.6/install.sh && bash install.sh
```

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.6/install.ps1 -OutFile install.ps1; .\install.ps1
```

## Assets

- `zeline-0.4.6-py3-none-any.whl`
- `zeline-0.4.6.tar.gz`
- `install.sh`, `install.ps1`, and `SHA256SUMS`

[Installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.4.6/docs/installation.md)
[Changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.4.6/CHANGELOG.md)
