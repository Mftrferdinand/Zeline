---
name: mt5-metaapi-termux
description: |
  Manage a real MT5 (or MT4) forex account from Termux/Android/Linux via the
  MetaApi.cloud REST + WebSocket API — place/close/modify orders on command or
  from analysis, read positions & equity, and copy-trade (CopyFactory). Use when
  the user wants an AI agent to auto-enter, manage, or copy trades on MetaTrader
  WITHOUT running the MT5 terminal locally (the MetaTrader5 pip package is
  Windows-only and does NOT work on Termux/ARM). All calls are plain HTTPS so
  they run fine from Termux. Load for "bot trader MT5", "auto entry MT5",
  "copy trade forex", "manage akun forex".
metadata:
  zeline:
    tags: [mt5, mt4, metaapi, forex, trading, copy-trade, termux]
    category: research
---

# MT5/MT4 auto-entry & copy-trade from Termux via MetaApi.cloud

The official `MetaTrader5` Python package is **Windows-only** and needs a running
MT5 desktop terminal on the same machine — it CANNOT run on Termux/Android (ARM,
no Wine). The only path that fully works from a phone/Termux is **MetaApi.cloud**:
a cloud REST + WebSocket API that hosts the MT5 terminal for you. The agent just
makes HTTPS calls (use `http_request`/curl). Verified endpoints (2026) below.

## ⚠️ Safety first (say this to the user, every time)
- **Real money = real risk.** Even with a no-deposit-bonus balance, a bug can
  blow the account. STRONGLY prefer testing every flow on a **demo account**
  first, then flip to real by changing only the account credentials.
- **API key must be trade-enabled but NOT withdrawal-capable.** Never store the
  broker's withdrawal password anywhere. MetaApi only needs MT5 login/password/
  server — the *investor (read-only)* password can't trade, so you need the
  *master* trading password, but withdrawals happen only inside the broker portal.
- **Confirm before every live order** unless the user explicitly enables
  autonomous mode. Never invent fills — only report what the API returned.
- Keep the MetaApi `auth-token` in config/env, NEVER in a skill, memory, repo,
  or chat log. Reference it as "token from operator".

## What the user must prepare (checklist)
1. **MT5 account credentials** from their broker:
   - `login` (account number), `password` (MASTER/trading password, not investor),
     `server` (exact broker server name, e.g. `ICMarketsSC-Demo` / `Exness-Real12`),
     `platform` = `mt5` (or `mt4`).
2. **A MetaApi.cloud account** → sign up at https://app.metaapi.cloud
   → create an **API token** (Tokens page). This token is the bearer for all calls.
