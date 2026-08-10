# Telegram Sales Bot

> Build and operate Telegram digital-store bots: catalog, payment, delivery, admin commands, broadcasts, alerts, and transaction tracking.

Build and operate a full-auto Telegram storefront:

```text
Customer → Bot / Mini App
  ├── /start → register user + Open Mini App
  ├── catalog → product/plan/stock
  ├── buy/top-up → payment or balance
  ├── callback/admin approval → fulfill transaction
  └── credentials + globally traceable transaction ID

Admin → private bot commands / panel
  ├── stats, users, stock, pending, track
  ├── gift codes and Mini App announcements
  ├── broadcasts (text/media, segmentation, scheduling)
  └── automatic top-up/order/low-stock alerts
```

## Core Stack

- Telegram Bot API via webhook or long polling
- A testable update-handler layer independent of transport
- SQLite for users, orders, deposits, stock, campaigns, and schedules
- Payment-provider webhooks for automatic confirmation where available
- Mini App for storefront UI; private bot commands for fast admin operations

## Customer Feature Checklist

- [ ] `/start` registers/refreshes the Telegram user
- [ ] One prominent **Open Mini App** button
- [ ] Product catalog with plans and live stock
- [ ] Balance top-up and/or direct payment
- [ ] Order and top-up history
- [ ] Automatic credential delivery
- [ ] Global transaction IDs shared by top-ups and orders
- [ ] Gift Code claim
- [ ] Mini App promo/maintenance/info notifications

## Admin Feature Checklist

- [ ] Admin-only command scope and runtime ID guard
- [ ] Store stats, users, recent orders/top-ups, and user detail
- [ ] Pending top-up Approve/Reject buttons
- [ ] Unified `/track ID` across top-ups and orders
- [ ] Stock summaries and low/out-of-stock alerts
- [ ] Gift Code create/list/toggle/delete
- [ ] Announcement create/list/toggle/delete
- [ ] Broadcast text/photo/video/document
- [ ] Audience segments: all, buyers, non-buyers, one user
- [ ] CTA: Mini App, none, or custom URL
- [ ] Send now or persistent schedule
- [ ] Success/failure report and inactive-user marking
- [ ] Successful-order and new-top-up alerts

When the user asks for a feature to be “complete,” implement the full useful lifecycle, not a create-only stub. For manageable resources that normally means create, list, toggle/disable, delete, validation, visible result reporting, and tests. For broadcast that means media, segmentation, CTA, preview, send-now, scheduling, cancellation, and delivery statistics.

## Payment Architecture

```text
Buy → create transaction → create provider payment
  → provider webhook verifies payment
  → idempotent fulfillment
  → allocate stock atomically
  → deliver credentials
  → notify admin and user
```

Common Indonesian payment providers include Xendit, Duitku, Midtrans, and iPaymu. Production accounts generally require identity/business verification. Keep provider secrets and callback-verification tokens outside source control.

## Data Integrity Rules

1. Never trust client-submitted prices; resolve product/plan prices from the database.
2. Purchase fulfillment must atomically check stock, debit balance, allocate credentials, and mark delivery.
3. Payment callbacks must be idempotent. A repeated callback must not redeliver stock or duplicate admin alerts.
4. Top-up approval must add balance exactly once.
5. Telegram IDs can exceed signed 32-bit range. Use `Number()`/safe-integer handling, never bitwise coercion.
6. Internal table IDs are not transaction evidence because deposit/order IDs collide. Use one global transaction sequence shared by both.
7. A revenue reset must not delete transaction history. Store a revenue baseline and display current delivered total minus that baseline.

## Bot Runtime Verification

A BotFather command entry is only menu metadata. Before claiming `/command` works:

1. Verify the exact token with `getMe`.
2. Inspect `getWebhookInfo` to determine the update transport.
3. Confirm exactly one webhook or long-polling consumer exists for that token.
4. Inspect process working directories and trace the deployed entrypoint; do not modify an unrelated legacy bot.
5. Feed a real-shaped update into a deterministic handler test.
6. Start the worker and confirm its readiness log.
7. Verify public/admin command scopes with `getMyCommands`.

A token used only to validate Mini App `initData` does **not** process Telegram commands.

## Testable Design

Separate transport from behavior:

```js
createTelegramBot({ db, api, sleep, now, adminId, webAppUrl })
bot.processUpdate(update)   // deterministic tests
bot.startPolling()          // production transport
```

Inject Telegram API, clock, and sleep. Test admin guards, callbacks, media previews, segmentation, schedules, idempotency, alerts, and DB effects without network calls.

## Detailed References

- [`references/broadcast-command-and-polling.md`](references/broadcast-command-and-polling.md) — diagnosing silent commands and implementing the basic admin broadcast/polling seam.
- [`references/admin-operations-and-broadcasting.md`](references/admin-operations-and-broadcasting.md) — complete admin command set, broadcast state machine, media-specific API behavior, scheduling, alerts, global transaction IDs, revenue baselines, and full regression checklist.
- [`references/miniapp-transaction-history-i18n.md`](references/miniapp-transaction-history-i18n.md) — render-time history localization, locale-aware dates, warranty units, global transaction IDs, state preservation, and regression checks.
- [`references/transaction-notification-outbox.md`](references/transaction-notification-outbox.md) — persistent FIFO alerts, ambiguous-send handling, duplicate prevention, single-flight flushing, and restart verification.
- [`references/usdt-onchain-payment.md`](references/usdt-onchain-payment.md) — USDT on-chain payment (manual verify): QR generation, tx hash validation, admin approve/reject via bot inline keyboard, nominal unique pattern, anti-fraud checks, pitfalls.
- [`references/MyStore-run-restart.md`](references/MyStore-run-restart.md) — running/restarting/verifying THIS user's MyStore backend on Termux: `start.sh` launcher + envs, readiness markers, getMe/HTTP verification, ghost-process cleanup. Use this instead of `local-dev-servers` for Node bot backends.

