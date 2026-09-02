<p align="center">
  <img src="assets/zerolinear-logo.png" alt="Zerolinear" width="760">
</p>

<p align="center">
  <a href="https://github.com/Mftrferdinand/Zeline/tree/main/docs"><img src="https://img.shields.io/badge/Docs-Documentation-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="https://t.me/zerolinear"><img src="https://img.shields.io/badge/Community-0A84FF?style=flat&labelColor=334155&logo=telegram&logoColor=white"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-EN-0A84FF?style=flat&labelColor=334155"></a>
  <a href="docs/README.id.md"><img src="https://img.shields.io/badge/Lang-ID-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="docs/README.zh.md"><img src="https://img.shields.io/badge/Lang-中文-0A84FF?style=flat&labelColor=334155"></a>
  <br>
  <strong>— by Zerolinear, an AI research lab.</strong>
</p>

---

# Zeline

Zeline is an open-source agentic AI framework developed by **Zerolinear**. It's a flexible foundation for building AI agents that can reason, use tools, interact with external systems, and carry out complex, multi-step workflows.

Rather than being tied to a single model, provider, or infrastructure, Zeline is built around flexibility. Connect your preferred AI models and OpenAI-compatible endpoints, configure providers, integrate tools, and extend the framework to fit how you want your agents to work — models and providers can be swapped without rebuilding the system, keeping the agent architecture portable and adaptable.

Run it locally for development or deploy it to your own server or cloud, and connect it to the interfaces you use. The goal is to keep control in the developer's hands: your models, your tools, your infrastructure, your data. Open-source, model-agnostic, extensible, and developer-first.

## Features

- **Agent core** — an OpenAI-compatible agent loop with tool calling, plus an interactive CLI and one-shot queries
- **Model-agnostic** — works with OpenAI, OpenRouter, vLLM, Ollama, and any OpenAI- or Anthropic-compatible API; swap model or provider without rebuilding
- **Persistent memory** — long-term memory isolated per platform identity
- **Session persistence** — conversation history stored in SQLite (`~/.zeline/sessions.db`), so it survives gateway restarts
- **Skills** — reusable Markdown procedures loaded on demand; see the [Zenith skill index](zeline/skills/ZENITH_INDEX.md) for the full bundled catalog
- **Messaging gateways** — Telegram (long polling, commands, attachments), WhatsApp (Baileys QR pairing), and an authenticated local HTTP webhook
- **Built-in tools** — web search, web fetch, deep research, HTTP requests, file read/write/edit/search, image analysis, text-to-image generation, code execution, shell, and sub-agent delegation
- **Lazy tool schemas** — the model is sent a small core of schemas plus a one-line summary of every other tool, and fetches full parameters on demand with `tool_search`. On the `full` profile that is 6,793 characters per request instead of 17,089 (60% less), without hiding any capability: names stay visible and a listed tool can be called directly. It engages only where the saving outweighs the extra round trip, so the public `safe` profile keeps sending everything; run `zeline toolsearch` for the numbers on your own tool set
- **Real browser control** — the `browser` tool drives an installed Chromium/Chrome over the Chrome DevTools Protocol (open, click, type, read, screenshot), so JavaScript-rendered and logged-in pages are reachable where raw HTML fetching is not. No Playwright or Puppeteer dependency
- **Scheduled jobs inside the gateway** — `zeline cron` runs interval or cron-expression jobs in the running gateway process, reusing its config, provider key, and workspace, and delivers results to the chat that owns the job
- **Language-server intelligence** — `code_intel` asks a real LSP server for diagnostics, definitions, references, hover, and symbols; servers are discovered on PATH and never downloaded, falling back to the project linter when none is installed
- **Operator extension points** — Python plugin hooks can audit, rewrite, or block any tool call before it runs, plain Python files can be loaded as `custom_*` tools, and local OpenAPI 3 documents become namespaced `api_*` tools without handwritten wrappers
- **Nothing is silently lost** — oversized tool output is written to disk and replaced by a pointer with a head/tail preview, and conversation turns evicted to keep the context window bounded are appended to an on-disk transcript plus a deterministic digest (what you asked, which files were touched, where the archive lives) injected at the front of history. Both are recoverable with `read_file`, and neither costs a model call
- **Undo** — writes and edits are snapshotted first; `zeline undo` lists and restores them
- **Human-in-the-loop** — `ask_user` pauses the run to ask you one question, with tappable options on messaging gateways and a keyboard prompt in the CLI
- **Format on write** — after the agent writes or edits a file, the project's installed formatter (ruff, gofmt, biome, prettier, rustfmt, shfmt, …) runs on it, so generated code matches your repo style; configurable per extension and never overwrites a failed write
- **Sub-agents** — delegate a focused subtask to an isolated child agent that returns only its final summary, keeping the main context clean (depth-limited; owner profiles only)
- **MCP client** — connect external MCP servers (stdio or HTTP) and expose their tools automatically
- **Scoped tool profiles** — gate access per surface:
  - `safe` — memory and public skills only; default for messaging gateways
  - `workspace` — `safe` plus files inside the owner workspace
  - `full` — `workspace` plus shell access; intended for the local owner CLI