3. **Pricing (VERIFIED 2026-08 by scraping the SPA's server HTML):**
   - **Regular $30/month** (~$1/day) — shared API servers, 1 free MT account
     included, historical market data access.
   - **Extended $100/month** (~$3.33/day) — dedicated servers, priority support.
   - **7-day trial (activatable ONCE only)** + **$5 signup bonus**. "Free usage
     tier available" for low volume; **one free MT account** included on plans.
   - ⚠️ **API access is billed PAY-AS-YOU-GO ON TOP of the subscription** (their
     words: "API access is billed on pay as you go basis on top of subscription
     cost"). So $30 is the FLOOR, not the total — usage adds more. Exact usage
     rate showed "temporarily unavailable"; confirm via their online chat.
   - To scrape the numbers yourself (pricing page is a client-rendered SPA that
     returns empty to plain curl — take the server HTML + strip tags):
     `curl -s -A "Mozilla/5.0" https://metaapi.cloud/ | tr -d '\0' | python3 -c "import sys,re,html;t=re.sub(r'<[^>]+>',' ',sys.stdin.read());print(html.unescape(re.sub(r'\s+',' ',t)))" | grep -oiE '.{40}(\\\$[0-9.]+|per month|trial|free).{60}'`
   - **Honest take for the user:** trading a small no-deposit-bonus balance, $30/mo
     + usage can EXCEED the NDB profit potential. The free trial/tier is fine for
     LEARNING; long-term this is NOT free. Only zero-cost path = self-hosted EA
     bridge, which needs a 24/7 machine running MT5 (VPS/PC) — that's the trade-off.
4. Decide **demo vs real** and **risk limits** (max lot, risk % per trade, max
   daily loss, max open positions).

## API layout (three base hosts, all HTTPS)
- **Provisioning** (create/manage the MT5 account link):
  `https://mt-provisioning-api-v1.<region>.agiliumtrade.ai` (or the generic
  `https://mt-provisioning-api-v1.new.agiliumtrade.ai`).
- **Client / trading** (orders, positions, prices):
  `https://mt-client-api-v1.<region>.agiliumtrade.ai`.
- **CopyFactory** (copy-trading): `https://copyfactory-api-v1.<region>.agiliumtrade.ai`.
- Auth header on every call: `auth-token: <YOUR_METAAPI_TOKEN>`.
- Region tokens like `new-york`, `london`, `singapore`; `new` works as a default.
  The account object returned at creation tells you the region to pin.

## Step 1 — Provision the account (one-time)
`POST /users/current/accounts` on the **provisioning** host:
```bash
curl -s -X POST \
  "https://mt-provisioning-api-v1.new.agiliumtrade.ai/users/current/accounts" \
  -H "auth-token: $METAAPI_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "login": "12345678",
    "password": "MASTER_TRADING_PASSWORD",
    "name": "zeline-mt5",
    "server": "YourBroker-Server",
    "platform": "mt5",
    "magic": 424242,
    "application": "MetaApi",
    "type": "cloud-g2"
  }'
# → returns {"id":"<accountId>", ...}. Save accountId.
```
Then **deploy** and wait until it's connected & synchronized:
```bash
curl -s -X POST ".../users/current/accounts/<accountId>/deploy" -H "auth-token: $METAAPI_TOKEN"
# poll state until "DEPLOYED" + connectionStatus "CONNECTED":
curl -s ".../users/current/accounts/<accountId>" -H "auth-token: $METAAPI_TOKEN"
```
(First connect can take a minute or two while MetaApi syncs history.)

## Step 2 — Read account state (client host)
```bash
BASE="https://mt-client-api-v1.new.agiliumtrade.ai"
# account info (balance/equity/margin):
curl -s "$BASE/users/current/accounts/<accountId>/account-information" -H "auth-token: $METAAPI_TOKEN"
# open positions:
curl -s "$BASE/users/current/accounts/<accountId>/positions" -H "auth-token: $METAAPI_TOKEN"
# current price for a symbol:
curl -s "$BASE/users/current/accounts/<accountId>/symbols/EURUSD/current-price" -H "auth-token: $METAAPI_TOKEN"
```

## Step 3 — Place / close / modify orders (the core)
Single endpoint, `actionType` selects the operation:
`POST /users/current/accounts/<accountId>/trade` on the **client** host.
```bash
# MARKET BUY 0.10 EURUSD with SL/TP + slippage guard:
curl -s -X POST "$BASE/users/current/accounts/<accountId>/trade" \
  -H "auth-token: $METAAPI_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "actionType": "ORDER_TYPE_BUY",
    "symbol": "EURUSD",
    "volume": 0.10,
    "stopLoss": 1.0800,
    "takeProfit": 1.0900,
    "slippage": 5,
    "magic": 424242,
    "comment": "zeline"
  }'
```
Key `actionType` values:
- `ORDER_TYPE_BUY` / `ORDER_TYPE_SELL` — market order.
- `ORDER_TYPE_BUY_LIMIT` / `_SELL_LIMIT` / `_BUY_STOP` / `_SELL_STOP` — pending
  (requires `openPrice`).
- `POSITION_MODIFY` — change SL/TP: `{"actionType":"POSITION_MODIFY","positionId":"<id>","stopLoss":..,"takeProfit":..}`.
- `POSITION_CLOSE_ID` — close a position: `{"actionType":"POSITION_CLOSE_ID","positionId":"<id>"}`.
- `POSITION_PARTIAL` — partial close: add `"volume":0.05`.
- `ORDER_CANCEL` — cancel a pending order by `orderId`.
Response includes `orderId`/`positionId` and a `stringCode` (e.g. `TRADE_RETCODE_DONE`).
**Always read the response** — only report success when the retcode confirms it.

## Step 4 — Copy-trading (CopyFactory)
Copy from a provider (master) to the user's account (subscriber). On the
**copyfactory** host under `/users/current/configuration`:
- Register the master account as a **strategy provider** (`PUT .../strategies/<strategyId>`).
- Register the user's account as a **subscriber** to that strategy, with a
  scaling factor / risk multiplier.
- Docs: https://metaapi.cloud/docs/copyfactory/ (subscriber/provider model, ~1ms copy).
Only build this after Step 3 works — copy-trade is the same trade API under the hood.

## Guardrails to bake into ANY bot built on this (non-negotiable)
1. **Magic number** unique per bot — only manage/close positions with that magic;
   never touch the user's manual trades.
2. **Volume validation** — before sending, fetch `symbols/<sym>/specification`
   and clamp `volume` to min/max/step; reject if above the user's max-lot cap.
3. **Risk % per trade** — size from equity and SL distance, not a fixed lot.
4. **Max open positions / max daily loss / equity kill-switch** — stop trading
   when drawdown threshold hit; expose a hard "close all + halt" command.
5. **SL/TP mandatory** on entry + `slippage`/deviation cap on market orders.
6. **Idempotency** — dedupe order commands (a `clientId` / comment tag) so a
   retry or a repeated signal never double-fills. Reconcile `positions` after
   any reconnect.
7. **Least privilege** — trade-only credentials, dedicated sub-account, never
   withdrawal-capable.

## Termux / Zeline / Zeline wiring
- Everything above is plain HTTPS → use the `http_request` tool (method + headers
  `{"auth-token": "..."}` + JSON body). No Windows, no Wine, no local terminal.
- Store `METAAPI_TOKEN` + `accountId` in env/config, never in the skill.
- Analysis → decision can come from the `MarketAnalysis` skill; this skill is the
  execution layer that turns a decision into a verified order.

## Pitfalls
- **`MetaTrader5` pip package does NOT work on Termux** — it's Windows-only and
  needs a local terminal. Don't waste time; use MetaApi.
- **Wrong password type**: MetaApi needs the MASTER (trading) password. The
  investor password connects but every trade fails with a permissions retcode.
- **Server name must match the broker EXACTLY** (case-sensitive, incl. suffix).
- **First deploy is slow** — poll `connectionStatus` until `CONNECTED` before
  trading; sending orders too early returns a not-synchronized error.
- **Pricing not verified here** — the pricing page is a JS SPA; confirm live cost
  in a browser before committing real volume.
- **Broker T&C** — automation is generally allowed, but some brokers ban specific
  strategies (latency arbitrage, tick-scalping, bonus abuse). Check the broker's terms.
- Regional host mismatch → 404/timeout; use the region returned on the account object.