## Admin Response Formatting

For this user's Telegram sales bots, every command result, transaction detail, management record, callback result, broadcast preview/report, and automatic admin alert should use one consistent plain-text box formatter:

```text
╭─ Transaction #3
├ Type : Purchase
├ User : @username
├ User ID : <OWNER_CHAT_ID>
├ Product : Proton Pro
├ Quantity : 1
├ Total : Rp5.000
├ Status : Delivered
╰──────────────
```

Formatting rules:

1. Prefer the Telegram username for `User`, normalized to one leading `@`; fall back to display name, then user ID.
2. For planned products, show the plan/type only (`Proton Pro`), not the redundant parent product plus plan (`Proton VPN — Proton Pro`). Use the product name only when no plan exists.
3. Humanize database statuses (`waiting_confirm` → `Waiting Confirmation`, `waiting_pay` → `Awaiting Payment`, `delivered` → `Delivered`).
4. Reuse one formatter for commands, button/callback edits, empty/error states, schedules, and automatic alerts. Do not mix polished boxes with old colon-delimited blocks.
5. Preserve user-authored broadcast content itself; box only the preview metadata, progress, and delivery report.
6. Cover exact box characters, username precedence, plan-only labels, multiline values, and status mapping with deterministic tests.

See [`references/admin-operations-and-broadcasting.md`](references/admin-operations-and-broadcasting.md) for implementation patterns and the formatting regression checklist.

## Mini App History Rules

1. Return raw fallbacks **and** product/plan/duration/warranty i18n maps from history APIs.
2. Localize persistent values at render time so existing orders change language immediately without rewriting transaction records.
3. Format dates from timestamps with the active locale; never freeze every history date to one locale.
4. Parse warranty units explicitly and expose a canonical duration. Never assume every first number means days.
5. Keep the active order/deposit detail or pagination state when language changes; do not let stale checkout state redirect the user.
6. Display the global public `txn` in user history while retaining internal IDs for joins and admin actions.

## Pitfalls

1. **Bot token conflict**: kalau 2 instance jalan bersamaan (misal dev + prod), dapat `409 Conflict: terminated by other getUpdates`. **Solution**: pastikan cuma 1 proses aktif, atau pakai webhook mode (bukan polling) buat prod.
2. **Double server process bikin route 404**: kalau restart gagal kill proses lama, server baru jalan tapi route lama yang termount (karena PID lama masih pegang port atau ada 2 server beda port). Gejala: syntax OK, `node --check` pass, tapi endpoint baru 404. **Solution**: `pkill -9 -f 'node server.js'` + `pgrep -af 'node server'` verify 0 result sebelum start ulang. Jangan andalkan `pkill` tanpa `-9` — process bisa stuck di cleanup.
2. **Avoid duplicate pollers.** Multiple consumers for one token cause missing/conflicting updates. During restart, kill and verify the entire old process tree—not only the tracked shell, which can leave an orphaned `node server.js`. Confirm one consumer by PID and working directory, then observe logs for longer than one `getUpdates` timeout before declaring the conflict gone. Historical “polling active” watch notifications are not proof that old processes are still alive.
3. **Persistent dedupe alone does not guarantee ordered, duplicate-free alerts.** Flush the outbox single-flight and strict FIFO. Stop the batch on the first failure; an older failed event must never be bypassed by newer transactions. A timeout/network failure after a Telegram send starts is delivery-ambiguous: mark it `delivery_unknown`, never blindly resend it, and use it as a barrier until explicitly reconciled. On startup, treat rows left in `sending` as ambiguous rather than automatically returning them to `pending`.
4. **Isolate post-commit side effects from each other.** A low-stock helper, analytics hook, or missing import must not prevent the committed order alert from being flushed. Test the real HTTP purchase path and assert runtime logs contain no post-purchase side-effect errors; run the critical order-alert flush independently from optional follow-up alerts.
5. **Media previews are not text messages.** Use `editMessageCaption` for photo/video/document progress and `editMessageText` for text.
6. **Guard every admin entry point.** Protect commands, draft ingestion, callbacks, scheduling, cancellation, and destructive actions—not just command-menu visibility.
7. **Do not reactivate every user on each boot.** Use a one-time migration marker, then update activity only on real interactions/delivery outcomes.
8. **Mark blocked/deactivated recipients inactive; do not delete transaction history.**
9. **Payment gateways need a public callback URL and verified signatures.**
10. **Bot/payment tokens are secrets.** Keep them in environment variables or protected secret files; redact them from logs and reports.
11. **SQLite is appropriate for small storefronts** when multi-step balance/stock operations use explicit transactions.
12. **Headless signup flows can be blocked by reCAPTCHA.** Use a real device for provider/account registration when necessary.

## Verification Before Delivery

- Syntax checks pass
- Handler regression suite passes
- Database migration preserves production row counts
- Health endpoint responds
- Exactly one bot consumer is running
- Polling/webhook readiness is visible
- Public/admin command scopes are correct
- A safe test verifies command response, preview, callback, and result reporting
- Reset-revenue tests prove orders/deposits remain and only new revenue accrues
- No credentials or tokens appear in the final report


---

## Lampiran: `references/admin-operations-and-broadcasting.md`

# Telegram Sales Bot: Admin Operations and Broadcasting

Use this reference when adding admin commands, broadcasts, schedules, or transaction alerts to a Telegram storefront.

## 1. Prove the command has a live update consumer

