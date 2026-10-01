---
name: telegram-commerce-bot
description: Build full-auto Telegram shop bots with QRIS payment (Tripay), auto delivery, and stock management. Use when user wants a bot that sells digital products (Netflix accounts, ChatGPT keys, Spotify, etc.) with automated payment detection.
tags: [telegram, bot, commerce, payment, qris, tripay, shop]
version: 1.0.0
author: Zeline
---

# Telegram Commerce Bot

Pattern: Telegram bot that sells digital goods → generates QRIS → auto-detects payment → delivers credentials. Full autonomous, no manual admin verification.

## When to Use

- User wants a Telegram bot to sell digital products (subscription accounts, keys, licenses)
- Payment must be automated (no manual confirm by admin)
- QRIS is the preferred payment method
- Delivery is immediate after payment detected
- Stock/catalog management from within Telegram

## Architecture

```
User → Bot (pick product) → Tripay API (generate QRIS)
  → User scans & pays
  → Tripay webhook (or poll) → Bot detects PAID
  → Bot auto-delivers credential from stock DB
```

Three layers:

```
iniochobot/
├── bot.py               # Telegram handlers + catalog UI
├── tripay_gateway.py    # Tripay API wrapper (QRIS create, check, verify callback)
├── webhook_server.py    # Optional: HTTP server for Tripay callback (production)
├── database.db          # SQLite: orders + stock (auto-created)
```

## Tripay Integration

