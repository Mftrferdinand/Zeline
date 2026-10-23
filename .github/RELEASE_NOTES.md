# Zeline v0.4.3

Zeline is the open-source agentic AI framework by Zerolinear.

This release adds the reviewed bounded native static engine to v0.4.2 and
includes audited asynchronous callback bindings. It is not a wholesale
restoration of the removed v0.3.5–v0.3.9 code line.

## Highlights

- **Built-in bounded static reverse engineering.** `reverse_engineer` inspects
  ELF/PE metadata and source inventory, validates symbols/imports, and provides
  native disassembly plus paginated references. It never executes the analyzed
  binary or source. Capstone/tree-sitter are packaged dependencies rather than
  a separate manually configured service.
- **Explicit static versus runtime identity.** PE32/PE64 import-slot references
  bind to validated IAT metadata. Runtime callees and provider resolution remain
  unknown; unsupported/far/addressing forms are not attributed by guesswork.
- **Worker and pagination controls.** Memory/time budgets, cancellation,
  fail-closed validation, evidence digests and bounded retained rows remain in
  force. These controls are not an OS sandbox, universal RSS cap or claim of
  whole-program completeness.
- **Audited callback bindings.** Provider payload/backoff and asynchronous
  batch invocation state are bound to their own iteration instead of a later
  loop value. Existing cancellation and evidence-preservation behavior remains.
- **Cross-platform verification.** Full configured pytest and fresh-wheel
  native probes cover Linux, Windows and macOS. Test skips and platform warnings
  remain visible; warnings are not silently reclassified as fixes.

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
python -m pip install --upgrade zeline==0.4.3
```

Or install in an isolated tool environment:

```sh
uv tool install zeline==0.4.3
```

POSIX installer — Termux, Linux, macOS and iSH:

```sh
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.3/install.sh && bash install.sh
```

Windows PowerShell:

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.3/install.ps1 -OutFile install.ps1; .\install.ps1
```

The installers download the matching versioned wheel and verify its SHA-256
checksum. Run `zeline setup` after a new install. Existing installations can use
`zeline update`, or `/update` in Telegram.

## Upgrade notes

The new engine performs static analysis only. It does not run targets, load
imports, resolve live loader state or provide a network/OS sandbox. Existing
network tooling is not a multi-tenant isolation boundary. PRs held by the
maintainer audit are not included in this release.


- Back up your existing configuration before upgrading. No manual configuration
  migration is required by this release; explicit operator settings are retained.
- Live code changes require a gateway restart. The updater drains active work
  and preserves the gateway selection. Updating a source checkout alone does
  not update a separately installed launcher environment.
- The mobile app remains a separate product and release cycle.
- This release does not claim to eliminate all bugs or provider/network errors.

## Documentation

- [Installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.4.3/docs/installation.md)
- [Static reverse engineering](https://github.com/Mftrferdinand/Zeline/blob/v0.4.3/docs/reverse-engineering.md)
- [Changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.4.3/CHANGELOG.md)
- [Bounded runtime I/O](https://github.com/Mftrferdinand/Zeline/blob/v0.4.3/docs/bounded-tool-runtime.md)
- [Optional MCP setup](https://github.com/Mftrferdinand/Zeline/blob/v0.4.3/docs/extending.md)

## Release assets

- `zeline-0.4.3-py3-none-any.whl`
- `zeline-0.4.3.tar.gz`
- `install.sh` and `install.ps1`
- `SHA256SUMS`

Artifacts are built from the immutable release tag in merged `main` history.
GitHub Release and PyPI receive the same wheel and source archive.

*Agent-generated release notes.*

*Agent-generated release notes. Publication is gated by repository CI and artifact verification.*
