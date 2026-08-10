# Mobile Finance App Design

> Design and implement lightweight mobile-first personal finance apps with hierarchical account navigation, transaction history, savings goals, editable branding, notes/tables, and auditable calendar history.

Use this skill when building or refining a mobile personal-finance dashboard, wallet tracker, bank/e-wallet/exchange organizer, savings-goal app, or Telegram Mini App with similar interaction patterns.

## Product Principles

1. **Start with information hierarchy, not a flat list.**
   - Home shows compact total balances and category cards.
   - Categories should represent the user's mental model, for example: Bank, E-Wallet, Exchange, Web3 Wallet.
   - Tapping a category opens a dedicated page with aggregate balance, account count, and the accounts inside it.
   - Tapping an account opens its balance, metadata, transaction controls, and history.

2. **Keep mobile space efficient.**
   - Avoid oversized cards and repeated labels.
   - Prefer two-column category cards and compact rows.
   - Put creation actions inside the relevant category/account instead of one ambiguous global floating button.
   - Long histories, tables, and canvases scroll internally; do not force the whole page to scroll for component content.

3. **No emoji in the product UI.**
   - Use one consistent vector-icon library.
   - Use SVG/CSS for crisp HD/retina rendering.
   - Do not substitute Unicode checkmarks or decorative emoji for icons.

4. **Use subtle visual treatment.**
   - Favor restrained glass, soft gradients, strong typography, and good spacing.
   - Avoid box-heavy layouts and excessive nested cards.
   - Keep animation transform-based and light for Android WebView performance.

## Account Navigation Pattern

Recommended flow:

```text
Home
  -> Category card (Bank / E-Wallet / Exchange / Web3 Wallet)
      -> Category total + account list
          -> Account detail
              -> Add transaction
              -> Internal-scroll transaction history
              -> Month/year lookup
              -> Edit account and logo
```

Category cards should show:
- category name and vector icon;
- number of accounts;
- aggregate IDR and/or USD balance;
- a clear affordance that the card opens another view.

The category page should show:
- prominent aggregate balance;
- separate currency totals rather than silently mixing currencies;
- account rows with logo, name, type/address hint, balance, and chevron;
- a category-scoped add button.

## Editable Logo System

For financial accounts, use this priority order:

1. User-defined override.
2. Automatically detected institution brand.
3. Initials fallback.

For Wishlist templates, pictogram icons are preferable to short text badges. Use a controlled local SVG/Lucide registry for recognizable goals (phone, laptop, vehicle, home, travel, game, camera, gift, education, crypto, etc.), persist a compact icon key, and keep text/color customization as an alternate custom mode.

Store explicit fields instead of baking presentation into a single image:

```text
logo_text   max 10 characters
logo_bg     validated hex color
logo_fg     validated hex color
```

Editor requirements:
- live preview;
- text counter and hard max length;
- background and foreground presets;
- unrestricted color input with hex validation;
- reset to automatic detection;
- responsive font sizing for 1-10 characters;
- same renderer everywhere: lists, detail views, selectors, and goals.

Backend requirements:
- validate `#RGB` or `#RRGGBB` only;
- slice logo text server-side as well as client-side;
- represent automatic mode with empty/null override fields;
- migrate existing databases additively.

## Savings Goals and Linked Accounts

A savings goal should clearly identify the funding account. Replace a generic select with account cards containing:
- institution logo;
- account name;
- account kind;
- current balance;
- selected-state check icon.

The funding relationship must be real ledger integration, not a decorative label:
- Goal and funding account currencies must match (IDR goal → IDR account; USDT goal → USDT account).
- **Setor** atomically decreases funding balance, increases saved amount, and appends a positive history event.
- **Tarik** atomically decreases saved amount, returns money to funding, and appends a negative event.
- Direct edits to saved amount should be disabled after creation; movement goes through Setor/Tarik.
- Deleting a goal refunds remaining savings to a surviving funding account.
- Deleting an account detaches it from goals without deleting the goals.

Keep target date separate from deposit date:
- target date answers "when do I want to buy this?";
- deposit events normally use server time and should not require a date picker unless backdating is explicitly requested;
- when the user says "hapus tanggal nabung", remove only the deposit-date picker — do not remove the goal target date or the history calendar;
- linked funding account selection should use visual account cards (logo, name, kind, balance), not an opaque native `<select>`.

