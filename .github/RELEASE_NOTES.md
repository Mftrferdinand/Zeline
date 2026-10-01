## Zeline v0.3.7

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
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.3.7/install.sh && bash install.sh
```

The installer downloads the versioned wheel and verifies it against
`SHA256SUMS` itself, so there is nothing to check by hand.

Every release carries the whole bundled surface — skills, tools, and
external service connectors — and the release workflow diffs the built
wheel against the source tree and fails rather than publishing an install
that is missing any of it.

### Highlights

- **Muse-style reasoning engine (new optional example).**
  `examples/muse-reasoning-engine/` shows how to give Zeline a reasoning
  backend running on your own machine: an OpenAI-compatible bridge
  (`bridge.py`, with `tool_calls` passthrough so the backend can drive
  Zeline's full native toolset), a standalone poller (`poller.py`) that
  works with **any** OpenAI-compatible LLM via your own API key, a
  model-agnostic system prompt (natural narration, `ask_user` picker for
  every question, wallet safety rules), a one-command installer
  (`sudo ./install.sh`), and a watchdog timer that reinstalls + restarts
  the stack if units vanish or services die. Zeline itself stays 100%
  provider-agnostic — this is opt-in, nothing changes by default.

### Upgrade note

No configuration changes are required. Existing installs can upgrade in place
with `zeline update`, or `/update` from Telegram. The new example ships in
the source archive and the repository; it is not auto-installed.

### Security

Publishing to PyPI goes through Trusted Publishing (OIDC), so no API token is
stored in this repository. Provider API keys never appear in any response, and
the progress feed prints a URL's host only — never the full URL or a proxy's
credentials.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.3.7/docs/installation.md) for install commands on every supported platform, and the [changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.3.7/CHANGELOG.md) for the full list of changes.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