A registered BotFather command is only metadata; it does not implement behavior. Before changing handlers:

1. Call `getMe` for the exact token and confirm the expected bot username.
2. Call `getWebhookInfo` and identify whether updates arrive through webhook or long polling.
3. Inspect running processes and ensure exactly one update consumer exists for that token.
4. Trace the deployed entrypoint, not an old bot script in another directory.
5. Build a deterministic `processUpdate(update)` test before starting live polling.

Common root cause of a silent command: the API/backend uses the token only to validate Mini App `initData`, but no webhook or polling worker processes Telegram updates.

Never claim a command is available until the handler is implemented, the update consumer is running, and `getMyCommands` plus a handler-level regression test pass.

## 2. Recommended admin-only command set

Keep the public command scope minimal (`/start`). Register operational commands only in the admin chat scope and enforce the admin ID again inside every message and callback handler.

Useful commands:

- `/admin` — command help
- `/stats` — users, orders, resettable revenue, pending top-ups, stock
- `/pending` — pending top-ups with Approve/Reject buttons
- `/track ID` — unified top-up/order lookup by global transaction ID
- `/orders`, `/topups` — recent transactions
- `/user ID`, `/users` — user detail and audience health
- `/stock` — stock by product and plan
- `/giftcode`, `/giftcodes` — create/list/toggle/delete gift codes
- `/announce`, `/ads` — create/list/toggle/delete Mini App announcements
- `/broadcast`, `/scheduled`, `/cancel` — campaign workflow

For Telegram IDs, use `Number()` or safe integer parsing. Never use bitwise coercion (`| 0`), which truncates IDs larger than 32 bits.

## 3. Broadcast state machine

A reliable broadcast flow is explicit and resumable within the worker:

1. `/broadcast`
2. Capture content: text, photo, video, or document plus caption.
3. Choose segment: all active users, buyers, non-buyers, or one registered user.
4. Choose CTA: Open Mini App, no button, or custom URL button.
5. Preview using the same send method as delivery.
6. Send now or schedule in a named timezone.
7. Report recipients, successes, and failures.

Store per-admin draft state with steps such as `content`, `segment`, `target_user`, `cta`, `custom_cta`, `preview`, and `schedule_time`. `/cancel` must clear the draft.

### Media-specific pitfall

Telegram previews are different message types:

- Text progress/results: `editMessageText`
- Photo/video/document progress/results: `editMessageCaption`

Using `editMessageText` on a media preview causes the Send button flow to fail even though text broadcasts work.

### Delivery behavior

- Retry Telegram 429 responses using `parameters.retry_after`.
- Pace bulk sends conservatively.
- Mark users inactive after persistent delivery failures such as blocked/deactivated chats; do not delete their transaction history.
- Use `bot_started`, `bot_active`, and `bot_last_seen` audience fields.
- If migrating existing users, use a one-time `meta` migration marker. Do not set every user active on every boot.

## 4. Scheduling

Persist schedules instead of relying on in-memory timers alone. Suggested fields:

```sql
id, content_json, segment, target_user_id, scheduled_at,
status, recipients, success, failed, created_at, sent_at
```

Store timestamps as epoch milliseconds and make the input timezone explicit. A periodic worker should atomically move `pending` to `sending`, deliver, then save `sent` or `failed`. Provide cancellation only while status is `pending`.

The stored JSON should include media `file_id`, caption/text, CTA, and optional target user. Telegram `file_id` reuse avoids downloading and re-uploading media.

## 5. Transaction alerts

Useful automatic admin alerts:

- New top-up: amount, method, sender, user ID, global transaction ID, Approve/Reject buttons.
- Successful order: user, product/plan, quantity, payment method, amount, transaction ID.
- Low/out-of-stock: product/plan and remaining count.

Approve/reject must be transactional and idempotent:

1. Select only a still-pending deposit.
2. Begin DB transaction.
3. Change status.
4. Add balance only on approve.
5. Commit, then notify the user.

For payment webhooks, suppress duplicate order alerts when fulfillment reports the order was already delivered.

## 6. Global transaction IDs

Internal table IDs may collide (`deposit #1` and `order #1`). For proof-of-transaction tracking, use one global sequence shared by deposits and orders:

- Allocate the next value inside the same transaction as record creation.
- Store it in a `txn` column on both tables.
- Search a unified admin endpoint by `txn` and return a type discriminator.
- Backfill legacy records in global creation-time order.
- Never reuse IDs after normal deletes. Reset only when the operator explicitly requests a full clean slate.

## 7. Resettable revenue without deleting history

A “Reset Revenue” control must not delete orders or deposits, and setting an unrelated counter to zero does not work if the dashboard still computes `SUM(orders.total)`.

Use a baseline:

```text
visible_revenue = delivered_order_total - revenue_baseline
```

On reset, set `revenue_baseline` to the current delivered total. New sales then accumulate from zero while transaction history remains intact. Share the same helper between the panel API and bot `/stats` so they cannot disagree.

## 8. Consistent admin response boxes

Use one pure formatter for every structured response, not one-off template strings:

```js
formatBox('Transaction #3', [
  ['Type', 'Purchase'],
  ['User', telegramUserLabel(username, name, userId)],
  ['User ID', userId],
  ['Product', productLabel(productName, planName)],
  ['Quantity', qty],
  ['Total', rupiah(total)],
  ['Status', humanStatus(status)],
])
```

Required helper behavior:

- `telegramUserLabel`: username first; normalize to a single leading `@`; fallback to name, then ID.
- `productLabel`: plan/type first when present; parent product only for items without plans.
- `humanStatus`: map machine states to human labels rather than exposing underscores or lowercase database values.
- `formatBox`: exact `╭─`, `├`, `│`, `╰─` structure; support multiline values by indenting continuation lines.
- `formatList`: same visual family for recent orders/top-ups, stock, admin help, and other list commands.