## Requirement Interpretation Rules

Finance UI requests often use similar words for different concepts. Resolve them explicitly in implementation:

- **Menu awal kategori** means Home shows only category cards; account rows belong one level deeper after tapping a category. A grouped list directly on Home is not equivalent.
- **Tanggal target** is goal metadata; **tanggal nabung** is an event timestamp; **kalender history** is an audit visualization. Removing one must not silently remove the others.
- **Hapus tabungan / ambil lagi** is a signed financial correction, not ordinary record deletion, when the user expects it to remain visible in history.
- **Logo bisa diatur** means one shared persisted renderer/editor across account lists, details, selectors, and goals — not a local preview-only customization.

## Auditable Savings Calendar

Calendar requirements:
- use a recognizable real-month grid with weekday headers and month navigation;
- **soft green**: net positive deposit on that date;
- **soft red**: net negative withdrawal/reversal on that date;
- **normal/neutral**: no activity or an ordinary elapsed date with no event;
- **neutral marked**: positive and negative activity net to zero;
- clicking a marked date reveals the signed events and notes;
- completed goals must continue exposing the same calendar/history.

Critical invariant: if the calendar must show withdrawals/deletions as red, **do not hard-delete the only audit event**. Append a compensating negative event (preferred), or void the original while retaining an immutable reversal. Hard deletion removes the evidence and makes red calendar cells impossible. A UI action labeled "Hapus" may therefore need to mean "reverse this deposit" at the data layer.

Recommended event model:

```text
wishlist_savings
  id
  wishlist_id
  amount       positive deposit, negative withdrawal/correction
  note
  saved_at
  created_at
```

Aggregate by local calendar day for display. Intensity may reflect absolute net amount, but keep the base colors soft enough for readable dates. Clicking a marked day should reveal its events.

## Notes, Tables, and Sketches

For lightweight note tools:
- support text/checklists, tables, and simple vector sketches;
- allow fullscreen editing for tables and sketches;
- keep table pan/scroll/zoom inside the table container;
- support practical column types: text, number, checkbox, date;
- store table and sketch data as validated JSON with payload-size guards;
- render sketches as normalized vector strokes so they stay sharp across screen sizes.

Do not attempt a full spreadsheet or illustration suite unless requested. Prioritize useful, lightweight features.

## Implementation Workflow

1. Inspect current types, schema, routes, API client, and key UI components in parallel.
2. Change backend schema and serialization first.
3. Add additive migrations for existing databases.
4. Extend backend validation and tests.
5. Update frontend types and API client.
6. Build reusable primitives (logo renderer/editor, calendar, internal-scroll table) before pages.
7. Integrate pages and navigation.
8. Run backend syntax checks and all tests.
9. Build the production frontend.
10. Restart the Node backend after backend patches; static frontend builds may be served without restart if the server reads `dist` dynamically.
11. Verify the live HTTP endpoint serves the exact fresh hashed asset.
12. Exercise new API flows end-to-end with real requests.

## Pitfalls

- Do not claim a backend change is live before restarting a non-reloading Node process.
- Do not mix balances in different currencies into one unexplained total.
- Do not show all accounts flat on Home when the requested mental model is category-first.
- Do not implement "withdrawal shown red" by deleting a positive history row; preserve an auditable negative event.
- Do not add date pickers to every monetary action. Ask whether the date is a target, event date, or unnecessary friction.
- Do not rely only on client validation for custom logo text/colors.
- Do not use external logo network requests when a local vector/monogram system is sufficient and more reliable.

## Verification Checklist

- Category totals match account rows by currency.
- Custom logos persist and render consistently after reload.
- Invalid colors are rejected or normalized server-side.
- Linked savings account is visible and unambiguous.
- Deposit calendar is green; withdrawal/correction calendar is red; empty dates remain normal.
- Completed goals still expose their calendar/history.
- Internal-scroll components do not scroll or zoom the entire app.
- No emoji or stray Unicode icon substitutes remain in UI source.
- Production build succeeds and its hashed asset is the one served live.

Session-specific implementation notes and schema examples: `references/cloudwallet-patterns.md`.


---

