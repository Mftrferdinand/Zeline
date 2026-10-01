---
name: mobile-finance-app-development
description: Build and refine compact mobile-first finance apps with reliable money movement, grouped account navigation, customizable branding, structured notes, exports, and Android WebView-friendly UI.
version: 1.0.0
author: Zeline Agent
license: MIT
platforms: [linux, android, web]
tags: [finance-app, mobile-ui, react, node, transactions, notes, export, webview]
metadata:
  zeline:
    created_by: agent
---

# Mobile Finance App Development

Use this skill when building or iterating on a mobile finance, wallet, budgeting, banking, or personal-ledger web app—especially React/TypeScript frontends with a Node/SQLite backend.

## User-Facing Design Rules

For this user's finance apps:

- Use Indonesian UI copy.
- Do not use emoji. Use a consistent vector icon set.
- Keep the interface compact; do not let hero panels, buttons, or cards consume unnecessary vertical space.
- Avoid flat lists of every account on the home screen. Start with account classes such as **Bank**, **E-Wallet**, **Exchange**, and **Web3 Wallet**; open a class to show its aggregate balance and accounts.
- Prefer subtle glass/gradient treatments over heavy boxes.
- Lists, histories, spreadsheets, and calendars must scroll inside their own bounded panel rather than scrolling the entire screen.
- Keep important actions minimal and contextual. For example, place transaction controls inside account detail instead of a large global floating button.
- Treat exact wording, placement, dimensions, and labels as product requirements, not suggestions.

## Delivery Workflow

1. Inspect current types, API client, backend routes/schema, state store, and relevant UI components.
2. Build a tight regression test for the exact requested behavior before changing production code.
3. Implement one vertical slice end to end: schema → route → API client → types → UI.
4. Run the focused regression test.
5. Run the full backend tests, frontend regression tests, TypeScript check, and production build.
6. Restart the Node server after backend code or migrations change; static frontend builds may be picked up without restart if served directly from `dist`.
7. Verify the live HTTP server serves the fresh hashed asset, not merely that `npm run build` succeeded.
8. Exercise critical flows through real HTTP requests against a disposable test user, verify resulting balances/data, then remove the test user.
9. Report real outputs only.

## Money Movement: Required Invariants

For this user's finance apps, expose **three separate contextual actions** in account detail rather than hiding them behind one ambiguous form:

- **Menerima**: adds money to the selected account and optionally records the source.
- **Transfer**: moves money atomically between two internal accounts, including currency conversion when supported.
- **Keluar**: subtracts money to a free-form external destination such as a merchant, bank account, phone number, person, or wallet address.

Do not make ordinary receive/transfer/expense depend on category selection. Categories may remain optional metadata. Keep the three actions visually distinct and pass the selected action into the form so the correct mode opens immediately.

Every money-flow implementation must prove:

- A positive amount is required.
- Sending exactly the full available balance is allowed (`balance < amount` rejects; `balance === amount` succeeds).
- Sending more than the balance is rejected with a human-readable available-balance message.
- Internal transfer changes both balances atomically.
- External expense subtracts only the source balance and records the destination in history.
- Receiving increases the destination balance and records the source when supplied.
- History labels show **Menerima**, **Transfer antar akun**, or **Keluar**, plus source/destination—not a blank category placeholder.
- Deleting a transaction restores balances consistently.
- Currency display and integer/decimal handling follow the account currency rather than a single global assumption.

Always include an exact-balance regression, e.g. balance `27`, send `27`, assert `0`; receive `27`, assert `27`.

## Account and Category Navigation

- Home shows total currency heroes and category cards.
- Currency heroes that the user expects should remain visible even at zero; do not hide USDT merely because no USDT account exists.
- Category cards show account count and aggregate totals.
- Category detail shows a clear aggregate header, account list, contextual add button, and editable visual theme.
- Account detail shows brand/custom logo, balance, address/account number when relevant, separate Menerima/Transfer/Keluar actions, and bounded transaction history.
- Do not expose archive controls when the user asked for permanent deletion instead.

### Permanent Account Deletion

Allow deletion even when the account has balance or history, but require a destructive confirmation that cannot be triggered by one accidental tap:

1. Show that the account balance and all related transaction history will be deleted permanently.
2. Require the user to type the account name exactly.
3. Validate the confirmation server-side as well as client-side.
4. In one database transaction: detach Wishlist funding links, null Notes transaction links, delete related transactions, then delete the account.
5. Return deleted balance and transaction count for audit/UI feedback.
6. Never silently move the remaining balance into another account.

Regression coverage must include wrong confirmation rejection, successful deletion of a funded account with history, Wishlist detachment, transaction removal, and account 404 afterward.

## Logos and Branding

Treat rebranding as a complete product change, not a header-only rename. Update the visible app title, loading copy, profile/version copy, export filenames, PDF footer/mark, browser title, favicon, primary logo, and backend startup message. Preserve internal database/env keys when renaming them would strand existing user data.

For financial accounts, use this priority:

1. User-defined logo text/background/foreground.
2. Automatically detected brand styling.
3. Initials fallback.

For Wishlist presets, prefer actual vector pictograms (phone, laptop, car, house, travel, gift, education, crypto, etc.) instead of text badges such as `HP` or `MOBIL`. Store a compact icon key, render it through a controlled Lucide/SVG registry, and keep custom text/color logos as a separate option.

Logo editors should provide:

- Live preview.
- Text length cap with visible counter.
- Background and foreground presets.
- Free color pickers.
- Reset to automatic detection.
- A useful pictogram library for Wishlist and brand-aware styling for financial accounts.

Keep logos vector/CSS based where practical so they stay sharp on high-density mobile screens.

## Per-Category Wallpaper Themes

Each account category may have a separate persistent theme:

- Two custom colors.
- Gradient presets.
- Lightweight patterns such as orbs, mesh, waves, grid, or clean.
- Live preview and reset.

For device-specific visual preferences, browser `localStorage` is sufficient and avoids a schema change. Use backend persistence only when the theme must follow the user across devices.