Apply the formatter to `/stats`, `/users`, `/stock`, `/track`, `/orders`, `/topups`, `/user`, `/pending`, Gift Code and announcement records, approve/reject results, broadcast preview/progress/results, schedules, and top-up/order/stock alerts. Keep the actual user-authored broadcast message unchanged.

Regression tests should assert the exact frame, username precedence, plan-only product names, humanized statuses, multiline content, and absence of legacy `User:`/`Product:` template blocks.

## 9. Test seams and regression suite

Build the bot around injectable dependencies:

```js
createTelegramBot({ db, api, sleep, now, adminId, webAppUrl })
```

Expose `processUpdate(update)` separately from `startPolling()` so tests can feed real-shaped Telegram updates without network calls.

Minimum regression coverage:

- Admin guard and public `/start`
- Command responses and 64-bit Telegram IDs
- Pending approve/reject and balance idempotency
- Unified transaction tracking
- Gift code and announcement create/toggle/delete
- Text and media broadcast previews
- All/buyers/non-buyers/single-user segmentation
- App/custom/no CTA
- Send-now media progress (`editMessageCaption`)
- Schedule persistence and cancellation
- Inactive-user handling and result counts
- Top-up/order/low-stock alerts
- Revenue baseline reset while orders/deposits remain

After tests pass, verify live state: health endpoint, one polling process, polling startup log, admin-scoped `getMyCommands`, and unchanged production row counts after migration.



---

## Lampiran: `references/broadcast-command-and-polling.md`

# Telegram Broadcast Commands and Polling

Use this reference when a Telegram sales bot needs `/broadcast`, `/start`, or any other command handled outside the Mini App.

## Critical distinction

A bot token used only to validate Telegram Mini App `initData` does **not** make bot commands work. `/broadcast` will be silent unless a process consumes updates through one of these mutually exclusive mechanisms:

- Long polling with `getUpdates`
- A configured webhook via `setWebhook`

Before claiming a command exists or works, verify all three layers:

1. The source has a command handler.
2. The deployed process actually starts the polling/webhook worker.
3. The correct bot token is attached to that process.

Useful probes:

```bash
curl -s "https://api.telegram.org/bot$BOT_TOKEN/getMe"
curl -s "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"
curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/getMyCommands" \
  -H 'Content-Type: application/json' \
  -d '{"scope":{"type":"chat","chat_id":ADMIN_ID}}'
```

Also inspect running processes and their working directories. A similarly named legacy bot may be alive while the intended bot backend never consumes updates. Avoid running two pollers for the same bot token; Telegram will return update conflicts.

## Recommended `/broadcast` flow

1. Admin sends `/broadcast`.
2. Bot sets per-admin state to `awaiting_text`.
3. Next text becomes a draft; `/cancel` clears it.
4. Bot loads recipients from the canonical app user table.
5. Bot sends a preview with recipient count and inline buttons:
   - `Send Broadcast`
   - `Cancel`
6. On confirmation, clear the draft before sending to prevent duplicate taps.
7. Send sequentially with a short delay and honor Telegram `retry_after` on HTTP 429.
8. Continue after per-user failures and report total, delivered, and failed counts.

Broadcast authorization must be enforced on every entry point:

- `/broadcast` command
- Draft message ingestion
- `broadcast_send` callback
- `broadcast_cancel` callback

Silently ignore non-admin broadcast requests. Do not rely only on hiding the command from the public command menu.

## Recipient registry

Broadcast can only reach users who have previously interacted with the bot. Make `/start` upsert the Telegram user into the same user database used by the Mini App. If users are created only after Mini App login, `/start` should still register them so the broadcast list is complete.

Exclude the admin from recipients unless the product explicitly wants a self-copy. Keep blocked/deactivated users either marked inactive or count them as failures; never abort the whole broadcast because one user blocked the bot.

## UX defaults

- Public commands: `/start` only.
- Admin chat scope: `/start` and `/broadcast` via `setMyCommands` with `scope.type = "chat"`.
- Broadcast messages may include a single `Open App` Web App button.
- Keep the preview and final report plain and compact.
- Limit draft length below Telegram's message ceiling (for example 3,500 characters) to leave room for wrappers if needed.

## Test seam

Design the worker so update processing can be tested without polling the live Telegram API:

```js
const bot = createTelegramBot({
  token,
  db,
  ensureUser,
  adminId,
  webAppUrl,
  api: fakeTelegramApi,
  sleep: async () => {},
});

await bot.processUpdate(fakeUpdate);
```

Regression coverage should prove:

- Admin `/broadcast` receives a prompt.
- Next message produces a preview and correct recipient count.
- Confirm callback sends once to every recipient.
- Final report contains delivered/failed counts.
- Cancel clears state.
- Non-admin command and callbacks cause no broadcast side effects.

Run the test before implementation to prove the original silent-command symptom, then rerun after wiring the polling/webhook worker into the actual startup path.

## Verification checklist

- `getMe` identifies the intended bot.
- `getWebhookInfo` matches the chosen update mode.
- Startup logs show the worker is active.
- `getMyCommands` shows `/broadcast` in the admin chat scope.
- Backend health still passes.
- Regression test passes.
- A real admin `/broadcast` receives the prompt.
- No duplicate poller uses the same token.



---

## Lampiran: `references/MyStore-run-restart.md`

# MyStore — Run / Restart / Verify (Termux)

MyStore = Telegram digital-store bot (`@mystore_bot`) + Express backend. NOT a web dev
server — it's a Node production bot. Manage it with the procedure below, not with
`local-dev-servers` (that skill is for Astro/MkDocs/static preview servers).

