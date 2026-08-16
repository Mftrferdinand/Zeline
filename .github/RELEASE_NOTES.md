## Zeline Release

Zeline is the open-source agentic AI framework by Zerolinear.

### Highlights

- **Text-to-image generation** — a new `generate_image` tool creates images from a text prompt (owner profiles only; requires a configured image model).
- **Sub-agent delegation** — `delegate_task` hands a focused subtask to an isolated child agent and returns only its final summary, keeping the main context clean (depth-limited).
- **Telegram replies quote your message** — the answer bubble now visibly replies to the message it answers, so multiple questions never get confused.
- **Cleaner message formatting** — richer response-style guidance plus preserved paragraph/list spacing when rendering to Telegram.
- Request-scoped safety: Zeline evaluates the requested action instead of rejecting an entire task by topic, and continues with safe, useful work when only one step is restricted.
- Cross-platform CLI, messaging gateways, and secure owner-aware tool profiles.
- Versioned, checksum-verified installers for Termux, Linux, macOS, iSH, and Windows PowerShell.
- Immutable release artifacts with build provenance.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.2.2/docs/installation.md) for checksum-verified commands on every supported platform.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