## Lampiran: `references/cloudwallet-patterns.md`

# Cloudwallet Implementation Patterns

Condensed details from a React/TypeScript + Express + `node:sqlite` mobile finance app implementation.

## Useful Schema Extensions

Additive logo fields:

```sql
ALTER TABLE accounts ADD COLUMN logo_text TEXT;
ALTER TABLE accounts ADD COLUMN logo_bg TEXT;
ALTER TABLE accounts ADD COLUMN logo_fg TEXT;
ALTER TABLE wishlist_items ADD COLUMN logo_text TEXT;
ALTER TABLE wishlist_items ADD COLUMN logo_bg TEXT;
ALTER TABLE wishlist_items ADD COLUMN logo_fg TEXT;
```

Savings audit history:

```sql
CREATE TABLE IF NOT EXISTS wishlist_savings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  wishlist_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  amount INTEGER NOT NULL,
  note TEXT DEFAULT '',
  saved_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY (wishlist_id) REFERENCES wishlist_items(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

## Logo Validation

```js
function cleanLogoText(value) {
  return value == null ? null : String(value).slice(0, 10);
}

function cleanHex(value) {
  if (value == null) return null;
  const s = String(value).trim();
  return /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(s) ? s : null;
}
```

## Calendar Aggregation

Group by local day and sum signed events:

```ts
const byDay = new Map<string, number>();
for (const event of savings) {
  const d = new Date(event.savedAt);
  const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  byDay.set(key, (byDay.get(key) || 0) + event.amount);
}
```

Use soft RGBA fills:
- positive: `rgba(34,197,94,alpha)`;
- negative: `rgba(239,68,68,alpha)`;
- empty: `rgba(0,0,0,0.03)`.

The maximum absolute daily amount can scale alpha. Clamp the range so low values remain visible and high values keep date text legible.

## Important Correction Semantics

A deletion endpoint that physically removes a deposit event and subtracts it from the goal balance cannot later show that date as red; the event no longer exists. For red correction history, use one of these:

1. append a compensating negative event and retain the original;
2. mark the original event void and emit a linked reversal event;
3. keep immutable events with `event_type` (`deposit`, `withdrawal`, `reversal`).

Option 1 is simplest for a personal finance tracker. Treat a UI action labeled "hapus setoran" as a reversal when the user expects the date to turn red in the calendar.

## UI Requirement Mapping

| User phrase | Implement as | Common wrong interpretation |
|---|---|---|
| "menu awal Bank / E-Wallet / Exchange / Web3 Wallet" | Home has four category cards; tapping one opens aggregate + accounts | Four grouped account sections directly on Home |
| "hapus tanggal nabung" | Remove deposit event date input; server timestamps deposit | Remove target purchase date or calendar history |
| "yang sudah tercapai ada kalender" | Completed goals remain navigable and retain event history | Hide detail because deposit CTA disappears |
| "kalau dihapus/diambil jadi merah" | Append signed negative reversal/withdrawal | Hard-delete deposit event |
| "atur logo" | Persist text/background/foreground and reuse one renderer everywhere | Preview-only customization or per-screen fields |

## Cloudflare Named Tunnel on Termux

For a custom domain backed by the app's local server:

1. Verify the local origin and `/health` first.
2. Inspect `~/.cloudflared/config.yml`; confirm the hostname routes to the actual app port. If Express serves both API and built frontend, do not start an unrelated static server for the public hostname.
3. `HTTP 530` usually means DNS exists but the named tunnel has no live connection.
4. Start it with `cloudflared tunnel --config ~/.cloudflared/config.yml run <name>`.
5. Wait for `Registered tunnel connection`, then verify public HTTPS root and `/health` separately.
6. On Android Termux, `ping_group_range` permission warnings only disable ICMP proxying. If QUIC connections register and HTTPS returns 200, the tunnel is healthy.

## Live Verification Pattern

After frontend build, verify the server serves its exact fresh hash:

```sh
npm run build
curl -fsS http://127.0.0.1:PORT/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'
```

After backend changes, restart Node first. Then POST real account/goal/events and GET them back. Verify:
- logo fields round-trip;
- invalid hex does not persist;
- linked account ID round-trips;
- saving event gets a valid server timestamp;
- aggregate balance and progress update correctly.