## Layout

- `~/store-backend/` — backend repo (Express + SQLite + Telegram bot)
  - `server.js` — entrypoint; mounts API on `:8899` AND calls `createTelegramBot` (polling) in the same process
  - `start.sh` — THE launcher. Exports env (PORT=8899, BOT_TOKEN, ADMIN_USER/PASS,
    ADMIN_TELEGRAM_ID=<OWNER_CHAT_ID>, WEB_APP_URL, NOWPAYMENTS_API_KEY/IPN_SECRET,
    PUBLIC_URL, USDT_WALLET/USDT_NETWORK, IDR_PER_USD), then `node server.js`
  - `config.js` — reads env; `DEV_MODE=true` when `BOT_TOKEN` empty (identity from query = insecure)
- `~/logs/store-backend.log` — log file
- Public URL: `https://MyStore.web.id` via Cloudflare Tunnel → `localhost:8899`

## Start

```bash
cd ~/store-backend && bash start.sh   # via terminal(background=true)
```

Watch patterns: `["bot polling aktif", "backend jalan", "Error:", "EADDRINUSE"]`.

Readiness markers (both must appear):
- `MyStore backend jalan di port 8899 (DEV_MODE=false)` ← DEV_MODE=false proves BOT_TOKEN loaded
- `MyStore Telegram bot polling aktif`

## Verify (never report "running" on start alone)

