## Zeline v0.3.6

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
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.3.6/install.sh && bash install.sh
```

The installer downloads the versioned wheel and verifies it against
`SHA256SUMS` itself, so there is nothing to check by hand.

Every release carries the whole bundled surface — skills, tools, and now
**external service connectors** — and the release workflow diffs the built
wheel against the source tree and fails rather than publishing an install that
is missing any of it.

### Highlights

- **Connectors.** A new `zeline.connectors` framework with a base class and
  registry, plus three production connectors:
  - **GitHub** — issues, pull requests, repositories.
  - **Google** — Gmail, Calendar, Sheets, Drive.
  - **WhatsApp** — Business Cloud API (`send_text`, `send_template`).
- **Media tools.** `tts`, `qr_code`, `transcribe_audio`, `pdf_tool`, plus
  `edit_image` (provider `/images/edits`), `edit_video` (ffmpeg CapCut-style
  trim / crop / text / speed), and `generate_video` (Veo text-to-video).
- **Parity batch.** Proactive briefings (scheduled, delivered to the owner),
  memory consolidation nudges, a private-skill curator with a JSONL ledger, a
  grouped recall digest, and a fix that lets the agent manage the operator's
  own crypto wallets (signing/broadcasting still gated by confirmation).
- **Provider API key pools.** Configure multiple keys per provider; Zeline
  rotates automatically on rate-limit or failure.
- **Mid-turn steering.** A message sent while a turn is running is classified:
  an urgent correction interrupts the running task with a banner and runs
  first; an ordinary message is injected into the running turn as steer
  guidance (it arrives after the next tool call) without aborting the task.
- **Task progress indicator.** `update_task` now renders as
  `📋 Updating tasks X → status` in the live feed, and a board summary helper
  reports `planning N task(s) — A completed, B remaining, C in progress`.
- **Narration fallback.** Models that emit empty `content` alongside
  `tool_calls` (some thinking-model variants) no longer go silent — the agent
  narrates the first tool call (`Running: ls -la`, `Reading config.py…`) so
  the user always sees what is happening.

### Bug fixes

- **`/update` reliability.** The post-update gateway restart now retries 3
  times with backoff and health-checks the result; a failed restart is
  reported loudly instead of leaving the bot dead in silence.
- **Reasoning-content fallback.** When a reasoning model returns an empty
  `content` but fills `reasoning_content`, the non-stream path now surfaces
  that text as the answer instead of the "(provider tidak mengirim jawaban
  teks)" placeholder.
- **Session amnesia after gateway restart.** The session now tracks the most
  recent topic (`last_topic`) on every turn, so "lanjut" / "continue" after a
  restart refers to the latest work — not the first message of the session.
- **Steer classification is context-aware.** Urgency keywords are split into
  always-urgent (stop, batal, cancel) and short-only (jangan, salah, bukan) —
  a long refinement sentence like "jadi sl di 14-16$ jangan di 19$ okey" no
  longer false-triggers an interrupt.
- **/stop UX.** The confirmation is now a single terse line
  (`❄️ Stopped — <title>`), the progress bubble is finalized rather than
  deleted (so the user keeps context and can reply "lanjut"), the
  stop-confirmation token is actually consumed (no double-send), and
  narration is suppressed once a turn is cancelled.
- **Telegram transport.** Honor `429 retry_after` with throttled progress
  edits, and bust the egress-proxy cache on `getUpdates` polling.
- **QR code tool.** The `qrcode[pil]` dependency is now declared, so the tool
  and its tests work out of the box.

### Upgrade note

No configuration changes are required. Existing installs can upgrade in place
with `zeline update`, or `/update` from Telegram.

### Security

Publishing to PyPI goes through Trusted Publishing (OIDC), so no API token is
stored in this repository. Provider API keys never appear in any response, and
the progress feed prints a URL's host only — never the full URL or a proxy's
credentials.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.3.6/docs/installation.md) for install commands on every supported platform, and the [changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.3.6/CHANGELOG.md) for the full list of changes.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