Avoid expensive animated blur/background-position effects on Android WebView. Prefer static gradients and transform-only animation.

## Currency-First Balance Navigation (Alternate to Category-First)

Some users want an even lighter-weight Home than the category-card pattern above. Signal: the user describes balance sections by **currency**, not by account type, and wants each tap to manage accounts inline without leaving Home. Pattern:

- Stack one compact row per currency directly on Home (e.g. IDR row, then USDT row below it) — not side-by-side hero cards. Order matches the order the user names them.
- Each row shows only the currency label and its aggregate balance; tapping it opens a bottom sheet scoped to that currency.
- Inside the sheet: list of accounts for that currency's relevant kinds (e.g. IDR → Bank/E-Wallet; USDT → Exchange/Web3 Wallet), each row editable/deletable inline, plus an inline add form in the same sheet (name, kind, balance). No separate page navigation is needed for simple CRUD — keep it a single sheet.
- Pemasukan/Pengeluaran get the same shallow treatment: one compact card each on Home, tapping opens a small form sheet (source/destination, amount, category, date) rather than a full page.
- Recent-transactions list stays directly on Home below these controls (5 items is a good default unless the user specifies otherwise).

Use this pattern instead of the deeper category-page pattern when the user's own spec is phrased as a flat bullet list of tappable Home elements rather than a drill-down hierarchy. Treat each bullet as a literal separate requirement (separate tap target, separate sheet) rather than merging them into fewer abstracted components.

## Category Management (Chip-Based, User-Editable)

When the user wants to define their own transaction categories instead of picking from a fixed dropdown, replace the `<select>` with an inline chip picker inside the transaction form itself:

- Render existing categories (filtered by `kind` matching the transaction type) as small rounded-pill chips that wrap with `flex flex-wrap gap-1.5` — never a boxed list, never full-width rows. This is the compact/minimal treatment this user consistently asks for.
- Selected chip gets a solid fill (`background: cat.color`); unselected chips get a tinted background (`${cat.color}18`) with the color as text — this alone communicates selection state without extra borders or checkmarks.
- Add an inline "+ Baru" chip at the end of the row. Tapping it reveals a single-line input + confirm/cancel icon buttons directly below the chip row (not a separate sheet/page) so adding a category costs one extra tap, not a navigation.
- Add a text-only "Kelola" / "Selesai" toggle next to the section label (not a button, just small text) that switches chips into delete mode: each chip grows a small circular "×" badge top-right that calls the delete endpoint. Suppress the normal select-on-tap behavior while in this mode.
- New categories should auto-assign a color from a small fixed palette (`PALETTE` array, cycle by `existingCount % palette.length`) rather than asking the user to pick a color — keep category creation to one field (name).
- Wire creation/deletion through the existing categories store action; add a `refreshCategories()` action to the global store (mirrors `refreshAccounts()`) if one doesn't exist yet, and call it after create/delete so the chip list updates without a full page reload.

This pattern generalizes beyond categories to any small user-owned taxonomy (tags, labels, wallet kinds) where the alternative would be a heavier dropdown or a full CRUD settings page the user didn't ask for.

## Monthly Transaction History Navigation

When the user asks for history that "follows the calendar" and can page through past months, build a dedicated component (not a flat list) with:

- State: `year`/`month` (0-11), defaulting to `new Date()` — always open on the real current month, not the most recent data month.
- Prev/next month arrow buttons flanking a `"{MonthName} {Year}"` label. Disable (don't hide) the next-month button once `year === now.getFullYear() && month === now.getMonth()` — the user should never be able to navigate into the future; only Prev should ever be clickable at the boundary.
- Fetch transactions scoped to `[startOfMonth, endOfMonth]` for the selected year/month via the existing list endpoint's `from`/`to` params — do not fetch everything and filter client-side.
- Group results by calendar day (`YYYY-M-D` key) and render a small date-label header per group (e.g. `"Sen, 27 Oktober"`) above that day's transactions, most recent day first. This reads as a proper ledger rather than an undifferentiated feed.
- Show a small income/expense mini-summary strip above the grouped list, computed client-side from the fetched month's transactions (don't add a new endpoint just for this).
- Reuse the existing transaction detail sheet/delete flow on row tap so deleting a transaction from monthly history stays consistent with deleting it anywhere else in the app.

Use this component to replace any "last N transactions" static list once the user asks to browse history by month — a flat recent-N list and a monthly calendar-scoped view are different requirements; don't try to make one component do both by adding a mode flag unless asked.

## Home Screen Decluttering (Move Secondary Content Off Home)

When the user says a section takes up too much space on the main screen and asks for it to become its own menu (e.g. "history transaksi jangan ditampilin di menu utama, buat menu baru kalo klik"), don't shrink or collapse it in place — extract it into a dedicated page:

- Keep the existing content component (e.g. `MonthlyHistory`) completely unchanged; only its host container changes. Wrap it in a new page component (e.g. `HistoryPage`) with its own back button and header.
- On the page the content was removed from, leave exactly one compact entry-point row/button: icon badge + label + a live one-line summary (e.g. transaction count this month) + trailing chevron. This preserves discoverability without the vertical space cost.
- Wire navigation with simple local state in the app root (e.g. `showHistory` boolean) rather than a router if the app doesn't have one already — toggle it from the entry-point button's `onClick` and from the new page's back button, and reset it in the bottom-nav tab-change handler so switching tabs always exits the sub-page.
- This pattern generalizes to any "X is taking over the home screen, give it its own menu" request — budget tracker, category management, export options, etc.

## Fluid, Non-Rigid Visual Language ("Jangan Kaku")

Signal: the user says a design is "kaku" (rigid/stiff) or gives blunt unspecific negative feedback ("jelek bgt", "jelek anj") about a UI that uses flat colored boxes, symmetric grids, or a plain white card wall. This user (aes) reacts to boxy/grid-like layouts across multiple projects — treat it as a durable style signal, not a one-off complaint. The fix is not more polish on the same boxes; it's replacing rigid rectangles with organic, overlapping, gradient-driven shapes:

- **Hero card over stacked boxes**: combine what used to be 2 separate white glass cards (e.g. IDR balance + USDT balance) into one dark-gradient hero card, e.g. `background: linear-gradient(145deg,#1c1533,#2c1a52,#1a1130)` with a matching `boxShadow` glow, `rounded-[32px]`. Separate the two pieces of content inside it with a `h-px bg-white/10` hairline instead of a card boundary.
- **Decorative blobs, not empty corners**: place 1-2 `absolute rounded-full` divs sized ~120-200px inside the hero card, `background: radial-gradient(circle,#accentColor55,transparent 70%)`, positioned with negative offsets (e.g. `top:-70,right:-50`) so they bleed off the edge. This alone reads as "not a plain rectangle" without adding real complexity.
- **Overlapping icon badges instead of centered icon boxes**: for secondary metric cards (income/expense, stat pills), give the icon badge `absolute -top-3 -left-3 w-16 h-16 rounded-full` with a gradient fill, so it visually pokes outside the card's own boundary rather than sitting centered and symmetric inside it. Nudge the inner icon off-center with margin (`ml-2 mt-2`) to match the badge's offset.
- **Pill switchers over button rows**: month/period switchers, category pickers, and mode toggles should be one `rounded-full` or `rounded-[22px]` pill container with icon buttons inside, not a row of square buttons with visible seams.
- **One entry-point row over a data table**: when summarizing something that has its own detail page (see Home Screen Decluttering above), a single glass row with a circular icon badge reads far softer than a boxed stat card.

Apply this whenever the user's own wording contains "kaku", "jangan kotak-kotak", "biar cair", or similarly blunt negative aesthetic feedback ("jelek") with no further spec — that phrasing IS the spec; proceed straight to a redesign pass using the patterns above rather than asking what specifically looks wrong.

## Soft Color Palette + True Liquid Glass Buttons