**Setup:** Daftar di [tripay.co.id](https://tripay.co.id) → dapat 3 credentials:
- `API_KEY` — Bearer token for API calls
- `PRIVATE_KEY` — HMAC-SHA256 signing key
- `MERCHANT_CODE` — Format `Txxxxx`

**Sandbox vs Production:**
- Sandbox: `https://tripay.co.id/api-sandbox`
- Production: `https://tripay.co.id/api`

**Setup templates (paths relative to this skill directory):**

```bash
mkdir -p "$HOME/shopbot"
cp templates/bot-template.py "$HOME/shopbot/bot.py"
cp templates/tripay-gateway.py "$HOME/shopbot/tripay_gateway.py"
cd "$HOME/shopbot"
python3 -m venv .venv
.venv/bin/python -m pip install 'python-telegram-bot>=20,<23' requests
# Configure environment credentials before starting; no live transaction for verification.
.venv/bin/python -m py_compile bot.py tripay_gateway.py
```

The underscore destination `tripay_gateway.py` is mandatory for the bot's import.
Use the complete module in `templates/tripay-gateway.py`, not a partial inline duplicate.
It defaults to sandbox. Confirm enabled payment channels with the merchant; do not infer
channel semantics from `QRIS`/`QRISC` names alone.

**Callback contract:** Per https://tripay.co.id/developer, pass the exact request body
bytes, the `X-Callback-Signature` header and private key to
`TripayPayment.verify_callback(raw_body, signature, private_key)`. Verify before JSON
parsing; never pop a signature field or reserialize JSON. Signature validity alone is
not authorization: require the expected callback event and `PAID`, match stored
reference, merchant_ref and amount, and deduplicate before fulfillment.

## Bot Flow (Inline-Keyboard UX)

1. `/start` → catalog with category buttons (Netflix, AI, Music, etc.)
2. Pick category → product list with prices
3. Pick product → **generate QRIS via Tripay** → send QR image as `send_photo`
4. User scans & pays
5. User taps "Cek Status" → bot polls Tripay → if `PAID`, deliver

**Key UX pattern:** Send QR as photo attachment (not inline in edit), then edit the original message to show instructions. This avoids the 64-byte callback data limit and the QR image being lost on message edit.

```python
# Send QR as separate photo
await context.bot.send_photo(
    chat_id=query.message.chat_id,
    photo=qris['qr_url'],
    caption=f"📦 Order #{order_id}\nScan QRIS di atas"
)
# Edit original message
await query.edit_message_text(
    f"QRIS dikirim 👆\nScan & bayar. Kredensial otomatis.",
    reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Cek Status", callback_data=f"check_{order_id}")],
        [InlineKeyboardButton("⬅️ Batal", callback_data="home")],
    ])
)
```

## Auto-Delivery

Use `await deliver_order(order_id, context.bot)` from `templates/bot-template.py`.
Only a verified payment may transition `pending` → `paid`. The template checks
buyer ownership, reference and exact gateway total (`payment_amount`, including fees)
before doing so. Legacy rows with no saved total fail closed and need reconciliation;
never infer their payable amount from catalog price. Delivery reserves
stock in `BEGIN IMMEDIATE`, awaits the Telegram send, then marks `done`.
A failed send retains the same credential for that buyer (`delivery_failed`);
retries never allocate another credential. A crash leaving `delivering` requires
operator reconciliation before resetting to `delivery_failed`. Telegram and SQLite
cannot provide exactly-once delivery across a crash: a retry can repeat the same
message, but must not consume another stock item. Never release exposed stock.

This is a starter manual-check flow, not a production webhook/outbox service.
Before production, persist merchant_ref before API creation, reserve inventory at
checkout, reconcile uncertain creates, enforce private chats and rate limits,
protect the credential DB with owner-only permissions, and configure durable
payment polling/webhooks. Do not claim full automatic detection from this template.

## Payment Detection: Poll vs Webhook

**Poll (simpler, no public URL needed):** Customer taps "Cek Status" → bot calls `check_payment()` → if PAID, deliver. Good for starting. No webhook server needed.

**Webhook (production, faster):** Tripay POSTs callback to your server → verify signature → deliver instantly. Need public HTTPS URL. Use `webhook_server.py` (http.server on port 8085, expose via tunnel/reverse proxy).

## Stock Management

Admin-only commands from within Telegram:

```
/addstock_<NamaProduk>_<kredensial>
/stok                          — lihat stok tersedia
```

Products in `PRODUCTS` dict must match exactly the string used in `/addstock_`.

## Database Schema

```sql
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, username TEXT,
    product TEXT, price INTEGER,
    status TEXT DEFAULT 'pending',  -- pending, done, rejected
    tripay_ref TEXT, created_at TEXT, paid_at TEXT
);

CREATE TABLE stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT, credential TEXT,
    status TEXT DEFAULT 'available',  -- available, used
    used_by INTEGER
);
```

## Pitfalls

- **No live payments for verification.** Use offline fixtures and the documented sandbox workflow. Production transactions require explicit user authorization.
- **Callback data 64-byte limit** — don't put product names or long IDs in `callback_data`. Use short codes (e.g., `netflix_1m`) mapped in a dict.
- **`deliver_order` is async:** await it and handle False; never announce delivery merely because a task was scheduled.
- **Product name matching** — stock name in `/addstock_` must match `PRODUCTS[name]['name']` exactly. Use exact copy from the products dict.
- **Termux background process** — bot polls Telegram, never exits. Use `terminal(background=true, notify_on_complete=false)` for daemon mode. Do NOT set `notify_on_complete=true` — it will never "complete."
- **Payment channel:** select an enabled merchant channel from Tripay's current API documentation; do not guess QRIS code semantics.
- **Callback signature:** use the X-Callback-Signature header and unchanged raw bytes, not a JSON signature field.
- **Duitku as alternative to Tripay:** Users may prefer Duitku over Tripay. Same pattern: register at duitku.com → get API key → generate QRIS → poll/callback. If the user's Duitku account is not yet approved, build the bot with mocked payment functions and swap in Duitku later. Keep `processDeposit()` as the single integration point.
- **Secrets:** use environment variables; do not print tokens or bypass secret detection. Use dummy values in offline tests.

## See Also

- `templates/bot-template.py` — starter commerce bot skeleton (copy, set TOKEN + Tripay keys, done)
- `templates/tripay-gateway.py` — standalone Tripay payment module
- `telegram-inline-picker` — inline-keyboard pattern used for the product catalog UI
- `telegram-gateway-setup` — group authorization and mention gating if bot is used in groups
- `telegram-mini-app` — if the user wants a **visual UI** (web app inside Telegram WebView) instead of inline-button-only bot. Commerce Mini App pattern (deposit credit, product grid, buy flow, profile tabs) with vanilla HTML/CSS/JS — no build step needed. Payment gateway can be mocked during dev, swapped to Duitku/Tripay when ready.
