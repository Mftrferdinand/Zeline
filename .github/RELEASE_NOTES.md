## Zeline Release

Zeline is the open-source agentic AI framework by Zerolinear.

### Highlights

- **A first-party gateway for the mobile app — REST + SSE on `/api/v1`** — the
  Android/iOS client now talks to the same `zeline.agent.Zeline` the CLI and
  Telegram use, with no duplicated runtime. Token deltas stream as
  `assistant.delta`, tool activity arrives as its own `tool.started` /
  `tool.output` / `tool.completed` events so the client never parses assistant
  prose to learn what happened, and history replays those events as the same
  collapsible cards. Contract in `docs/ZELINE_APP_API.md`, every event field in
  `docs/SSE_EVENT_SCHEMA.md`, a working consumer in
  `examples/zeline_app_client.py`.
- **Stop actually stops** — cancellation is a flag checked inside the streaming
  read loop, so with streaming off there was no loop to check it and a stop
  request waited for the blocking provider call to return: **113–211 seconds**
  measured. Streaming is now a per-instance property, and the app gateway forces
  it on because for its protocol streaming is a requirement rather than a
  preference. Cancel now lands in **2.7 seconds**, and partial text is kept
  rather than discarded. The global `agent.stream` preference still governs the
  CLI exactly as before.
- **A cancelled turn no longer erases the work it already did** — the tool-call
  protocol requires every `tool_calls` entry to have a matching result, and
  Zeline used to satisfy that by amputation: drop the results, then drop the
  assistant message that asked for them. Stop after three of five calls had
  finished and those three real results went with it, along with the plan the
  model had written, so the next turn started blind and re-ran the same
  commands. Unanswered calls now get a result that says plainly the call did not
  complete — never a fabricated outcome, which the model would then reason from.
  The repair also scans the whole history instead of only the tail, so a dangling
  call in the middle (a parallel batch where one worker raised, a transcript from
  an older build, a crash between writing the call and its results) no longer
  leaves a session permanently rejected with nothing to do but wipe it.
- **Oversized tool output is offloaded to disk, not thrown away** — a large
  result used to be truncated into uselessness. It is now written to disk with a
  summary and a path the agent can read back in slices.
- **Trimming archives what it evicts and injects a digest** — context trimming
  silently deleted the oldest turns. Evicted turns are archived and a digest of
  them is injected, so the agent still knows what was decided fifty turns ago
  instead of confidently contradicting it.
- **`/version` and `/update` in Telegram** — a phone install can report its
  version and upgrade itself without ever opening a shell.
- **Discord gateway** — a bot over the official Gateway websocket and Bot API.
  Setup asks for a Bot Token; intents, heartbeat, and REST endpoints are
  transport details, not questions.
- **`openconnector` skill** — self-host OpenConnector and reach 1451 SaaS
  providers from Zeline over MCP.
- **One-line install** — the manual copy-paste checksum block is gone. It asked
  users to compare two hex strings by eye, which is not verification; artifacts
  are published with build provenance and the installer verifies checksums
  itself.

### Security

The app gateway binds `127.0.0.1` by default. Binding a routable interface
exposes an agent that can run shell commands to your network — put it behind a
TLS terminator with its own auth, and rotate the gateway token if it leaks.
Provider API keys never appear in any response (`/providers` returns a
`••••XXXX` hint only), every endpoint except `/health` and `/auth/login`
requires a Bearer JWT, and `/system` deliberately carries no IP address.
Session ids name files on disk, so they are validated at the storage boundary
and the resolved path is asserted to stay inside the data directory.

### Upgrade note

No configuration changes are required. `discord` and `zeline_app` now have
config defaults, so `zeline gateway enable discord` works instead of failing on
a name its own help text offered; both stay disabled until you enable them.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.2.7/docs/installation.md) for install commands on every supported platform.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
