---
name: mem0-memory-mcp
description: |
  Connect mem0 (hosted long-term memory) to a Hermes agent or to Zeline
  as an MCP server, so the agent has semantic long-term memory beyond the
  built-in char-limited memory. Covers minting an agent API key with the
  mem0 CLI (no email/dashboard needed), the hosted MCP endpoint + auth,
  wiring it into Hermes (`hermes mcp add`) and Zeline (`~/.zeline/
  config.json`), and the add/search/get tool surface. Load when the user
  asks to install mem0, add long-term memory, or hit the memory char limit.
metadata:
  hermes:
    tags: [mem0, mcp, memory, long-term-memory, zeline, hermes]
    category: automation
---

# mem0 long-term memory via MCP

mem0 is a hosted long-term memory service. Connected over MCP it gives an
agent semantic, effectively-unlimited memory — a second layer beyond
Hermes's built-in (char-capped, injected-every-turn) memory. Same mem0
account/key works for BOTH Hermes and Zeline (MCP is a shared standard).

## 1. Mint an API key (agent mode — no email/dashboard)

```bash
pip install mem0-cli
mem0 init --agent --agent-caller <your-agent-name> --json
```

This mints an **unclaimed** key in seconds and writes it to
`~/.mem0/config.json`:
- `platform.api_key` → `m0-...`
- `defaults.user_id` → `user_xxxx` (the default memory bucket)
- `base_url` → `https://api.mem0.ai`

**IMPORTANT — tell the user to claim it** (one-time, preserves memories):
```bash
mem0 init --email <their-email>                 # sends a 6-digit code to that email
mem0 init --email <their-email> --code <code>   # completes the claim
```
Until claimed, every API response carries an "unclaimed account" notice.
On success: "Agent claimed to <email>. Your API key is unchanged." After
claiming, the user logs into the dashboard at app.mem0.ai with that email
(Google/OTP) — same key, memories preserved.

Claim-code pitfalls (verified live):
- **Non-interactive terminal**: `mem0 init --email X` alone sends the
  code but then errors `No --code provided and terminal is
  non-interactive`. Expected — the email still went out. Finish with a
  second call `--code <code>`.
- **Each resend invalidates the previous code.** Calling
  `mem0 init --email X` multiple times means only the LAST code works;
  older ones fail `Claim failed: Invalid code`. Ask the user for the
  NEWEST email and avoid triggering extra resends between ask & receive.
- The code lands in a REAL user inbox (Gmail), NOT a temp-mail you can
  auto-read — ask the user for it. (Only temp-mail OTPs are agent-
  readable; see temp-email-automation.)

## 2. Hosted MCP endpoint

- URL: `https://mcp.mem0.ai/mcp/`  (note the trailing slash; `/mcp`
  307-redirects to `/mcp/`)
- Transport: **http** (JSON-RPC over SSE — responses come as
  `event: message\ndata: {...}`)
- Auth: header `Authorization: Bearer <api_key>` (a raw `Token <key>`
  also works on the Platform REST API).
- 11 tools exposed: `add_memory`, `search_memories`, `get_memories`,
  `get_memory`, `update_memory`, `delete_memory`, `delete_all_memories`,
  `delete_entities`, `list_entities`, `list_events`, `get_event_status`.

Quick verify the endpoint is live + key valid:
```bash
curl -s -X POST https://mcp.mem0.ai/mcp/ \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"x","version":"1"}}}'
# expect: event: message  data: {... "serverInfo":{"name":"mem0",...}}
```

## 3a. Wire into Hermes (this agent)

```bash
# interactive prompts: require auth? Y → paste key → enable all tools
printf 'Y\n%s\ny\n' "$KEY" | hermes mcp add mem0 \
  --url "https://mcp.mem0.ai/mcp/" --auth header --connect-timeout 25
hermes mcp list          # shows mem0 ✓ enabled
hermes mcp test mem0     # lists the 11 tools
```
Saved to `~/.hermes/config.yaml`. **Start a NEW session** for the tools
to load (they don't hot-load mid-session).

## 3b. Wire into Zeline (the framework)

Zeline reads MCP servers from `~/.zeline/config.json` → `mcp.servers`.
Add:
```json
{
  "mcp": {
    "servers": {
      "mem0": {
        "transport": "http",
        "url": "https://mcp.mem0.ai/mcp/",
        "headers": { "Authorization": "Bearer m0-XXXXXXXX" },
        "enabled": true
      }
    }
  }
}
```
Zeline's `MCPRegistry.from_config` picks up `transport`, `url`,
`headers`, `env`. Tools appear as `mem0.add_memory`, `mem0.search_memories`, etc.

## 4. Platform REST API (fallback, no MCP)

Same key works directly:
```bash
# add
curl -s -X POST https://api.mem0.ai/v1/memories/ \
  -H "Authorization: Token $KEY" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"..."}],"user_id":"user_xxxx"}'
# search
curl -s -X POST https://api.mem0.ai/v2/memories/search/ \
  -H "Authorization: Token $KEY" -H "Content-Type: application/json" \
  -d '{"query":"...","filters":{"user_id":"user_xxxx"}}'
```
Adds are **async** — the response is `{"status":"PENDING","event_id":...}`
and the memory takes a bit to appear in search/get (mem0 distills the
message first). Don't expect instant read-after-write; poll `list_events`
or `get_event_status`, or just wait.

## Hermes memory vs mem0 — when to use which
- **Hermes built-in memory** (`memory` tool): tiny, injected every turn,
  best for the handful of always-relevant facts (name, tone, hard rules).
  Char-capped (~2.2k) — keep it lean.
- **mem0**: large, semantic, fetched on demand via `search_memories`.
  Best for the long tail — project details, past decisions, account maps,
  anything you'd otherwise overflow the built-in store with.
  Pattern: keep a 1-line pointer in built-in memory ("detail X ada di
  mem0"), push the bulk to mem0.

## Pitfalls
- Trailing slash on `/mcp/` matters (else 307).
- MCP tools only load on a fresh session after `hermes mcp add`.
- `UID` is read-only in bash — use another var name in test scripts.
- Agent-mode key is unclaimed until the user runs `mem0 init --email`;
  surface that to the user.
- Never commit the `m0-...` key to a repo; keep it in config only.
