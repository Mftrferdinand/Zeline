## Zeline v0.3.5

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
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.3.5/install.sh && bash install.sh
```

The installer downloads the versioned wheel and verifies it against
`SHA256SUMS` itself, so there is nothing to check by hand.

Every release carries the whole bundled surface — this one ships **269 skills**
and **34 tools** — and the release workflow diffs the built wheel against the
source tree and fails rather than publishing an install that is missing any of it.

### Bug fixes

- **Web search no longer dies silently.** The `r.jina.ai` reader proxy — the
  server-side renderer Zeline uses to reach Bing and DuckDuckGo from mobile /
  Termux networks — started rejecting browser `User-Agent` strings with HTTP 403.
  Because both primary search engines routed through it, every general query
  quietly collapsed to only Google News RSS + Wikipedia, and users saw "web
  search failed on all sources". The reader now uses a lightweight bot UA that
  the proxy accepts. Verified end to end: representative queries went from 0
  results to 7 / 9 / 6 results.
- **Telegram flood-ban handling.** The gateway now honors Telegram's `429
  retry_after` and throttles progress-bubble edits (minimum interval between
  edits) so a long task with rapid tool calls no longer trips a flood ban and
  stops updating.
- **Reply-to context is understood.** When a user replies to a specific message
  (e.g. "continue this" while quoting an older bubble), the quoted text is now
  injected as `[Replying to: "..."]` so the model resolves *which* message
  "this" refers to instead of guessing the last task. Three cases are
  distinguished: replying to the bot, to the user's own earlier message, or to
  someone else.
- **Self-identity is locked in.** Zeline consistently knows it is Zeline (an
  agentic AI framework) by Zerolinear, and its configurable chat name. It no
  longer answers "I don't know what Zeline / Zerolinear is" when asked about
  itself or its origin.
- **`sessions.progress()` guarded for stub sessions** so the test suite's
  lightweight session stubs don't crash the mid-turn path.

### Highlights

- **Mid-turn steering, no more accidental cancels.** A message sent while a turn is running is
  now injected into the running turn (it arrives after the next tool call) rather
  than aborting the task. The turn keeps running undisturbed; the user cancels
  explicitly with `/stop`. This replaces the old keyword heuristic that would
  interrupt and kill a task on words like "don't" or "change" — which repeatedly
  stopped work mid-flight during a simple correction. Busy-acks are terse and in
  English and debounced to at most one per 30 s.
- **New `/steer <prompt>` command.** Steer the running task explicitly from the
  command menu. When no turn is running it behaves like a normal message.
- **Held-task memory across interruptions.** A task parked by an urgent message
  is remembered so Zeline can offer to resume it once the interrupting message is
  handled.
- **Progress labels rewritten clean.** Tool progress bubbles now read like short
  action phrases — `Reading`, `Writing`, `Editing`, `Running code`, `Searching
  files for …`, `Searching the web for …` — with trailing ellipses and filler
  words removed. Emoji icons are unchanged.

### New skills

- **voice-reply** — anime-female visual-novel style voice replies (Indonesian).
- **file-converter** — convert between common file formats.
- **video-downloader** — download video from supported sources.
- **airdrop-manager** — analyze and classify crypto airdrop links.

### Upgrade note

No configuration changes are required. Existing installs can upgrade in place
with `zeline update`, or `/update` from Telegram.

### Security

Publishing to PyPI goes through Trusted Publishing (OIDC), so no API token is
stored in this repository. Provider API keys never appear in any response, and
the progress feed prints a URL's host only — never the full URL or a proxy's
credentials.

### Installation

See the [installation guide](https://github.com/Mftrferdinand/Zeline/blob/v0.3.5/docs/installation.md) for install commands on every supported platform, and the [changelog](https://github.com/Mftrferdinand/Zeline/blob/v0.3.5/CHANGELOG.md) for the full list of changes.

### Assets

- POSIX installer: `install.sh`
- Windows installer: `install.ps1`
- Python wheel and source archive
- `SHA256SUMS`

All assets are built from merged `main`, checksum-verified, and published with build provenance.
