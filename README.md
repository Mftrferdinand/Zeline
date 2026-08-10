<p align="center">
  <img src="assets/zeline-logo.png" alt="Zeline AI" width="760">
</p>

# Zerolinear

**Zerolinear** is an AI research lab based in Majalengka, Indonesia, building intelligent agents that learn, remember, and act.

This repository contains **Zeline** — an open-source agentic AI framework by Zerolinear, created by Mftrferdinand.

## What it includes

- An OpenAI-compatible agent loop with tool calling
- Provider support for OpenAI, OpenRouter, vLLM, Ollama, and compatible APIs
- Persistent memory isolated by platform identity
- Markdown skills loaded on demand
- Interactive CLI and one-shot queries
- Telegram Bot API long polling, bot commands, and attachment intake
- WhatsApp pairing through Baileys and a QR code
- An authenticated local HTTP webhook
- Scoped tool profiles:
  - `safe`: memory and public skill access only; default for messaging gateways
  - `workspace`: `safe` plus files inside the owner workspace
  - `full`: `workspace` plus shell access; intended for the local owner CLI

## Install

**Requirements:** Python 3.10 or newer. WhatsApp also requires Node.js 18+ and npm.

### Termux

```bash
pkg install git python -y
curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/Zerolinear/main/install.sh | bash
zeline setup
```

### Linux and macOS

```bash
curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/Zerolinear/main/install.sh | bash
zeline setup
```

To install from a checkout instead:

```bash
git clone https://github.com/Mftrferdinand/Zerolinear.git
cd Zerolinear
bash install.sh
```

Your configuration is stored locally at `~/.zeline/config.json`. Run a quick check after setup:

```bash
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
zeline                         Set up a gateway first, then open local chat
zeline chat -q "..."           Send one query after gateway + model setup
zeline setup                   Open the gateway picker (Telegram/WhatsApp/Webhook)
zeline model                   Detect protocol, fetch models, and choose one
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
