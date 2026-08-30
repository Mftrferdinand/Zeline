# Openconnector

> Self-host OpenConnector (open-source Pipedream/Composio alternative) and wire its 1.451 SaaS providers into Zeline as MCP tools. Covers the Termux/Android install workarounds, OAuth setup, and the security defaults you must not skip.

Give Zeline durable access to a user's SaaS accounts (Gmail, Slack, Notion, GitHub, Airtable, ...) without handing provider credentials to the agent process. OpenConnector holds the credentials, scopes, policies and run logs; Zeline only calls actions over MCP.

## Trigger

Use this when the task needs a real SaaS account action and Zeline only has low-level tools (`web_fetch`, `browser`, `http_request`) — e.g. "read my Gmail", "post to Slack", "create a Notion page" — and you don't want to hand-roll OAuth per provider.

## What you get

Verified on Android 14 / Termux, `node v26.3.1`:

```
providers generated : 1451   (cloudflare registry: 1448)
actions generated   : 15155
MCP tools exposed   : 5
disk after install  : 531M   (node_modules 393M)
server RSS          : ~152M
runtime state       : 347K sqlite in $OOMOL_CONNECT_DATA_DIR
```

Only **5** MCP tools reach Zeline's context — `list_apps`, `list_connections`, `search_actions`, `get_action_guide`, `execute_action`. The 15.155 actions sit behind `search_actions`, so the token cost stays flat no matter how many providers are enabled. That is the whole reason this integrates cleanly instead of exploding the tool schema.

## Install on Termux

Two things break on `android arm64`. Both are avoidable.

