# Muse reasoning engine (OPTIONAL example)

> **Zeline tetap 100% provider-agnostic.** Contoh ini murni opsional: cara
> memakai satu LLM OpenAI-compatible sebagai *reasoning backend* untuk
> Zeline. Tidak mengubah perilaku default Zeline, tidak mengganti provider
> bawaan, tidak memaksa siapa pun memakai Muse. Abaikan folder ini kalau
> tidak butuh.

Give any Zeline agent (or any agent speaking OpenAI tool_calls) a
**Muse-style reasoning backend** running on your own machine:

```
Zeline agent  ──►  9Router  ──►  bridge.py (localhost:8765, OpenAI-compatible)
                                            │  queue/pending/*.json
                                            ▼
                                   poller.py ──► your LLM API ──► queue/done/*.json
                                            │
                              {"content","tool_calls"} ──► agent executes tools natively
```

- **bridge.py** — tiny OpenAI-compatible HTTP server (`/v1/chat/completions`,
  `/v1/models`, `/health`, SSE streaming with keepalives). Forwards the
  poller's `tool_calls` with `finish_reason: "tool_calls"` so the agent runs
  its own tools step by step instead of getting one-shot answers.
- **poller.py** — the reasoning-engine loop (stdlib only). Polls the queue,
  drops stale requests, mechanically trims oversized histories, asks your LLM
  with `system-prompt.md`, writes answers back.
- **system-prompt.md** — the brain's instructions: JSON response contract,
  natural narration (no mechanical "Running ..." lines), picker-based
  confirmations, wallet/secret safety rules, large-file discipline.
  **Customize section 5 for your own projects.**

## Prerequisites

> **New here? Start with [SETUP.md](SETUP.md)** — step-by-step guide with two
> brain options (standalone poller, or your own Muse as the reasoning engine)
> plus a one-shot prompt you can paste to your Muse to set it all up.

- Python 3.9+
- [Zeline](https://github.com/Mftrferdinand/zeline) (`pip install zeline`)
- [9Router](https://github.com/) or any OpenAI-compatible router/proxy
- An LLM API key for the poller (OpenAI-compatible endpoint). The *wiring and
  instructions* ship in this repo; the model/API key is yours.

## Quickstart

```bash
# 1. start the bridge
python3 bridge.py &

# 2. configure the poller (use your own key — never commit it)
export MUSE_POLLER_API_KEY="sk-..."
export MUSE_POLLER_BASE_URL="http://localhost:20128/v1"  # your router or provider
export MUSE_POLLER_MODEL="your-model-id"

# 3. start the poller
python3 poller.py
```

Then wire it up:

1. **Router**: add an OpenAI-compatible provider named `Muse` pointing at
   `http://127.0.0.1:8765/v1` (in 9Router: dashboard → Providers → add
   OpenAI-compatible, base URL above). It exposes one model: `muse`.
2. **Zeline**: set the model to `muse/muse` (provider `Muse`, model `muse`).
3. Chat. The agent now reasons via your LLM and executes its native tools
   (`run_shell`, `read_file`, `web_search`, `ask_user`, …) step by step.

For a persistent install: `sudo ./install.sh` (installs to `/opt/muse-stack`
with systemd units; copy `poller.env.example` to `/etc/muse-stack/poller.env`
and fill in your key first).

## How a request flows

1. Agent POSTs `/v1/chat/completions` (with its `tools` list) → bridge writes
   `queue/pending/<id>.json` and waits (up to 240s; SSE keepalives in between).
2. `poller.py` picks it up, trims history if > 200KB, and calls your LLM:
   `system-prompt.md` + trimmed messages → **one JSON object**
   `{"content": "...", "tool_calls": [...]}`.
3. The answer lands in `queue/done/<id>.json`; the bridge forwards it to the
   agent (plain or SSE). `finish_reason` is `tool_calls` when there are calls,
   `stop` otherwise.
4. The agent executes the tools, sends results back as new requests, and the
   loop continues until the poller returns a final `content` answer.

Dashboard "Test Connection" probes (`{"content":"hi"}` with
`max_tokens: 1024`) are answered instantly without touching the queue.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `MUSE_BRIDGE_DIR` | `~/.muse-bridge/queue` | queue root |
| `MUSE_BRIDGE_HOST` / `MUSE_BRIDGE_PORT` | `127.0.0.1` / `8765` | bridge listen |
| `MUSE_BRIDGE_DIAG` | `0` | `1` → append `diag.log` |
| `MUSE_POLLER_BASE_URL` | `http://localhost:20128/v1` | LLM endpoint |
| `MUSE_POLLER_API_KEY` | (required) | LLM key |
| `MUSE_POLLER_MODEL` | `muse` | model id to request |
| `MUSE_POLLER_SYSTEM_PROMPT` | `./system-prompt.md` | prompt file |
| `MUSE_POLLER_INTERVAL` | `10` | poll seconds |
| `MUSE_POLLER_STALE_SECS` | `220` | drop older requests |
| `MUSE_POLLER_MAX_PER_CYCLE` | `5` | requests per cycle |
| `MUSE_POLLER_TRIM_BYTES` | `200000` | history trim threshold |
| `MUSE_POLLER_JSON_MODE` | `0` | `1` → `response_format: json_object` |
| `MUSE_POLLER_HTTP_TIMEOUT` | `150` | LLM call timeout (s) |

## What is Muse-specific vs portable

**Muse-specific (cannot be distributed):** the reasoning itself. On the
reference setup the poller is backed by a live Muse runtime — that "brain"
is not something you can zip. The bridge without a backend is just wiring.

**Portable (everything in this folder):**
- `bridge.py` v4 — OpenAI-compatible endpoint with `tool_calls`
  passthrough, so the reasoning backend can return tool calls and the agent
  (Zeline) executes its full native toolset itself, step by step — exactly
  like any other model-agnostic setup. No special-casing.
- `poller.py` — standalone reasoning engine; point it at **your own**
  OpenAI-compatible LLM (`MUSE_POLLER_API_KEY` / `BASE_URL` / `MODEL`).
- `system-prompt.md` — model-agnostic prompt: natural progress narration,
  `ask_user` picker for every question, wallet safety rules.
- `install.sh` — one-command install: bridge + poller + systemd units +
  watchdog timer (checks every 2 min; reinstalls + restarts the stack if
  units vanish or services die, and also watches 9Router / Zeline gateway
  units when present).

So on your own machine: install Zeline, install 9Router, run
`sudo ./install.sh`, set your LLM key, add the `Muse` provider in 9Router
pointing at `http://127.0.0.1:8765/v1` — done. Full tool use, picker
bubbles, auto-restart, all working with whatever model you choose.

## Troubleshooting

- `curl -s http://127.0.0.1:8765/health` → `{"ok": true}` means the bridge is up.
- Queue growing in `~/.muse-bridge/queue/pending/`? The poller isn't answering:
  check its logs (LLM key valid? endpoint reachable?).
- `504 "Muse did not answer in time"` → poller too slow or down; check
  `MUSE_POLLER_HTTP_TIMEOUT` and the LLM endpoint latency.
- Agent repeats safe/no-op steps → history probably bloated: lower
  `MUSE_POLLER_TRIM_BYTES` or raise the model context.

## Security notes

- The bridge listens on **localhost only** by default. Do not expose it
  publicly without authentication — anyone with access can make your LLM
  key spend money via the poller.
- Keep `MUSE_POLLER_API_KEY` in env / a `0600` env file, never in the repo.
- The system prompt's wallet rules are safety defaults; adapt section 3 to
  your own wallet tooling and never let tool calls sign or send transactions
  without explicit user confirmation.
