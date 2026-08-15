<p align="center">
  <img src="assets/zerolinear-logo.png" alt="Zerolinear" width="760">
</p>

<p align="center">
  <a href="https://zeline.zerolinear.com"><img src="https://img.shields.io/badge/Docs-zeline.zerolinear.com-1D4ED8?style=flat&labelColor=334155"></a>
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

Zeline is an open-source agentic AI framework developed by [Zerolinear](https://zerolinear.com). It's a flexible foundation for building AI agents that can reason, use tools, interact with external systems, and carry out complex, multi-step workflows.

Rather than being tied to a single model, provider, or infrastructure, Zeline is built around flexibility. Connect your preferred AI models and OpenAI-compatible endpoints, configure providers, integrate tools, and extend the framework to fit how you want your agents to work — models and providers can be swapped without rebuilding the system, keeping the agent architecture portable and adaptable.

Run it locally for development or deploy it to your own server or cloud, and connect it to the interfaces you use. The goal is to keep control in the developer's hands: your models, your tools, your infrastructure, your data. Open-source, model-agnostic, extensible, and developer-first.

## Features

- **Agent core** — an OpenAI-compatible agent loop with tool calling, plus an interactive CLI and one-shot queries
- **Model-agnostic** — works with OpenAI, OpenRouter, vLLM, Ollama, and any OpenAI- or Anthropic-compatible API; swap model or provider without rebuilding
- **Persistent memory** — long-term memory isolated per platform identity
- **Session persistence** — conversation history stored in SQLite (`~/.zeline/sessions.db`), so it survives gateway restarts
- **Skills** — reusable Markdown procedures loaded on demand
- **Messaging gateways** — Telegram (long polling, commands, attachments), WhatsApp (Baileys QR pairing), and an authenticated local HTTP webhook
- **Built-in tools** — web search, web fetch, deep research, HTTP requests, file read/write/edit/search, image analysis, text-to-image generation, code execution, shell, and sub-agent delegation
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

### Termux, Linux, and macOS

```bash
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.2
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(line.split()[0] for line in lines if line.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
export PATH="$HOME/.local/bin:$PATH"  # harmless on Termux; needed on some Linux/macOS shells
zeline setup
```

### iOS / iPadOS through iSH

```sh
apk add bash curl python3 py3-pip
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.2
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(line.split()[0] for line in lines if line.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
zeline setup
```

### Windows PowerShell

```powershell
$base = 'https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.2'
Invoke-WebRequest "$base/install.ps1" -OutFile install.ps1
Invoke-WebRequest "$base/SHA256SUMS" -OutFile SHA256SUMS
$expected = ((Get-Content SHA256SUMS | Where-Object { $_ -match ' install.ps1$' }) -split '\s+')[0]
if (-not $expected -or $expected -notmatch '^[0-9a-f]{64}$') { throw 'invalid install.ps1 checksum entry' }
if ((Get-FileHash install.ps1 -Algorithm SHA256).Hash.ToLower() -ne $expected.ToLower()) { throw 'checksum mismatch' }
.\install.ps1
zeline setup
```

See the complete [installation guide](docs/installation.md) for package
prerequisites, platform limitations, checkout installs, updates, PATH fixes, and
uninstall instructions.

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
zeline skills                  List installed skills
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
- Gateway users receive the `safe` profile by default.
- Webhooks require a secret token and bind to loopback by default.
- Memory is namespaced by platform identity, for example `telegram:123` or `webhook:alice`.
- The WhatsApp bridge uses a random runtime token between Python and Node.
- The repository enables secret scanning, push protection, Dependabot, CodeQL, and dependency review.

See [SECURITY.md](SECURITY.md) for reporting guidance.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --wheel-dir dist .
```

## Roadmap

- PyPI publishing and signed release artifacts
- Service integration for systemd and Termux:Boot
- More messaging adapters
- Scheduled jobs
- Plugin and extension APIs
- Session search and richer interfaces

## License

MIT © 2026 Mftrferdinand