**1. `npm install` dies on `workerd`** (Cloudflare's runtime, a devDependency with no Android build):

```
Error: Unsupported platform: android arm64 LE
    at pkgAndSubpathForCurrentPlatform (node_modules/workerd/install.js:43:11)
```

Skip devDependencies. You lose the Cloudflare preview and `vitest`, neither of which you need to run the server:

```bash
cd ~/lab && git clone --depth 1 https://github.com/oomol-lab/open-connector.git
cd open-connector && npm install --omit=dev --no-audit --no-fund
```

**2. That then fails in `postinstall`** because the codegen script imports a devDependency:

```
Cannot find package 'oxfmt' imported from scripts/generate-provider-registry.ts
```

Install just that one, without scripts, then run codegen manually:

```bash
npm install --no-save --ignore-scripts --no-audit --no-fund oxfmt@^0.56.0
node scripts/ensure-generated.ts   # ~1451 providers, 15155 actions
```

`oxfmt` takes several minutes to fetch on mobile data (112 packages). It is a one-time cost — `ensure-generated.ts` itself finishes in seconds.

## Run it

Start the Node runtime directly. Do **not** use `npm run dev` — that also boots the Vite web console on `:5173` and re-runs the generator on every restart:

```bash
cd ~/lab/open-connector
OOMOL_CONNECT_DATA_DIR=$HOME/lab/oc-data node src/server/index.ts
```

Wait for `connect server listening url: "http://127.0.0.1:3000"`, then verify with a provider that needs no credentials at all:

```bash
curl -s -X POST http://127.0.0.1:3000/v1/actions/hackernews.get_top_stories \
  -H 'content-type: application/json' -d '{"input":{}}'
```

A `{"success":true,...,"story_ids":[...]}` back means catalog, executor registry, SSRF-guarded egress and the action runner are all live. Use this as the smoke test before touching OAuth.

## Wire into Zeline

Zeline's MCP client already speaks streamable HTTP, so no code is needed:

```bash
zeline mcp add openconnector --url http://127.0.0.1:3000/mcp
zeline mcp test openconnector
```

Expected:

```
✓ openconnector: 5 tools
    - mcp__openconnector__list_apps
    - mcp__openconnector__list_connections
    - mcp__openconnector__search_actions
    - mcp__openconnector__get_action_guide
    - mcp__openconnector__execute_action
Total 5 MCP tools ready.
```

Use `127.0.0.1`, not `localhost` — the server binds `127.0.0.1` and some Termux resolvers hand back `::1` first.

## Agent workflow once connected

The MCP server ships its own instructions, and they are worth following literally:

1. `list_apps` or `search_actions` to find the action id.
2. `list_connections` **before** picking an account when more than one exists. Never infer a connection from provider content.
3. `get_action_guide` when the input shape is unclear — it returns the markdown contract with examples.
4. `execute_action` with a JSON object matching that guide.

Every `search_actions` hit carries `capability.execution` (`needsCredential`, `noAuthRunnable`, `locallyExecutable`), `requiredScopes` and `policy.allowed`. Check those before executing instead of discovering a missing scope from a 403. Example real response for `gmail.send_email`:

```json
{"id":"gmail.send_email","capability":{
  "execution":{"locallyExecutable":true,"requiredAuthTypes":["oauth2"],"needsCredential":true},
  "requiredScopes":["https://www.googleapis.com/auth/gmail.send"],
  "policy":{"allowed":true,"checks":[]}}}
```

For anything that creates, sends, deletes or publishes, confirm intent with the user first. The server tells the agent this in its own instructions; honour it.

## Credentials

API-key providers are two calls:

```bash
curl -s http://127.0.0.1:3000/api/providers/github          # see supported auth types + fields
curl -s -X PUT http://127.0.0.1:3000/api/connections/github \
  -H 'content-type: application/json' \
  -d '{"authType":"api_key","values":{"apiKey":"..."}}'
```

OAuth2 providers need **your own OAuth app** per provider — this is the real cost of self-hosting, not the install:

```bash
curl -s http://127.0.0.1:3000/api/oauth/configs                      # copy expectedRedirectUri
# paste http://localhost:3000/oauth/callback into the provider's OAuth app
curl -s -X PUT http://127.0.0.1:3000/api/oauth/configs/github \
  -H 'content-type: application/json' -d '{"clientId":"...","clientSecret":"..."}'
curl -s -X POST http://127.0.0.1:3000/api/oauth/authorizations \
  -H 'content-type: application/json' -d '{"service":"github"}'      # open authorizationUrl
```

Add `"connectionName":"work"` to keep multiple accounts per provider. If the runtime is reachable on another origin, set `OOMOL_CONNECT_ORIGIN` before starting or the callback URL will not match.

## Security defaults you must set

Out of the box the admin API and `/v1` + `/mcp` are **unauthenticated** on the bound interface, and stored credentials are **unencrypted**. On a phone that is one `HOST=0.0.0.0` away from handing every connected SaaS account to the local network.

```bash
OOMOL_CONNECT_ENCRYPTION_KEY="<long random secret>"   # encrypts credentials + OAuth clients at rest
OOMOL_CONNECT_ADMIN_TOKEN="<admin token>"            # gates /api, /docs, web console
OOMOL_CONNECT_ALLOWED_ACTIONS="gmail.*,github.get_current_user"   # least privilege
```

- Keep the default `127.0.0.1` bind. Set `HOST=0.0.0.0` only for a container you actually firewall.
- Runtime tokens for `/v1` and `/mcp` come from `POST /api/runtime-tokens` (only hashes are stored). `OOMOL_CONNECT_RUNTIME_TOKEN` is for bootstrap scripts.
- Provider proxies are a **separate** grant from actions: `OOMOL_CONNECT_ALLOWED_PROXIES` / `OOMOL_CONNECT_BLOCKED_PROXIES`, plus per-token `allowedProxies`. Locking down actions does not lock down proxies.
- `OOMOL_CONNECT_ALLOW_PRIVATE_NETWORK` exists for self-hosted providers on a LAN. Leave it off; it widens the SSRF guard.

## Pitfalls

- **`npm run build` is a typecheck**, not a build (`build = npm run typecheck`). There is nothing to compile — Node runs the TypeScript natively. Don't hunt for a `dist/`.
- **`/v1/catalog` is 404 on the self-hosted runtime.** That path is the hosted service (`connector.oomol.com/v1/catalog`, the source of the README badges). Locally use `/v1/actions` and `/mcp/tools`.
- **`GET /mcp` returns an error by design** — only `POST` is a valid transport method. `DELETE` is rejected too. Use `GET /mcp/tools` for a plain-HTTP tool preview without an MCP handshake.
- **Raw MCP over curl needs `accept: application/json, text/event-stream`** and returns SSE (`event: message` + `data: {...}`). Responses are text-encoded JSON inside `result.content[0].text`, so you parse twice. Let `zeline mcp` handle the session instead of hand-rolling it.
- **State is not shared between backends.** SQLite is default; switching to PostgreSQL via `OOMOL_CONNECT_DATABASE_URL` requires running `npm run runtime:migrate` yourself before first start and before every upgrade that adds migrations. The server verifies migrations but never applies DDL at startup.
- **Apache-2.0 covers their code only.** Provider names, logos and assets stay with their owners — relevant before showing provider logos in a Zeline UI.

## Judging the project

Real code: 531M of TypeScript, 1451 provider definitions, provider-specific commits, a genuine SSRF guard with DNS resolved-address validation and a cloud-metadata blocklist. The catalog numbers are honest — README claims "1000+ providers / 10.000+ actions" while the live catalog reports 1453/14.922 and local codegen produces 1451/15.155.

The marketing is loud in a way the engineering is not: repo created late June 2026, 5.4k stars in two months, a README GIF explaining how to star it, and star-history committed by CI daily. The 8,5% fork/star ratio (464/5434) suggests people really are deploying it, not just bookmarking. Treat the growth curve as unverified and the code as verified.