1. HTTP: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8899/` → expect 200
2. Bot identity: `curl -s "https://api.telegram.org/bot$(grep -oP 'BOT_TOKEN="\K[^"]+' ~/store-backend/start.sh)/getMe"` → ok:true, username `@mystore_bot`
3. Exactly one consumer: `pgrep -af "node server.js"` → one line only (duplicate pollers = 409 conflict)

## Restart

```bash
pkill -9 -f 'node server.js'
sleep 2
pgrep -af 'node server.js' || echo "all killed"   # MUST show 0 before starting
cd ~/store-backend && bash start.sh          # then verify as above
```

## Pitfalls

- **BOT_TOKEN lives in `start.sh`, NOT in `~/.zeline/.env`.** `~/.zeline/.env` holds Zeline'
  own `TELEGRAM_BOT_TOKEN` (a different bot). Grep for `BOT_TOKEN` there and you'll only hit
  the Zeline token — don't confuse them. If the bot starts in DEV_MODE, `start.sh` export is missing/empty.
- **Old log full of `Telegram polling error: fetch failed` is stale Termux-network noise, not
  a code failure.** If a fresh start prints clean readiness lines and getMe responds, ignore
  the historical error lines in the log.
- **`process(kill)` only kills the tracked shell** — the `node server.js` child survives.
  Always use `pkill -9 -f 'node server.js'` + pgrep-verify for cleanup (same rule as
  local-dev-servers ghost-process pitfall).
- Mini App `initData` validation needs the real BOT_TOKEN, same token as the polling bot.



---

## Lampiran: `references/miniapp-transaction-history-i18n.md`

# Mini App Transaction History i18n and Warranty Semantics

Use this reference when purchase/top-up history has translated labels but persistent values still appear in the wrong language.

## Root-cause pattern

The usual bug is not the label dictionary. The backend returns only raw fallback columns such as `duration` and `warranty`, even though multilingual maps exist in `duration_i18n` and `warranty_i18n`. The client then freezes those raw strings into history objects.

A correct history API returns both:

```json
{
  "duration": "7 Hari · Berbagi",
  "duration_i18n": {"id":"7 Hari · Berbagi","en":"7 Days · Sharing"},
  "warranty": "1 Hari · Garansi",
  "warranty_i18n": {"id":"1 Hari · Garansi","en":"1 Day · Warranty"}
}
```

Do the same for product and plan names. Parse malformed/legacy JSON safely and preserve raw fields as fallback.

## Render-time localization

Persistent history must not store only the language active when the transaction was created. Keep the multilingual payload and choose values every time history/detail is rendered:

```text
localized = map[current_language] || map.en || map.id || raw_fallback
```

Re-render the active row/detail when the language changes. Preserve sub-page state (order detail, purchase-history page, deposit detail, deposit-history page); do not let a stale checkout state redirect the user back to payment.

Format dates at render time using the active locale (`en-US` or `id-ID`). A timestamp is authoritative; a preformatted date string is only a legacy fallback. Also update the document `lang` attribute.

## Warranty semantics

Never extract the first number and assume days. Parse explicit units:

- minute/menit
- hour/jam
- day/hari
- week/minggu
- month/bulan
- year/tahun

Return or compute a canonical `warranty_ms`, then set expiry as `created_at + warranty_ms`. “No Warranty” / “Tanpa Garansi” has zero duration: show the warranty value if useful, but do not render a false `Expired` status row.

Timer terminal text (`Expired` / localized equivalent) must use the translation dictionary. Payment-success and loaded-history paths must share warranty semantics.

## Transaction-ID consistency

User-visible history must display the global public transaction ID (`txn`), not an internal deposit/order row ID. Keep internal IDs separately for approve/reject and joins.

## Data-quality sweep

After wiring i18n, inspect every plan’s English map for leaked Indonesian terms and obvious singular/plural/typo errors. Update only the English map; do not alter raw Indonesian values or transaction evidence. Treat admin-entered values as untrusted display data and HTML-escape them.

## Regression checklist

- API includes product/plan/duration/warranty i18n maps.
- English detail shows English values; Indonesian detail shows Indonesian values.
- The same stored order switches languages without refetching or stale strings.
- English and Indonesian dates use their respective month names.
- Minute/hour/day/month warranties produce correct milliseconds.
- “No Warranty” does not produce `Expired`.
- Language switching preserves the active history/detail page.
- New top-ups show global `txn`, not internal `depositId`.
- Malformed legacy i18n JSON falls back without crashing.
- Syntax, helper regression, API integration, public asset version, health endpoint, and production row counts are verified before delivery.



---

## Lampiran: `references/transaction-notification-outbox.md`

# Ordered, Duplicate-Resistant Telegram Transaction Alerts

Use this pattern when purchase/top-up alerts must arrive in transaction order without duplicates, even across retries and restarts.

## Delivery guarantee to choose explicitly

Telegram `sendMessage` has no caller-supplied idempotency key. If the HTTP request times out after Telegram accepted it, the sender cannot know whether the message exists. Therefore true exactly-once delivery is impossible from the sender alone.

For stores where duplicate alerts are worse than a rare missing alert, use **at-most-once for ambiguous sends**:

- definitely failed before send acceptance → retry later;
- timeout/connection loss after send starts → mark `delivery_unknown`, do not auto-retry;
- process crash with a row left in `sending` → convert to `delivery_unknown`, not `pending`;
- keep `delivery_unknown` as a FIFO barrier until an operator explicitly marks it `sent` or confirms it should return to `pending`.

Document this trade-off. Do not claim exactly-once semantics.

## Durable invariants

1. Enqueue the alert in the **same database transaction** that commits the order/top-up.
2. Use one globally unique dedupe key, such as `order:<global_txn>` or `deposit:<global_txn>`, protected by a `UNIQUE` constraint.
3. Read `pending` rows by ascending outbox ID/creation order.
4. Stop the flush on the first failure. A newer event must never overtake an older one.
5. Run one flush at a time. If another flush is requested while busy, set a `requested` latch and run again after the current pass.
6. Never let optional work such as low-stock calculation prevent the committed order alert from flushing.
7. Keep Telegram polling single-consumer. A `409 Conflict: terminated by other getUpdates request` means another consumer still owns the token.

Suggested state model:

```text
pending → sending → sent
              ├→ pending           definite retryable failure
              └→ delivery_unknown  ambiguous network result / process crash
```

## Single-flight flush pattern

```js
let busy = false;
let requested = false;

async function flushSoon() {
  if (busy) { requested = true; return; }
  busy = true;
  try {
    do {
      requested = false;
      await flushOldestPendingBatch();
    } while (requested);
  } finally {
    busy = false;
  }
}
```

Inside `flushOldestPendingBatch()`:

1. Convert stale `sending` rows to `delivery_unknown`.
2. If any `delivery_unknown` barrier exists before pending work, return blocked.
3. Select pending rows ascending.
4. Claim one row as `sending`.
5. Dispatch it.
6. On success mark `sent`.
7. On definite failure restore `pending`, record the error, and break.
8. On ambiguous failure mark `delivery_unknown`, record the error, and break.

## Post-commit side-effect ordering

A purchase response may already be committed while post-commit work still runs. Isolate each side effect:

```text
commit purchase + enqueue order alert
→ acknowledge purchase
→ flush mandatory order outbox
→ enqueue/flush optional low-stock alert (separate try/catch)
```

A missing import or exception in stock calculation must not skip the order flush. Add a regression test that asserts runtime logs do not contain post-purchase side-effect failures.

## Compact admin help

Generic list formatters may insert visual separator rows (`│`) between items. When the user requests a dense command menu with no blank lines, render the exact static line array and join with `\n`; do not reuse a separator-adding formatter. Keep displayed limits synchronized with behavior (`Latest 10` must query `LIMIT 10`).

## Regression checklist

- Same dedupe key enqueued twice produces one row.
- First failed event prevents dispatch of the second event in that pass.
- Ambiguous send is not retried automatically.
- Restart recovery does not change `sending` to `pending`.
- `delivery_unknown` blocks newer alerts until explicitly resolved.
- Two simultaneous flush requests do not run two dispatch loops.
- Optional low-stock failure cannot delay the purchase alert.
- Recent order/top-up commands return exactly the advertised count.
- Restart verification shows one actual token consumer, not merely one wrapper process.

## Restart hygiene

Killing a shell wrapper does not guarantee its child worker exited; the child can be re-parented and continue polling. After every restart:

1. enumerate actual worker processes and their working directories;
2. confirm the old child PID is gone;
3. verify the local health endpoint;
4. wait longer than one long-poll cycle and inspect logs for conflicts;
5. verify the public health endpoint;
6. confirm exactly one worker remains.

Do not treat the startup log line alone as readiness proof.


---

## Lampiran: `references/usdt-onchain-payment.md`

# USDT On-Chain Payment (Manual Verification)

Implement direct USDT payments to your wallet (BEP20/BSC) with admin manual verification. User transfers on-chain, submits transaction hash, admin verifies via BSCScan, then approves/rejects.

## Why This Pattern

- **No gateway fees** — uang langsung ke wallet kamu
- **No minimum** — beda dari NOWPayments ($12), bisa transaksi kecil
- **No KYC** — nggak butuh daftar/verifikasi merchant account
- **Full control** — kamu yang verify transfer, nggak ada auto-fraud-detection yang salah blocking

## Architecture

```text
User flow:
  1. Pilih produk → bayar USDT
  2. App tampilkan: wallet address + QR + nominal USDT unique
  3. User transfer → submit tx hash (0x...)
  4. Order status 'waiting_confirm' → app polling tiap 5 detik
  5. Admin approve → kredensial terkirim + status 'delivered'

Admin flow:
  1. Bot notif: "New USDT On-Chain Order #123" + [Approve] [Reject]
  2. Cek BSCScan → pastikan transfer masuk + nominal sesuai
  3. Tap Approve → bot kirim kredensial ke user
  4. Atau Reject kalau transfer salah/palsu
```

## Implementation Checklist

### Backend

- [ ] Config: `USDT_WALLET`, `USDT_NETWORK`, `IDR_PER_USD`
- [ ] Install `qrcode` npm package (pure JS, no native deps)
- [ ] Endpoint `/api/buy/usdt-onchain`: create order + return `{ wallet, network, amountUsdt, qr }`
- [ ] **Nominal unique**: `base + (orderId % 1000) / 100000` → 5 desimal (misal 3.00182 USDT)
- [ ] Endpoint `/api/order/:id/submit-tx`: validate tx hash regex, check duplicate, update `provider_ref`, status → `waiting_confirm`
- [ ] `purchase.js`: `submitOnchainTxHash()`, `approveOnchainOrder()`, `rejectOnchainOrder()`
- [ ] Admin endpoints: `/api/admin/orders-pending`, `/api/admin/order/:id/approve`, `/api/admin/order/:id/reject`

### Frontend (Mini App)

- [ ] `startBuyUsdt()` call `/api/buy/usdt-onchain`
- [ ] `showOnchainPayment()`: tampilkan alamat (onclick copy) + QR + warning jaringan + form tx hash
- [ ] Input validation: regex `^0x[a-fA-F0-9]{64}$`
- [ ] `submitOnchainTxHash()` → POST `/api/order/:id/submit-tx`
- [ ] `showOnchainSubmitted()`: status "Menunggu Verifikasi Admin" + emoji ⏳
- [ ] Polling: `startBuyUsdtPoll()` tiap 5 detik, stop saat `status === 'delivered'`
- [ ] i18n: `onchain_title`, `onchain_send_exact`, `onchain_to_address`, `onchain_network_warn`, `onchain_txhash_ph`, `onchain_confirm_btn`, `onchain_bad_hash`, `onchain_submitted_title`, `onchain_submitted_msg`

### Telegram Bot

- [ ] `telegram-outbox.js`: dispatch event `onchain_order` + `order_delivered`
- [ ] `telegram-bot.js`:
  - [ ] `notifyOnchainOrder(data)`: admin notif + inline keyboard `orderKeyboard(id)` → `ord_approve_`, `ord_reject_`
  - [ ] `notifyOrderDelivered(data)`: kirim kredensial ke user
  - [ ] `resolveOnchainOrder(query, id, approve)`: callback handler → panggil `purchase.approveOnchainOrder()` atau `rejectOnchainOrder()`
  - [ ] `/pending`: sertakan order USDT `waiting_confirm` + button approve/reject
  - [ ] Export: `notifyOnchainOrder`, `notifyOrderDelivered`
- [ ] Callback routing: `data.startsWith('ord_approve_')` → `resolveOnchainOrder(query, id, true)`

### Security & Anti-Fraud

- [ ] **Tx hash unique check**: `SELECT id FROM orders WHERE provider_ref=? AND id<>?` → reject kalau sudah dipakai order lain
- [ ] **Regex validation**: `^0x[a-fA-F0-9]{64}$` (BSC/ERC-20 tx hash format)
- [ ] **Manual verify wajib**: stok nggak dealokasikan sampai admin approve
- [ ] **Warning jaringan**: "Hanya kirim USDT di jaringan BEP20 (BSC). Salah jaringan = dana hilang permanen."
- [ ] **Provider_ref format**: simpan tx hash murni (bukan prefix `onchain:`), atau prefix tapi parse saat display

## Code Patterns

### QR Generation (Backend)

```js
const QRCode = require('qrcode');

// Generate QR dari alamat wallet (plain atau ethereum: URI)
const qr = await QRCode.toDataURL(cfg.USDT_WALLET, { margin: 1, width: 260 });
// Return sebagai data URL → bisa langsung jadi `<img src="...">`
res.json({ wallet: cfg.USDT_WALLET, network: cfg.USDT_NETWORK, amountUsdt, qr });
```

### Nominal Unique

```js
const base = totalIdr / (cfg.IDR_PER_USD || 16000);
const unique = Number(orderId % 1000) / 100000; // 0.00000 - 0.00999
const amountUsdt = +(base + unique).toFixed(5);
// Misal order #182, harga 48k IDR, rate 16000:
// base = 3.00, unique = 0.00182 → 3.00182 USDT
```

### Tx Hash Validation

```js
const hash = String(txHash || '').trim();
if (!/^0x[a-fA-F0-9]{64}$/.test(hash)) throw new Error('BAD_TX_HASH');

// Anti-reuse: cek hash sudah dipakai order lain
const dup = db.prepare("SELECT id FROM orders WHERE provider_ref=? AND id<>?").get(hash, orderId);
if (dup) throw new Error('TX_HASH_USED');
```

### Frontend Copy-to-Clipboard

```js
function copyText(txt) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(() => showCustomAlert(t('onchain_copied')));
  }
}
// Onclick di alamat wallet atau nominal USDT
```

### Admin Approve Flow

```js
// purchase.js
function approveOnchainOrder(orderId) {
  const o = db.prepare('SELECT * FROM orders WHERE id=?').get(orderId);
  if (!o) throw new Error('ORDER_NOT_FOUND');
  if (o.pay_method !== 'usdt') throw new Error('BAD_METHOD');
  return fulfillUsdtOrder(orderId); // reuse existing fulfillment logic
}

