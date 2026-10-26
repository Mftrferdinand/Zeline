# Zeline v0.4.4

Zeline is the open-source agentic AI framework by Zerolinear.

This release integrates the reviewed v0.4.4 work on top of the retained v0.4.3
baseline. It adds evidence-backed self-learning receipts and provenance,
bounded native analysis continuations, an optional GEPA training bridge, and
provider-correct API progress labels. It does not restore the removed v0.3.5–v0.3.9
code line.

## Highlights

- **Evidence-backed self-learning.** Memory, lesson and skill mutations emit
  versioned receipts with record IDs and revisions. Duplicate/no-op changes are
  suppressed, retrieval feedback is revision-aware, and successful tool evidence
  is kept separate from task correctness or acceptance.
- **Review-gated skill changes.** Reflection can propose a skill change, but the
  active skill remains unchanged until testing and explicit operator review/apply.
  Reflection memory writes are bounded, low-confidence and subject to the
  existing expiry policy.
- **Bounded native static analysis.** `reverse_engineer` continues to inspect
  ELF/PE metadata, source structure, symbols, imports, disassembly and paginated
  references without executing the analyzed artifact. Continuation pages can be
  bound to an expected SHA-256 digest.
- **Optional GEPA bridge.** GEPA receives training-only cases and returns a
  report/host handoff; it does not receive held-out labels, modify model weights,
  auto-promote a skill, change production prompts, or invoke a provider on the
  default agent path.
- **Provider-correct progress UI.** Telegram API progress labels identify the
  actual provider and make the complete request label link to the actual
  HTTP(S) request URL.
- **Cross-platform verification.** The configured CI matrix covers Python
  3.10–3.13, Windows and macOS. Package metadata, installers, source archives
  and wheel contents are checked again for this release.

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
python -m pip install --upgrade zeline==0.4.4
```

Or install in an isolated tool environment:

```sh
uv tool install zeline==0.4.4
```

POSIX installer — Termux, Linux, macOS and iSH:

```sh
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.4/install.sh && bash install.sh
```

Windows PowerShell:

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.4.4/install.ps1 -OutFile install.ps1; .\install.ps1
```

The installers download the matching versioned wheel and verify its SHA-256
checksum. Run `zeline setup` after a new install. Existing installations can use
`zeline update`, or `/update` in Telegram.

## Upgrade notes

- A receipt proves that a bounded state mutation or review operation occurred;
  it does not prove better answers, causality or user acceptance.
- The native analysis engine is static and read-only. It does not run targets,
  load imports, resolve live loader state or provide a network/OS sandbox.
  This is not full REA catalog parity; external REA MCP remains optional.
- GEPA is experimental and training-only. Provider/token accounting and stronger
  isolation are still required before any provider-backed optimizer promotion.
- Existing network tooling is not a multi-tenant isolation boundary. PRs held by
  the maintainer audit are not included in this release.
- Back up existing configuration before upgrading. No manual configuration
  migration is required by this release; explicit operator settings are retained.
- Live code changes require a gateway restart. The updater drains active work and
  preserves the gateway selection. Updating a source checkout alone does not
  update a separately installed launcher environment.
- The mobile app remains a separate product and release cycle.
- This release does not claim to eliminate all bugs or provider/network errors.

## Documentation

- [Installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.4.4/docs/installation.md)
- [Evidence-backed self-learning](https://github.com/Mftrferdinand/Zeline/blob/v0.4.4/docs/evidence-backed-self-learning.md)
- [GEPA training bridge](https://github.com/Mftrferdinand/Zeline/blob/v0.4.4/docs/gepa-training.md)
- [Static reverse engineering](https://github.com/Mftrferdinand/Zeline/blob/v0.4.4/docs/reverse-engineering.md)
- [Bounded runtime I/O](https://github.com/Mftrferdinand/Zeline/blob/v0.4.4/docs/bounded-tool-runtime.md)
- [Changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.4.4/CHANGELOG.md)
- [Optional MCP setup](https://github.com/Mftrferdinand/Zeline/blob/v0.4.4/docs/extending.md)

## Release assets

- `zeline-0.4.4-py3-none-any.whl`
- `zeline-0.4.4.tar.gz`
- `install.sh` and `install.ps1`
- `SHA256SUMS`

Artifacts are built from the immutable release tag in merged `main` history.
GitHub Release and PyPI receive the same wheel and source archive.

*Agent-generated release notes. Publication is gated by repository CI and artifact verification.*