Two distinct corrections that often arrive back-to-back after an initial rebrand/redesign pass: (1) "warna soft semua, jangan mencolok/aneh-aneh" (desaturate — the brand color itself, e.g. a punchy chartreuse `#BDF424`, is too loud for a finished product even though it's correct for a logo), and (2) "semua tombol pakai liquid glass, jangan kaku" (every button, not just the hero/cards, must use the frosted-glass treatment).

**Desaturating a punchy source palette for UI:** when a reference logo/brand color is vivid (sampled via PIL pixel-read, see Rebranding section), do not use it verbatim as button/text/background color. Derive a muted sibling for actual UI surfaces: keep the hue family, drop saturation and lift/lower lightness, e.g. `#BDF424` (loud chartreuse) → `#a3e635`/`#d9f99d` for faint decorative blobs only, and a desaturated forest tone like `#3a5a45` for text/solid accents and `#4f9d6e`/`#d9727f` (muted sage/dusty-rose, not `#16a34a`/`#e11d48`) for income/expense semantic colors. The vivid source color is correct for the logo mark itself (keep it as-is for the actual logo image/icon) but should not reappear at full saturation anywhere else in the UI.

**Building reusable liquid-glass button classes** (add once to the global CSS, then apply everywhere — don't hand-roll `backdrop-filter` inline per button):

```css
.btn-glass {           /* icon-only circular buttons: back, close, chevron nav */
  background: rgba(255,255,255,0.5);
  backdrop-filter: blur(14px) saturate(150%);
  -webkit-backdrop-filter: blur(14px) saturate(150%);
  border: 1px solid rgba(255,255,255,0.65);
  box-shadow: 0 2px 10px rgba(58,90,69,0.08), inset 0 1px 0 rgba(255,255,255,0.7);
}
.btn-glass-brand {     /* primary CTA: save, FAB, active nav tab */
  background: linear-gradient(155deg, rgba(107,143,90,0.92), rgba(58,90,69,0.96));
  backdrop-filter: blur(14px) saturate(150%);
  border: 1px solid rgba(255,255,255,0.22);
  box-shadow: 0 6px 18px rgba(58,90,69,0.28), inset 0 1px 0 rgba(255,255,255,0.25);
}
.chip-glass {           /* pills: filters, category chips, dropdown triggers */
  background: rgba(255,255,255,0.55);
  backdrop-filter: blur(10px) saturate(140%);
  border: 1px solid rgba(255,255,255,0.6);
}
```
For buttons whose color must vary per instance (e.g. an income-vs-expense submit button using `accentColor`), don't fall back to a flat `style={{background: accentColor}}` — build the same glass recipe inline: `background: linear-gradient(155deg, ${accentColor}cc, ${accentColor}f2)`, plus `backdropFilter: 'blur(14px) saturate(150%)'` and a translucent white border. This keeps every button glass even when the base classes can't cover a dynamic color.

**Top-to-bottom brand gradient background**, when asked for "gradasi dari atas ke bawah, atas warna X bawah putih": this is a distinct ask from decorative corner blobs — set it directly on the fixed background layer, e.g. `background: linear-gradient(180deg, #cfe8a8 0%, #e3f0cf 22%, #f4f8ec 42%, #ffffff 65%, #ffffff 100%)`, and keep the soft blurred blobs as an additional accent layered on top, not a replacement for the vertical gradient.

**Scope the sweep to the whole app, not the last-touched screen.** When the user says "semua tombol" / "semua warna" (ALL buttons/ALL colors), that means every screen, including ones not currently in view: sheets, delete-confirmation buttons, bottom nav active state, FABs, secondary CTAs in edit/delete flows. `search_files` for the old hardcoded patterns across `src/` in one pass — `bg-blue-600`, `bg-red-600`, `bg-black/5`, `bg-black/6`, and any `style={{ background: '#hex' }}` — and convert each hit to the matching glass class or inline glass recipe before reporting done. Missing a screen (e.g. the destructive "Hapus Permanen" button in an account-edit sheet, or the FAB in a secondary tab) is exactly the kind of gap that triggers the user to repeat the same instruction with more emphasis ("semua tombol") in a follow-up turn — treat any second "make it all X" request as a signal the first pass under-scoped the sweep.

## Rebranding From a Reference Logo Image

When the user attaches a logo/icon image and says "buat brand ini, ubah tampilan sesuai warna ini" (build the brand from this, change the look to match these colors), treat it as a full rebrand task, not just a palette swap:

1. **Extract exact colors programmatically, don't eyeball them.** Load the image with PIL/Pillow via `execute_code` and sample pixel colors directly (e.g. `img.getpixel((x,y))`, or grid-sample with a `Counter` to find the dominant background/foreground colors). `vision_analyze` can time out on close-color-reading tasks — pixel sampling is faster and exact.
2. **Reuse the actual reference image as the logo asset — do not redraw the mark as SVG/CSS.** Copy the source image into the frontend's `public/brand/` (or equivalent static asset) folder and reference it via `<img>` in the Logo component. Recreating a custom letterform in SVG risks drifting from what the user actually approved; the literal file is the ground truth.
3. **Propagate the palette everywhere, not just the header.** Grep for the old accent color's Tailwind utility classes across `src/` (e.g. `blue-600`, `blue-500`, `violet-*`) and replace every hit — buttons, active nav state, FAB, avatar gradients, chip highlights, sheet CTAs. A rebrand that leaves old accent colors on secondary buttons reads as unfinished.
4. **Update the theme file's CSS variables and background**, not just component-level colors — `--color-*` tokens, `body`/`#root` background, decorative blob gradients, `theme-color` meta tag, `<title>`, and the favicon file.
5. **Rename the product everywhere a rebrand implies**, matching the "Logos and Branding" section above: page title, header wordmark, profile/about footer version string, export filenames, backend startup log line. Comments-only mentions of the old name are lower priority but worth a pass.
6. Rebuild and restart per the standard workflow, then verify the built `dist/` actually contains the copied image asset (not just that the build succeeded) — `ls dist/brand/` or equivalent.

## Generating a Brand-New Logo From a Text Description (No Reference Image)

When the user asks to change the look to a named palette/mood but does NOT attach a reference image (e.g. "perbagus desainnya, liquid glass, premium, warna putih hitam biru oren silver, logo juga ubah jadi biru atau oren yang bagus"), you must *create* the logo asset, not copy one. Draw it programmatically with PIL/Pillow via `execute_code` and write the PNG straight into `public/brand/` + `public/favicon.png`:

- **App-icon silhouette = superellipse (squircle), not a plain rounded rect.** Build a squircle mask by sampling a superellipse (`x = cx + a*sign(cos t)*|cos t|^(2/p)`, `p = 2n`, `n≈4`) into an `L` mask and `paste(..., mask)`. A `border-radius` rounded rectangle reads as generic; the squircle reads as a real iOS/Android icon.
- **Fill with a vertical brand gradient** (draw line-by-line into an RGBA image), then clip through the squircle mask.
- **Keep the mark literal and legible.** For a letterform, draw actual strokes (`d.line(apex→leg, width=lw)` + a filled ellipse at the apex to round the join) rather than a font glyph — fonts may be missing on Termux and a hand-built letter stays crisp at any size.
- **Iterate with `vision_analyze` as a critic.** After saving, load the PNG back through `vision_analyze` and ask "is the letter readable / does it look premium". Act on the feedback: this session's first pass had a glossy skeuomorphic sheen + too-bright orange that `vision_analyze` flagged as "dated / utilitarian" — the fix was dropping the sheen (flat = more premium) and deepening the amber slightly. One critique-and-revise loop measurably improves the result.
- Save a 512px master to `public/brand/<icon>.png` and a `resize((180,180), LANCZOS)` copy to `public/favicon.png` in the same script.

## Premium Palette Swap (White / Black / Blue / Orange / Silver)

A recurring aes request is to take an existing (often green or otherwise "norak"/gaudy) finance UI and make it "premium" with a white-black-blue-orange-silver scheme. Concrete values that worked and read as premium:

- **Primary blue** `#0A84FF` (iOS system blue), **deep navy** `#0A2540` for the hero card gradient (`linear-gradient(150deg,#0d3a6b,#0A2540,#081b30)`) and section titles.
- **Orange amber** `#FF9500` as the ACCENT only — decorative orb + logo rungs, not large fills. Keep its background-orb opacity very low (~0.10) so it's a warm hint, not a block of color.
- **Silver-blue background wash**, not white: `linear-gradient(180deg,#eaf1fb,#eef3fa,#f5f8fc,#ffffff)` on the fixed bg layer, with two low-opacity blurred orbs (blue ~0.16, orange ~0.10) bleeding off the corners.
- **Glass shadows go silver-navy**, not the old hue: `box-shadow: 0 6px 22px rgba(10,37,64,0.07)`.
- Income = blue (`#0A84FF`), expense stays red-orange (`#FF453A`) — do NOT leave income green when swapping away from a green brand.
- Update `telegram.ts` `setHeaderColor`/`setBackgroundColor` too (`#0A2540` / `#ffffff`) — the Telegram WebView chrome is part of the "premium" impression and is easy to miss.

Same full-sweep discipline as the reference-image rebrand applies: `search_files` every old hardcoded hex (`#3a5a45`, `#4f9d6e`, etc.) across `src/` in one pass and convert each hit, including secondary pages (Profile avatar gradient, MonthlyHistory summary strip, Notes fullscreen background) — not just Home.

## Premium Glassmorphism Redesign (Multi-Color, Not Just Blue)

Signal: "buat jadi glassmorphism", "premium", "warna bebas sebagusnya jangan cm biru", "tombol-tombol UI juga harus bagus", often with "rombak semua dari awal". This is a full visual-system pass, not a tweak. Concrete patterns that landed well for aes:

- **Multi-color aurora mesh background, not a single-hue wash.** When the user explicitly says "jangan cm biru", layer several low-opacity radial tints in the fixed bg plus 2-3 blurred floating orbs in DIFFERENT hue families — e.g. violet `#7C5CFC`, blue `#5B7CFA`/`#2F80FF`, teal `#22C3A6`, amber `#FF9500`. Keep each orb opacity ~0.08-0.14 so it's an ambient glow, not a color block. A blue-only palette is exactly what triggers the "jangan cm biru" complaint — spread the accents across cards (income tile blue, expense tile coral, quick-action icons each a different gradient).
- **Refine the glass recipe itself**: give `.glass`/`.glass-strong` a `::before` sheen — `radial-gradient(130% 60% at 12% 0%, rgba(255,255,255,0.55), transparent 58%)` — so cards catch light on the top-left edge. Any content inside then needs `relative z-10` to sit above the sheen pseudo-element (easy to forget → text renders under the highlight and looks washed out).
- **Build reusable gradient button + menu classes once in global CSS**, then apply everywhere (don't hand-roll per button):
  - `.btn-grad-violet` — glossy `linear-gradient(135deg,#7C5CFC,#5B7CFA,#2F80FF)` primary CTA / FAB / active nav tab, `rounded-2xl` (squircle, not circle) with a colored glow shadow.
  - `.seg-menu` + `.seg-item` — segmented pill row for quick actions (Riwayat / Rekap / Masuk / Keluar), each item a small `seg-ico` gradient tile (different gradient per action) above an 11px label.
  - `.action-tile` — 38px `rounded-[13px]` gradient icon square with inner-shadow, used for income/expense card icons and empty-state badges.
- **Font/layout hierarchy pass**: bump hero balance to ~30px extrabold, section titles to 14-18px extrabold with `tracking-tight`, tiny labels get `tracking-wide`/uppercase. The user reads "perbaiki ukuran font" as a request for a clearer type scale, not a uniform size bump.
- Keep bank + transaction functionality intact through any "rombak semua" redesign — the user always means visual overhaul, never "remove the money features". Rebuild + verify the served hashed asset per the standard workflow.

## Warm "Fintech Premium" Redesign From a Reference App Screenshot

Signal: the user sends a screenshot of a polished fintech app (e.g. Monarch Money, Copilot, Mint) and says "ubah UI-nya seperti ini jadi Fintech Premium". This is a distinct aesthetic from the glass/multi-color patterns above — do NOT reach for aurora glass here. These apps read premium precisely because they are warm, calm, and typographic, not frosted-and-glowy. Patterns that landed well matching Monarch:

- **Warm cream/beige background, not white or cool-blue.** `#F4EEE3` base with low-opacity warm orbs (orange `#E8743B`, gold `#E8B15B`, muted green `#1F9D6B`). White (`#FFFFFF`) is reserved for the cards that sit ON the cream, giving depth without borders.
- **Serif font for big numbers and titles.** Load Fraunces (`family=Fraunces:opsz,wght@9..144,500;600;700`) alongside Inter and add a `.font-serif` utility. Apply it to the net-worth figure, income/expense amounts, section titles, and the app wordmark. The serif-numeral-on-cream combination is the single biggest driver of the "premium editorial" impression these apps have — Inter-everywhere reads generic.
- **Solid warm cards (`card-solid`), not glass, for dense content on the cream.** `background:#FFFFFF; border:1px solid rgba(26,23,18,0.06); border-radius:24px; box-shadow:0 4px 18px rgba(80,60,40,0.06)`. Frosted glass over a warm cream bg looks muddy — reserve `.glass`/`.glass-strong` for the rare overlay, use opaque white cards for account lists and transaction rows.
- **Dark espresso hero, not navy.** Net-worth card `linear-gradient(150deg,#2A231C,#1E1913,#17120D)` with a warm orange radial glow top-right. Reads richer than a blue/navy hero against a warm palette.
- **Segmented asset tabs** mimicking Monarch's `NET WORTH / CASH / INVESTMENTS / REAL ESTATE`: a `.seg-tabs` pill container (`background:rgba(26,23,18,0.05)`) with `.seg-tab` items; active tab gets a solid white fill + small shadow. Wire it to filter the account list by `kind` (SEMUA / BANK / E-WALLET / CRYPTO).
- **`% of assets` per account row + an asset-split bar in the hero.** Compute each account's share of net worth (`Math.round(balance/netWorth*100)`) and show it inline ("Bank · 32% aset"), plus a thin stacked horizontal bar in the hero colored per account kind. This is the specific data-density detail that makes it read like a real net-worth tracker rather than a toy wallet.
- **Orange accent, green income, coral expense.** Brand orange `#E8743B` for CTAs/active nav/logo; income emerald `#1F9D6B`; expense coral `#E5533D`. Update `telegram.ts` header/bg (`#1E1913` / `#F4EEE3`) and the `theme-color` meta too.

Same full-sweep discipline as every other rebrand: `search_files` the old accent hexes across `src/` in one pass, convert `AddTransactionSheet` accent color, `ProfilePage` avatar gradient, `MonthlyHistory` summary strip, `BottomNav` active state, and the loading-screen copy — not just Home. Keep bank + transaction features intact.

## Apple / iOS System UI Redesign

Signal: \"gaya UI Apple\", \"iOS premium\", \"seperti aplikasi Apple\". This is the OPPOSITE of the glassmorphism/aurora pass — Apple's own apps (Settings, Wallet, Health, Fitness) read premium through restraint and system conventions, not frosted glow. Do NOT layer aurora orbs or multi-color gradients here. Patterns that landed well:

- **SF Pro system font, not Inter.** Set `--font-sans: -apple-system, BlinkMacSystemFont, \"SF Pro Display\", \"SF Pro Text\", \"Inter\", system-ui, sans-serif`. On real Apple devices this renders as SF Pro; elsewhere falls back to Inter. Add `letter-spacing: -0.01em` on body and a `.tabular { font-variant-numeric: tabular-nums; }` utility for money figures so digits align.
- **systemGroupedBackground `#F2F2F7`, white cards on top.** The signature iOS look is a light-grey grouped background with opaque white rounded cards (`border-radius: 18px`, hairline `0.5px solid rgba(0,0,0,0.04)`, soft shadow). Do NOT use frosted glass for the cards — reserve `backdrop-filter` for the tab bar and sheet chrome only.
- **Apple system colors:** blue `#007AFF`, green `#34C759`, red `#FF3B30`, purple `#AF52DE`, orange `#FF9500`. Income green, expense red (Apple's own semantic pairing).
- **Large title header** (`font-size:30px; font-weight:700; letter-spacing:-0.02em`) with a small grey eyebrow label above it — mimics the iOS large-title nav bar (Settings/Mail).
- **iOS segmented control**, not custom pills: `background:rgba(120,120,128,0.12)` track, active segment gets a solid white fill + `box-shadow:0 3px 8px rgba(0,0,0,0.1)`. This is the exact UISegmentedControl look.
- **Grouped list rows**: divider `0.5px solid rgba(0,0,0,0.06)` between rows, trailing `ChevronRight` in `text-neutral-300`, `:active { background: rgba(120,120,128,0.1); }` tap highlight. Rows are edge-to-edge inside the card (`pl-3 pr-3.5`), not individually boxed.
- **Apple Card-style hero**: `linear-gradient(165deg,#2C2C2E,#1C1C1E,#000)` graphite with a `radial-gradient` accent glow top-right — reads like the physical Apple Card, richer than navy/blue against the grey bg.
- **Tab bar nailed to the bottom edge** (not floating): `fixed bottom-0 left-0 right-0`, frosted `rgba(249,249,249,0.82)` + `backdrop-filter:blur(30px) saturate(180%)`, `borderTop:0.5px solid rgba(0,0,0,0.1)`, `paddingBottom:env(safe-area-inset-bottom)`. Active tab = blue, inactive = `#8E8E93`. Larger 24px icons.
- **iOS action buttons**: fills use `rgba(120,120,128,0.12)` (system fill) for secondary, solid `#007AFF` with a blue glow shadow for primary. Icon quick-actions become `rounded-[16px]` gradient tiles ~52px (home-screen-icon style).
- **Bottom sheet** = opaque `#F2F2F7` panel with a grabber bar, plain `bg-black/40` backdrop (no blur on the dimmer), `borderTopRadius:14`.
- Update `telegram.ts` header/bg to `#F2F2F7` and the `theme-color` meta too.

Same full-sweep discipline: convert `AddTransactionSheet` accent (`#34C759`/`#FF3B30`), `ProfilePage` avatar (circular blue gradient), `MonthlyHistory` summary strip, `BottomNav`, loading copy. Keep bank + transaction features intact.

## Copilot-Style \"Transaction to Review\" Card

Signal: user sends a Copilot/Monarch screenshot showing a \"TRANSACTION TO REVIEW\" modal and wants that review-inbox flow (often as a GABUNG — keep current theme, just add this feature). Build a standalone `ReviewCard` component pinned at the top of Home:

- Wrap a white inner card inside the app's hero-style dark frame; header label \"PERLU DITINJAU\" + remaining count.
- Inner card: date (weekday, d mon yyyy) + merchant/note (big) + amount, a colored category chip (dot + name, `background: ${catColor}18`), and two action buttons — a neutral \"Lewati\" (system-fill) + a primary \"Tandai ditinjau\" (brand CTA) with Check/X icons. Mirrors Copilot's Skip / Review.
- Carousel dots under the card (elongated active dot) when >1 item remains.
- **Persist reviewed IDs in `localStorage`** (no backend/schema change needed) — filter the recent-transactions list against a `reviewed` array; both Skip and Review mark the id reviewed and advance the queue. Cap the stored array (`.slice(-200)`) so it can't grow unbounded.
- Feed it the existing dashboard `recentTransactions`; no new endpoint. Only render when the unreviewed queue is non-empty.

## Fixed Sub-Page Header That Does Not Scroll

Signal: "tombol history dan lainnya buat menu terpisah, jangan sampe bisa di-scroll". This extends "Home Screen Decluttering" above — the user wants the extracted sub-page's header/controls pinned while only the content body scrolls. Implement as a flex column that fills the viewport:

```tsx
<div className="fixed inset-0 flex flex-col z-30">
  <div className="app-mesh-bg"><div className="blob-3" /></div>
  <div className="flex-shrink-0 px-4 pt-4 pb-3" style={{ paddingTop: 'calc(env(safe-area-inset-top,0px) + 16px)' }}>
    {/* header: back button + title — stays pinned */}
  </div>
  <div className="flex-1 overflow-y-auto no-scrollbar px-4 pb-8">
    {/* ONLY this scrolls */}
  </div>
</div>
```

Key points: the page is `fixed inset-0 flex flex-col` (fills screen, no page-level scroll); header is `flex-shrink-0`; body is `flex-1 overflow-y-auto`. Respect `env(safe-area-inset-top)` on the header so it clears the notch/status bar. This is the correct answer whenever the user complains a menu "bisa di-scroll" and wants its controls to stay put — do NOT just wrap everything in one scrolling div with a sticky header (sticky still lets the whole page scroll and feels loose on WebView).

## Notes Workspace

Default to the simplest possible notes model — a title and a body, Google Keep style — unless the user explicitly asks for tables, checklists, sketches, or templates. Do not preemptively build the richer workspace described below for a finance app's notes feature; confirm scope first. If a rich workspace already exists and the user says something like "cukup note kaya google note gt, gausah ada tabel segala macam" ("just simple notes like Google Notes, don't need tables and such"), that is a request to strip it down to title+body, not to add options — remove the table/sketch/tag UI and the corresponding creation buttons rather than hiding them behind a toggle.

Useful note types (only when explicitly requested):

- Text plus checklist.
- Spreadsheet-like table.
- Lightweight vector sketch.

Table requirements:

- Internal horizontal/vertical scrolling.
- Internal zoom controls.
- Sticky headers and row numbering.
- Column types such as text, number, checkbox, and date.
- Per-column colors.
- Add/remove rows and columns.

Editor requirements:

- Fullscreen toggle.
- Pin, tags, colors, duplicate.
- Ready-to-use templates (daily note, meeting, finance, to-do).
- Clear save/delete controls.

### Fullscreen Toggle Implementation (even for the stripped-down title+body editor)

Even after stripping notes down to title+body per the rule above, the user may still ask for the editor to "scale up full screen." Implement this as a single `fullscreen` boolean in the editor component's local state, not a separate route/page:

- Default editor renders inside the existing bottom `Sheet` component. Add an expand icon button into the sheet's header (extend `Sheet` with an optional `headerExtra` prop rendered next to the close button, so other sheets can reuse the same header-button pattern later).
- When `fullscreen` is true, render the *same* form JSX (extract it to a local `editorBody` variable/const so there is exactly one copy) inside a `fixed inset-0 z-50` full-viewport container with its own header (back/minimize button + title) instead of the `Sheet` wrapper — do not fork the form into two components that can drift out of sync.
- Bump the textarea's `rows` and give it a `minHeight: 60vh` inline style in fullscreen mode so it actually uses the extra space, rather than leaving a full-screen container with a small textarea floating in it.
- Keep save/delete handlers identical between the two render modes — only the chrome around `editorBody` changes.

## Wishlist Funding Ledger

A Wishlist savings goal linked to an account is a money ledger, not a cosmetic progress number.

- Require the funding account currency to equal the Wishlist currency.
- **Setor** atomically subtracts from the funding account, adds to `saved_amount`, and appends a positive savings-history row.
- **Tarik** atomically subtracts from `saved_amount`, returns money to the funding account, and appends a negative history row so the calendar stays red/auditable.
- Reject deposits above the funding balance and withdrawals above the saved amount.
- Record the funding-account ID on each savings-history row.
- If a Wishlist with a surviving funding account is deleted, refund its remaining saved amount before deletion.
- If an account is deleted first, detach it from Wishlist goals rather than deleting the goals.
- Do not expose direct `savedAmount` editing after creation; use Setor/Tarik so balances and history cannot drift.
- Calendar cells should use positive net activity as soft green, negative net activity as red, and empty dates as neutral.

Tests must prove funding balance changes, same-currency enforcement, positive/negative history, over-deposit/over-withdraw rejection, and delete refund.

## Browser-Native Exports

Build every export from one canonical `ExportableNote` representation so formats do not disagree.

- TXT: normalized plain-text representation of title, tag, text/checklist, or tab-separated table.
- CSV: UTF-8 BOM plus correctly quoted cells; table notes map directly to rows/columns, ordinary notes use explicit section/value rows.
- XLS: Excel-compatible HTML generated from the same note renderer; preserve table header colors and cell order.
- PDF: provide a fullscreen A4 preview rendered from the same HTML as the note. Use the browser Print / Save as PDF path so tables, colors, checklists, and vector sketch strokes remain visible. Do not label a text-only summary as a faithful PDF.

PDF preview should have print CSS (`@page`, margins, hidden controls), mobile fullscreen fallback, and an explicit **Save PDF** button. Spreadsheet fullscreen should use viewport-relative internal height, sticky headers, bounded scrolling, and internal zoom—never page-level zoom.

Exports must work for text, checklist, table, and sketch notes. Sketch TXT/CSV may describe the sketch, but PDF must embed the actual SVG paths.

## Data Migration and Compatibility

- `CREATE TABLE IF NOT EXISTS` does not add columns to an existing table. Use an idempotent `addColumnIfMissing` migration.
- New serialized fields must be wired through backend serializers, frontend types, API client payloads, editors, and display components.
- Preserve legacy endpoints/data while introducing a simpler UI where practical.
- Sanitize custom logo text and colors server-side as well as client-side.

## Feature Removal and Refactoring

When removing a complete feature from a full-stack finance app (e.g., removing wishlist, savings goals, or budgets):

**Backend cleanup (in order):**
1. Remove route registration from `server.js` first.
2. Delete the route handler file (e.g., `routes-wishlist.js`).
3. Remove the table CREATE statements from `db.js` schema.
4. Remove any indexes referencing the deleted tables.
5. Remove `addColumnIfMissing` calls for columns on deleted tables.
6. Clean up foreign key detachment logic in related routes (e.g., account deletion).
7. Remove the route test file if it exists.

**Frontend cleanup (in order):**
1. Delete the page component file (e.g., `pages/WishlistPage.tsx`).
2. Delete related sheet/modal/picker components.
3. Remove the import and route from `App.tsx`.
4. Remove the tab from `BottomNav` and update the `TabKey` type union.
5. Remove API methods from `api.ts`.
6. **Critical**: Remove the type imports at the top of `api.ts` (e.g., `WishlistItem`, `WishlistSaving`) to prevent TypeScript errors even after the types are deleted.
7. Delete the interface definitions from `types.ts`.
8. Remove references from the state store (`store.ts`).

**Restart and rebuild:**
- Kill the backend process, restart it, and verify `/health`.
- Run `npm run build` in the frontend and fix any TypeScript errors iteratively.
- Common post-removal TypeScript errors: unused imports in `api.ts`, missing icon exports, store property references.

## Dashboard Statistics Endpoint

For a financial tracker dashboard showing monthly summaries:

**Backend endpoint pattern** (`routes-dashboard.js`):
```javascript
router.get('/summary', (req, res) => {
  const userId = req.tgUser.id;
  const now = Date.now();
  const d = new Date(now);
  const startOfMonth = new Date(d.getFullYear(), d.getMonth(), 1, 0, 0, 0).getTime();
  const endOfMonth = new Date(d.getFullYear(), d.getMonth() + 1, 0, 23, 59, 59).getTime();

  const totalIncome = db.prepare(`
    SELECT COALESCE(SUM(amount), 0) as total 
    FROM transactions 
    WHERE user_id=? AND type='income' AND occurred_at >= ? AND occurred_at <= ?
  `).get(userId, startOfMonth, endOfMonth).total;

  const totalExpense = db.prepare(`
    SELECT COALESCE(SUM(amount), 0) as total 
    FROM transactions 
    WHERE user_id=? AND type='expense' AND occurred_at >= ? AND occurred_at <= ?
  `).get(userId, startOfMonth, endOfMonth).total;

  const topCategory = db.prepare(`
    SELECT c.name, SUM(t.amount) as total
    FROM transactions t
    JOIN categories c ON t.category_id = c.id
    WHERE t.user_id=? AND t.type='expense' AND t.occurred_at >= ? AND t.occurred_at <= ?
    GROUP BY c.id
    ORDER BY total DESC
    LIMIT 1
  `).get(userId, startOfMonth, endOfMonth);

  res.json({
    summary: {
      totalIncome,
      totalExpense,
      netBalance: totalIncome - totalExpense,
      transactionCount: /* COUNT query */,
      topCategory: topCategory ? { name: topCategory.name, total: topCategory.total } : null,
      month: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`,
    },
    recentTransactions: /* last 5 with category JOIN */,
  });
});
```

**Frontend consumption:**
- Use `api.request<T>('GET', '/dashboard/summary')` directly if the endpoint isn't wrapped yet.
- Expose `request` function in `api` export: `export const api = { request, me: ..., accounts: ... }`.
- Display metrics in gradient icon cards (emerald for income, rose for expense, sky for net balance).
- Use `TrendingUp`/`TrendingDown` icons from `lucide-react` for visual hierarchy.

## "Nothing Changed At All" — Duplicate Server Processes on the Port

Signal: after a full rebuild + restart the user reports the UI is byte-for-byte identical ("gd yg berubah sama sekali", "nothing changed", "kocak"). Before touching the CSS/JS again, verify WHO is actually serving the port — this is almost always a stale process, not stale code:

1. **Prove the built asset is correct first.** `curl -s http://localhost:PORT/ | grep -oE 'index-[A-Za-z0-9_-]+\.(css|js)'` to get the served hash, then `curl` that asset and grep for a string only the new version contains (e.g. a new CSS class `btn-grad-violet`, new copy "SAVINGS RATE"). If the new markers ARE present in the served asset, the server is fine and the problem is client-side cache (see below). If they're absent, a stale server is serving old `dist/`.
2. **Find every process on the port, not just the one you started.** `ps aux | grep server.js | grep -v grep` frequently shows MULTIPLE node processes — a background one you launched turns earlier plus new ones — all having bound the port at different times. The first to bind keeps it; your fresh restart may have silently failed to bind (EADDRINUSE swallowed) and you're testing the old one.
3. **Disambiguate by working directory**, since several projects share the pattern: `ls -l /proc/<pid>/cwd | grep -oE 'projectname'` for each PID. Kill only the ones whose cwd matches THIS project (`kill -9 <pid>`) — don't blanket-kill every `server.js` (you may take down an unrelated app like a sibling bot).
4. Confirm the port is free (`curl -s .../health || echo free`), then start exactly ONE server, and re-verify the served asset hash.

