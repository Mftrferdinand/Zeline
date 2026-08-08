<p align="center">
  <img src="assets/aesora-logo.png" alt="Aesora Agent" width="760">
</p>

# Aesora

**Aesora** is a complete, lightweight, self-hosted AI agent framework for Python by Mftrferdinand.

## What it includes

- An OpenAI-compatible agent loop with tool calling
- Provider support for OpenAI, OpenRouter, vLLM, Ollama, and compatible APIs
- Persistent memory isolated by platform identity
- Markdown skills loaded on demand
- Interactive CLI and one-shot queries
- Telegram Bot API long polling
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
curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/aesora/main/install.sh | bash
aesora setup
```

### Linux and macOS

```bash
curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/aesora/main/install.sh | bash
aesora setup
```

To install from a checkout instead:

```bash
git clone https://github.com/Mftrferdinand/aesora.git
cd aesora
bash install.sh
```

Your configuration is stored locally at `~/.aesora/config.json`. Run a quick check after setup:

```bash
aesora doctor
aesora gateway list
```

## Use the CLI

```bash
aesora
# or
aesora chat -q "What can you do?"
```

## Connect a platform

### Telegram

Create a bot with [@BotFather](https://t.me/BotFather), then run:

```bash
aesora gateway setup telegram
aesora gateway start
```

An empty allowlist makes the bot public. Public gateways always use the `safe` tool profile by default, so users cannot access host files or a shell.

### WhatsApp

```bash
aesora gateway setup whatsapp
aesora gateway start
```

On first start, Aesora installs its Baileys bridge under `~/.aesora/wa-bridge/` and prints a QR code. In WhatsApp, open **Linked devices**, choose **Link a device**, then scan it.

> This gateway uses WhatsApp multi-device through Baileys, not the Meta Business API. Make sure your use complies with WhatsApp policies.

### HTTP webhook

```bash
aesora gateway enable webhook
aesora gateway start
```

The default listener is `127.0.0.1:8765`. It does not listen on the public internet.

```bash
curl http://127.0.0.1:8765/health

curl -X POST http://127.0.0.1:8765/message \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_WEBHOOK_TOKEN' \
  -d '{"chat_id":"demo-user","text":"Hello Aesora"}'
```

Show masked configuration with:

```bash
aesora config show
```

If you expose the webhook through a tunnel or reverse proxy, use HTTPS and keep token authentication enabled.

## Command reference

```text
aesora                         Start the local chat
aesora chat -q "..."           Send one query
aesora setup                   Configure the provider and gateways
aesora doctor                  Check dependencies and configuration
aesora config path             Print the configuration location
aesora config show             Print configuration with masked secrets
aesora gateway setup [name]    Configure telegram, whatsapp, or webhook
aesora gateway enable <name>   Enable a gateway
aesora gateway disable <name>  Disable a gateway
aesora gateway list            Show configured gateways
aesora gateway token webhook   Explicitly reveal a webhook token
aesora gateway start           Run enabled gateways in the background
aesora gateway stop            Stop the background gateway process
aesora gateway status          Show background gateway status
aesora gateway log             Print gateway logs
aesora gateway run             Run enabled gateways in the foreground
aesora skills                  List installed skills
aesora memory                  Print local CLI memory
```

## Security

- Keep `~/.aesora/`, `.env`, provider keys, and bot tokens out of Git.
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
