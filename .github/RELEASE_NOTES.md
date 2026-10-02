## Zeline v0.3.8

Zeline is the open-source agentic AI framework by Zerolinear.

### Installing

PyPI (recommended):

```sh
pip install zeline
# or, in an isolated tool environment:
uv tool install zeline
```

Then `zeline setup`.

One line on every POSIX platform — Termux, Linux, macOS, iSH:

```bash
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.3.8/install.sh && bash install.sh
```

The installer downloads the versioned wheel and verifies it against
`SHA256SUMS` itself, so there is nothing to check by hand.

Every release carries the whole bundled surface — skills, tools, and
external service connectors — and the release workflow diffs the built
wheel against the source tree and fails rather than publishing an install
that is missing any of it.

### Highlights

- **Interactive choice picker (`ask_user`).**
  Operators can now select options from numbered interactive pickers on
  both Telegram and the CLI interface.
- **Graceful gateway drain & user notifications.**
  Before a gateway restart or update, active sessions receive advance warning
  and running turns are allowed to finish cleanly instead of being cut off.
- **Reasoning model auto-continue & fallback.**
  Thinking models that exhaust token budgets in reasoning before producing
  visible output receive an autonomous nudge (up to 2x) to finish, with automatic
  fallback to `reasoning_content` when content is blank.
- **Steer & interrupt reliability.**
  Imperative words (`jangan`, `ganti`, `ubah`, `salah`, etc.) trigger immediate
  interrupts reliably, silent background thread crashes are trapped and notified,
  and dead streams are terminated by watchdogs.
- **Core SOUL and operational rules.**
  Standardized "Narrate, don't interrogate" principles and authorized crypto,
  wallet, and airdrop task workflows into the core agent instructions.

### Upgrade note

No configuration changes are required. Existing installs can upgrade in place
with `zeline update`, or `/update` from Telegram.

### Security

Publishing to PyPI goes through Trusted Publishing (OIDC), so no API token is
stored in this repository. Provider API keys never appear in any response, and
the progress feed prints a URL's host only — never the full URL or a proxy's
credentials.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.3.8/docs/installation.md) for install commands on every supported platform, and the [changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.3.8/CHANGELOG.md) for the full list of changes.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