This is the real explanation behind the existing "declaring success while the served asset hash is old" pitfall — the mechanism is usually a duplicate/orphaned process, so check process list before re-editing code.

## Client-Side Cache Hardening for Tablet / Telegram WebView

If the served asset genuinely contains the new code but the user still sees the old UI, it's WebView/browser cache. Two-layer fix:

- **Server: never cache `index.html`.** Vite assets are content-hashed (`index-<hash>.js`) so they're safe to cache forever, but `index.html` (which references the current hashes) must always be fresh. In the Express static handler, force no-cache headers on `index.html`/root/extension-less paths, and pass `express.static(DIST, { etag: false, lastModified: false, cacheControl: false })` for the asset dir. NOTE: Express may still emit `Cache-Control: public, max-age=0` on static index.html despite `setHeaders` — the reliable fix is a small middleware BEFORE `express.static` that sets `no-cache, no-store, must-revalidate` + `Pragma`/`Expires` on `req.path === '/' || '/index.html' || !path.extname(req.path)`.
- **Client: tell the user to fully kill the app**, not just back out — "tutup total Telegram dari recent apps, buka lagi" or hard-refresh / incognito in a browser. WebView holds the old bundle until the process is killed.
- If the app is served through Cloudflare Tunnel on a domain (not localhost), remember Cloudflare is an additional cache layer — test against `localhost:PORT` directly to isolate whether the staleness is server, tunnel, or client.

