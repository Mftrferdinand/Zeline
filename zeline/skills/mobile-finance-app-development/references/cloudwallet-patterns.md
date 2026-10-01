# Obscura / Mobile Finance Implementation Patterns

This reference records reusable technical patterns from a React/TypeScript + Express + `node:sqlite` personal-finance app. Adapt brand names and paths; do not copy credentials or user data.

## Architecture

- Frontend: React, TypeScript, Vite, Zustand, Lucide icons.
- Backend: Express, `node:sqlite`, user scoping via authenticated Telegram/dev user ID.
- Accounts: `bank | ewallet | exchange | wallet` with `IDR | USDT | USD`.
- Transactions: legacy `income | expense | transfer`, with a simpler UI mapped to receive/send/internal transfer.
- Notes: text/checklist, table JSON, sketch JSON.
- Wishlist: target amount, saved amount, linked funding account, savings history, logo styling.

## Idempotent SQLite Migration

```js
function addColumnIfMissing(table, column, def) {
  const cols = db.prepare(`PRAGMA table_info(${table})`).all();
  if (!cols.some((c) => c.name === column)) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${def}`);
  }
}

addColumnIfMissing('transactions', 'counterparty', "TEXT DEFAULT ''");
```

Run migrations on server startup and restart the actual server process after editing.

## External Send Endpoint

Input:

```json
{
  "accountId": 1,
  "amount": 27,
  "destination": "0x... or account/name/phone",
  "note": "optional"
}
```

Core validation:

```js
const amt = Math.round(Number(amount));
if (!Number.isFinite(amt) || amt <= 0) reject('invalid_amount');
if (!destination?.trim()) reject('destination_required');
if (account.balance < amt) reject('insufficient_balance');
```

Atomic write:

```js
tx(() => {
  db.prepare('UPDATE accounts SET balance=balance-? WHERE id=?').run(amt, account.id);
  db.prepare(`INSERT INTO transactions
    (user_id,account_id,type,amount,note,counterparty,occurred_at,created_at)
    VALUES (?,?,?,?,?,?,?,?)`)
    .run(userId, account.id, 'expense', amt, note || '', destination.trim(), ts, ts);
});
```

The strict comparison matters: `balance < amt`, not `balance <= amt`.

## Receive Endpoint

Receive mirrors send but adds the amount and stores optional `source` in the same counterparty field. This keeps history display simple while retaining legacy transaction types.

## Tight Regression for a Balance Bug

Create a disposable database/user, then:

1. Create account with balance `27`.
2. Send `27` externally.
3. Assert HTTP 200 and balance `0`.
4. Receive `27`.
5. Assert HTTP 200 and balance `27`.
6. Run all other transaction tests.

Repeat the same flow against the live local HTTP server using a disposable user ID, then delete that user.

## Transaction UI

Use three explicit account-detail shortcuts:

```ts
const TYPE_OPTIONS = [
  { key: 'receive', label: 'Menerima' },
  { key: 'transfer', label: 'Transfer' },
  { key: 'expense', label: 'Keluar' },
];
```

- `Menerima`: selected account + optional source.
- `Transfer`: internal destination selector and optional currency conversion.
- `Keluar`: free text for merchant, bank account, phone, person, or wallet address.
- `Gunakan semua saldo`: fills the exact current balance for Transfer/Keluar.

Display current account and formatted available balance before submission.

## Wishlist Funding Ledger

Persist `linked_account_id` on the goal and `funding_account_id` on each savings event. Deposit/withdraw writes must be atomic with account balance updates:

```js
tx(() => {
  // deposit
  updateFunding(-amount);
  updateWishlist(+amount);
  insertSaving(+amount, fundingId);
});

tx(() => {
  // withdraw
  updateWishlist(-amount);
  updateFunding(+amount);
  insertSaving(-amount, fundingId);
});
```

Validate same currency, funding balance, saved amount, and refund remaining savings when deleting the goal.

## Account Deletion with Typed Confirmation

Require `confirmName === account.name` server-side. In one transaction:

1. `wishlist_items.linked_account_id = NULL`.
2. Null `notes.linked_txn_id` for related transaction IDs.
3. Delete transactions where the account is source or destination.
4. Delete the account.

Return deleted balance and transaction count. Do not transfer the balance implicitly.

## Notes Export Patterns

Use one canonical HTML/text renderer:

- TXT: title, type, tag, body/table, checklist.
- CSV: table columns/rows when table note; otherwise section/value rows. Prefix UTF-8 BOM.
- XLS: Excel-compatible HTML produced from the canonical note renderer.
- PDF: fullscreen A4 iframe preview + browser Print/Save as PDF. Render actual table colors, checklist state, and sketch SVG paths.

Always sanitize filenames and HTML/CSV cells. Verify the PDF renderer with text, table, checklist, and sketch fixtures.

## Category Themes

For per-device themes:

```ts
interface CategoryTheme {
  primary: string;
  secondary: string;
  pattern: 'orbs' | 'mesh' | 'waves' | 'grid' | 'clean';
}
```

Store a record keyed by category kind in `localStorage`. Compose patterns with static CSS gradients; avoid animated blur/background-position on Android WebView.

## Served-Build Verification

After build:

```sh
npm run build
curl -fsS http://127.0.0.1:PORT/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
```

The served hash must match the fresh Vite build output. HTTP 200 alone is insufficient evidence that the new UI is active.