## Install

**Requirements:** Python 3.10+. WhatsApp also needs Node.js 18+ and npm. POSIX
platforms use a private Python environment; Windows uses a per-user package
install. Neither requires root/Administrator access.

### From PyPI

If you already have Python tooling, this is the shortest path on every platform:

```bash
uv tool install zeline
```

Or with pip, into an environment you manage: `pip install zeline`. Then `zeline setup`.

### Termux, Linux, macOS, and iSH

The installer route needs no existing Python tooling — it provisions a private
environment for you:

```bash
curl -fsSLO --proto '=https' --tlsv1.2 https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.sh && bash install.sh
```

Then `zeline setup`. The installer downloads the versioned wheel and verifies it
against `SHA256SUMS` itself before installing, so there is nothing to check by
hand. On iSH, run `apk add bash curl python3` first.

### Windows PowerShell

```powershell
iwr -UseBasicParsing https://github.com/Mftrferdinand/Zeline/releases/download/v0.2.8/install.ps1 -OutFile install.ps1; .\install.ps1
```

Then `zeline setup`.

### Verify the download independently (optional)

The installer's own wheel check comes from the same release as the installer, so
it proves integrity, not origin. For that, verify the release's build-provenance
attestation with GitHub CLI — an independent signature chain:

```bash
gh attestation verify install.sh --repo Mftrferdinand/Zeline
```

See the complete [installation guide](docs/installation.md) for package
prerequisites, platform limitations, checkout installs, updates, PATH fixes, and
uninstall instructions.

### Update

One command on every platform — Termux, Linux, macOS, iSH, and Windows
PowerShell:

```bash
zeline update
```

It fetches the latest release installer, verifies its SHA-256 before running it,
and updates in place. Your config, sessions, memory, and private skills under
`~/.zeline` are untouched. From a git checkout it rebuilds your local source
instead.

Check what you are on, and whether a release is waiting:

```bash
zeline version
```

Both are also available from Telegram, so a phone-only install never needs a
shell: **`/version`** reports the installed build against the latest release, and
**`/update`** performs the update. `/update` is owner-only and runs in a detached
process — the gateway finishes in-flight work, stops, installs, and relaunches
itself, with progress posted back to the chat. It refuses to run from a source
checkout, where installing an uncommitted working tree would be a surprise.

Then inspect the available integrations and health:

```bash
zeline tools list
zeline mcp list
zeline doctor
zeline gateway list
```

## Use the CLI

```bash
zeline
# or
zeline chat -q "What can you do?"
```

## Connect a platform

### Telegram