## Common Pitfalls

- **After deleting types from `types.ts`, forgetting to remove their imports from `api.ts`**, causing "Module has no exported member" TypeScript errors even though the types are gone.
- **Restarting the server without killing the orphaned old process first** — the new process fails to bind the in-use port and you keep testing the old code (see "Nothing Changed At All" above).
- Patching backend code without restarting the server, then testing stale behavior.
- Declaring success because source files changed while the served asset hash is old.
- Hiding a currency hero based on account existence when product requirements say it should always appear.
- Using `balance <= amount` for insufficient funds, which incorrectly rejects sending the full balance.
- Rendering external expenses as category `-` instead of showing their destination.
- Combining internal transfer and external expense under one ambiguous button after the user explicitly requested separate actions.
- Rejecting account deletion merely because balance/history exists when the product calls for typed destructive confirmation.
- Treating Wishlist progress as an independent number while leaving the selected funding-account balance unchanged.
- Deleting savings-history rows when the product expects withdrawals to remain visible as negative/red calendar activity.
- Deleting a funded Wishlist without refunding its remaining savings.
- Calling a text-only PDF “faithful” when tables, colors, checklist state, or sketch strokes are missing.
- Rebranding only the header while stale names remain in loading copy, exports, favicon, PDF, profile, or backend logs.
- Adding UI features without matching types, serializers, API payloads, migrations, and tests.
- Using emoji when the requested visual language is icon-only.
- Letting tables/history/calendar scroll or zoom the whole page instead of their own container.

## Verification Checklist

- Focused regression test was observed failing before the fix.
- Exact-balance send passes.
- Receive/send real HTTP flow passes.
- Full backend test suite passes.
- Frontend regression scripts pass.
- `tsc` and production build pass.
- Live server returns HTTP 200 and references the fresh hashed asset.
- Temporary test data is removed.
- No emoji remain in user-facing UI.
- New UI remains compact and usable in a mobile WebView.

See `references/cloudwallet-patterns.md` for a condensed implementation reference from a proven React/Node/SQLite finance-app iteration.