// telegram-bot.js
async function resolveOnchainOrder(query, id, approve) {
  const o = db.prepare(`SELECT o.*,u.name,u.username,p.name pname FROM orders o
    LEFT JOIN users u ON u.id=o.user_id LEFT JOIN products p ON p.id=o.product_id
    WHERE o.id=? AND o.status='waiting_confirm'`).get(id);
  if (!o) return api('editMessageText', { ...query.message, text: 'Already processed' });
  let creds = [];
  if (approve) {
    const r = purchase.approveOnchainOrder(id);
    creds = r.credentials || [];
  } else {
    purchase.rejectOnchainOrder(id);
  }
  await api('editMessageText', { ...query.message, text: formatBox('Order Result', [...]) });
  // Kirim kredensial ke user
  if (approve) {
    await send(o.user_id, formatBox('Order Delivered', [...]) + '\n\n' + creds.join('\n\n'));
  }
}
```

## Pitfalls

1. **QR library native deps**: `qrcode` (pure JS) works di Termux. `qrcode-terminal` atau `node-qrcode` dengan canvas native GAGAL compile. Pilih `qrcode` npm package.
2. **Tx hash format BSC/ERC-20**: `0x` + 64 hex chars. TRC-20 (Tron) beda format. Validasi regex sesuai network yang kamu support.
3. **Double server process**: kalau restart gagal kill proses lama, route baru nggak terdaftar (404) padahal syntax OK. **Solution**: `pkill -9 -f 'node server.js'` + `pgrep` verify sebelum start baru.
4. **Nominal collision**: kalau cuma pakai harga produk, 2 user beli produk sama → transfer nominal sama → admin bingung mana yang mana. **Solution**: tambahin unique mikro dari order ID (5 desimal terakhir).
5. **Network warning must be LOUD**: salah network (kirim USDT ERC-20 ke alamat BSC) = **dana hilang permanen**. Tampilkan warning dengan warna merah + bold + emoji ⚠️.
6. **Provider_ref collision**: kolom `provider_ref` di `orders` table biasanya simpan invoice ID (NOWPayments). On-chain, simpan tx hash. Kalau mixed mode (NOWPayments + on-chain), beda prefix (misal `nowp:123` vs `onchain:0x...`) atau pisah kolom.
7. **Admin panel web vs bot**: bot pakai inline keyboard (callback query), admin panel web pakai HTTP POST. Endpoint `/api/admin/order/:id/approve` harus distinct dari bot callback `ord_approve_`.
8. **Frontend hardcoded minimum**: saat ganti payment provider (NOWPayments → on-chain), jangan lupa update `USDT_MIN` constant di frontend. Kalau masih hardcode `var USDT_MIN = 12;` (NOWPayments limit), user akan kena warning "Minimum USDT payment is 12 USDT" meski backend sudah nggak pakai gateway lagi. **Solution**: set `USDT_MIN = 0` saat migrate ke on-chain manual-verify.
8. **Polling stop condition**: jangan cuma cek `status === 'delivered'`, cek juga `status === 'rejected'` biar user tahu kalau ditolak (dan bisa contact admin).

## User Experience Tips

- **QR code size**: 260px cukup buat scan di mobile, jangan terlalu kecil (unreadable) atau terlalu besar (scroll annoying)
- **Alamat wallet**: clickable → copy to clipboard + toast "Disalin"
- **Nominal USDT**: clickable → copy (biar user bisa paste exact ke wallet app)
- **Tx hash input**: placeholder "Paste Transaction Hash (0x...)" + auto-trim whitespace
- **Polling feedback**: "Menunggu verifikasi..." + emoji ⏳ (jangan silent polling, user nggak tahu app lagi ngapain)
- **Timeout**: stop polling setelah 15 menit (180 tries × 5 sec) → kasih button "Cek Status" manual refresh
- **BSCScan link**: (optional) tampilkan link BSCScan buat user cek sendiri status tx mereka

## Admin Verification Checklist

Saat admin dapat notif order USDT:

1. Klik tx hash → buka BSCScan
2. Cek **To address** = wallet kamu
3. Cek **Token** = USDT (contract 0x55d398326f99059fF775485246999027B3197955 di BSC)
4. Cek **Amount** = nominal di notif (misal 3.00182 USDT)
5. Cek **Status** = Success (hijau ✓)
6. Kalau semua OK → tap **Approve**
7. Kalau salah (nominal beda / wrong token / belum masuk) → tap **Reject** + DM user jelasin

## Testing Without Real Money

1. **Mock flow**: hardcode order jadi `waiting_confirm`, lalu test approve/reject dari bot
2. **Testnet**: pakai BSC Testnet + faucet USDT (tapi user harus switch network juga)
3. **UI-only test**: tampilkan layar pembayaran, jangan submit tx hash (cukup screenshot UI)
4. **Tx hash validation**: test regex dengan sample hash valid/invalid

## When to Use This vs. Payment Gateway

| Kriteria | On-Chain Manual | Gateway (NOWPayments/Xendit) |
|----------|----------------|------------------------------|
| Setup complexity | Low (cuma wallet address) | Medium (daftar merchant, KYC) |
| Minimum transaksi | Berapapun (misal 0.5 USDT) | $12+ (NOWPayments) |
| Fee | Gas fee user bayar sendiri | Gateway fee 0.5-2% |
| Auto-confirm | No (manual verify) | Yes (webhook callback) |
| Fraud risk | Admin verify manual | Gateway handle |
| Best for | Volume rendah, trust-based | Volume tinggi, fully automated |

On-chain manual cocok buat:
- Toko kecil/personal (< 50 transaksi/hari)
- Buyer trusted (komunitas/repeat customer)
- Nggak mau ribet daftar merchant account
- Mau kontrol penuh tanpa pihak ketiga

Gateway cocok buat:
- Toko besar (ratusan transaksi/hari)
- Open to public (nggak kenal buyer)
- Butuh instant delivery (nggak bisa manual 24/7)
- Mau auto-refund/dispute handling