Create a bot with [@BotFather](https://t.me/BotFather), then run:

```bash
zeline gateway setup telegram
zeline gateway start
```

An empty allowlist makes the bot public. Public gateways always use the `safe` tool profile by default, so users cannot access host files or a shell.

Telegram commands:

```text
/start, /help             Show command help
/status                   Show provider and session status
/models                   Show the active model
/model <provider/model>   Persistently switch this installation's model
/new                      Clear the current chat history
/restart                  Restart the current Telegram chat session
/stop                     Stop this Zeline gateway process
/logs                     Show how to inspect gateway logs from the installation terminal
```

Attachments up to 256 KB are accepted for text, JSON, CSV, common code/config files, and ZIP archives containing safe text files. Text-based PDFs are extracted with `pypdf`. Images are accepted as attachment metadata; pixel analysis needs a vision-capable provider.

### WhatsApp

```bash
zeline gateway setup whatsapp
zeline gateway start
```

On first start, Zeline installs its Baileys bridge under `~/.zeline/wa-bridge/` and prints a QR code. In WhatsApp, open **Linked devices**, choose **Link a device**, then scan it.

> This gateway uses WhatsApp multi-device through Baileys, not the Meta Business API. Make sure your use complies with WhatsApp policies.

### HTTP webhook

```bash
zeline gateway enable webhook
zeline gateway start
```

The default listener is `127.0.0.1:8765`. It does not listen on the public internet.

```bash
curl http://127.0.0.1:8765/health

curl -X POST http://127.0.0.1:8765/message \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_WEBHOOK_TOKEN' \
  -d '{"chat_id":"demo-user","text":"Hello Zeline"}'
```

Show masked configuration with:

```bash
zeline config show
```

If you expose the webhook through a tunnel or reverse proxy, use HTTPS and keep token authentication enabled.

## Command reference

```text
zeline                         First run: gateway onboarding; later: local chat
zeline chat -q "..."           Send one query after gateway + model setup
zeline setup                   First run: gateway picker; later: setup center
zeline setup <section>         Configure gateway|model|tools|integrations|agent
zeline model                   Detect protocol, fetch models, and choose one
zeline tools list              List native tools, profiles, and enabled state
zeline tools profile <name>    Set safe|workspace|full for the local CLI
zeline tools enable|disable T  Toggle one native tool for new sessions
zeline tools workspace <path>  Set the owner workspace root
zeline tools openapi-add FILE  Install a local OpenAPI 3 YAML/JSON document
zeline tools openapi           List parsed API operations and credential variable names
zeline doctor                  Check dependencies and configuration
zeline config path             Print the configuration location
zeline config show             Print configuration with masked secrets
zeline gateway setup [name]    Configure telegram, whatsapp, or webhook
zeline gateway enable <name>   Enable a gateway
zeline gateway disable <name>  Disable a gateway
zeline gateway list            Show configured gateways
zeline gateway token webhook   Explicitly reveal a webhook token
zeline gateway start           Run enabled gateways in the background
zeline gateway stop            Stop the background gateway process
zeline gateway status          Show background gateway status
zeline gateway log             Print gateway logs
zeline gateway run             Run enabled gateways in the foreground
zeline skills                  List installed skills (catalog: zeline/skills/ZENITH_INDEX.md)
zeline memory                  Print local CLI memory
```

On first launch, Zeline requires one gateway selected from an arrow-key picker:
Telegram, WhatsApp, Webhook, or Cancel. It configures only the selected gateway,
returns to the terminal, and directs the user to `zeline model`; local chat stays
locked until both gateway and model setup are complete.

During model setup, Zeline detects OpenAI-compatible or Anthropic APIs,
queries the provider model endpoint, and shows a numbered picker. Secret input
renders one `*` per character while the real API key stays hidden. If a provider
cannot list models, Zeline requires an explicit model ID instead of accepting a
placeholder.

Zeline can safely describe its active model, provider URL, protocol, tool profile,
and available tools through `runtime_info` and the bundled `self-analysis` skill.
API keys and gateway tokens are never included.

## Security

- Keep `~/.zeline/`, `.env`, provider keys, and bot tokens out of Git.
- OpenAPI credentials are read only from `~/.zeline/.env` (`ZELINE_OPENAPI_<FILE>_<SCHEME>`), never exposed in model schemas, and loaded only for `workspace`/`full` profiles.
- Gateway users receive the `safe` profile by default.
- Webhooks require a secret token and bind to loopback by default.
- Memory is namespaced by platform identity, for example `telegram:123` or `webhook:alice`.
- The WhatsApp bridge uses a random runtime token between Python and Node.
- The repository enables secret scanning, push protection, Dependabot, CodeQL, and dependency review.

See [SECURITY.md](SECURITY.md) for reporting guidance.

## Extend it

Custom Python tools, plugin hooks that can audit or block any tool call, local
OpenAPI documents as tools, and MCP servers all work without changing the
package. See [docs/extending.md](docs/extending.md).

## Development

```bash
python3 -m unittest discover -s tests -v
ruff check zeline tests
python3 -m pip wheel --no-deps --wheel-dir dist .
```

CI runs `unittest`, not `pytest`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
branch/PR flow, what each CI job proves, and where things live;
[CHANGELOG.md](CHANGELOG.md) records every release with links to its pull
requests.

## Roadmap

- Service integration for systemd and Termux:Boot
- More messaging adapters
- Richer interfaces on top of the app gateway
- Promoting more ruff rules into the blocking lint gate

## License

MIT © 2026 Mftrferdinand
