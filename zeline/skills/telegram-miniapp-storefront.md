# Telegram Miniapp Storefront

> Build and maintain Telegram Mini App e-commerce storefronts with iOS-inspired glass morphism UI

Build full-featured e-commerce Mini Apps for Telegram with iOS-inspired design language, optimized for mobile WebView performance.

## When to Use

- Building product catalog/shop Mini Apps for Telegram
- iOS-style glass morphism UI with dark themes
- Mobile-first, WebView-optimized storefronts
- Real-time balance, transactions, and product management

## Not just storefronts: any Telegram Mini App class (finance trackers, dashboards, note apps)

This skill's name says "storefront" but the reusable core — Termux/Node backend,
`node:sqlite`, Telegram initData auth with DEV_MODE browser fallback, Cloudflare Tunnel
deploy, rate limiting — applies to ANY Telegram Mini App, not just e-commerce. When the
class of app has real forms/state/multiple entity types (e.g. a personal-finance tracker
with accounts + transactions + notes) rather than a static product catalog, use React +
TypeScript + Vite + Tailwind v4 for the frontend instead of a single vanilla `app.js`.
Tooling differences (Tailwind v4's `@tailwindcss/vite` plugin instead of `init -p`, Express
5's `app.get('*')` breaking, Termux's missing `/tmp`, using a Cloudflare quick tunnel to let
the user preview UI when no browser-automation tool is installed, the user-256-GCM for sensitive
text fields) are in `references/react-vite-modern-stack.md` — read it before scaffolding a
React-based Mini App so you don't rediscover the same v4/Express5/Termux gotchas.

## Static frontend vs. real backend (know which you're building)

The frontend patterns below produce a STATIC storefront: products, stock, prices, and
credentials all hardcoded in `app.js`. Fine for a demo/etalase. But the moment the user
wants to actually SELL accounts, that model is unsafe — anyone can View-Source `app.js`
and read every credential without paying. Real selling requires a backend that keeps
credentials server-side and releases them only after payment is confirmed.

When the user asks "how do I fill in products / stock after deploy? is there an admin
portal?" — that is the signal to build the full stack (backend + admin panel + payments).
The complete proven recipe (Node+Express, `node:sqlite` on Termux incl. its missing
`transaction()` gotcha, credential security + no-leak catalog, NOWPayments USDT + IPN,
PBKDF2 admin auth, credit-vs-direct-pay flows, flexible multi-line credential rendering
with the `(?!//)` label regex and base64 copy-safety) is in
`references/backend-admin-payments.md`. Read it before starting any backend work.

## Language & Communication

**CRITICAL**: If user writes in Indonesian, respond ENTIRELY in Indonesian. Memory may note language preference, but always verify from their messages—don't default to English.

## Design Principles

### No emoji in the UI (recurring user preference)
For this user's apps, do NOT put emoji anywhere in the interface — not in section headers,
buttons, empty-states, status labels, checkmarks, or celebratory text ("Target tercapai 🎉"
→ plain "Target tercapai"). Use vector icons (lucide-react `<Check>`, `<Heart>`, etc.) or
plain text instead. The user has stated this flatly and repeatedly ("jangan adaa emoji sama
sekali", "no emoji"). After any UI build/edit, run a quick emoji grep over `src/` before
declaring done — even a stray `✓`/`✔` glyph counts; swap it for a `<Check>` icon:
`grep -rnP '[\x{1F000}-\x{1FAFF}\x{2600}-\x{27BF}\x{2B00}-\x{2BFF}\x{FE0F}\x{2705}\x{2713}\x{2714}]' src/`

### iOS Dark Glass Aesthetic
- **Background**: Pure black `#000` with subtle gradients
- **Glass cards**: `backdrop-filter: blur()` with multi-stop gradients (7+ stops to prevent banding)
- **Text**: Inter font, iOS dynamic type scale
- **Colors**: iOS blue `#0A84FF` for primary actions
- **Animations**: Transform-only (avoid `background-position`, `blur` animation—WebView lag risk)

### Product Grid Layout
- 4-column responsive grid
- 5 rows = 20 total slots (real products + placeholders)
- Icon size: 64–72px, fills bubble with thin glass frame (~5px inset)
- Border radius: 22.9% (matches iOS app icon proportion)

### Icon Standards
```css
.product-bubble svg,
.product-bubble img {
  width: 100%;
  height: 100%;
  border-radius: 22.9%; /* matches SVG rx=11 on 48px canvas */
  object-fit: cover;
}
```

**SVG icons**: `viewBox="0 0 48 48"`, `rx="11"` for rounded corners, embed as string in product data.

**PNG icons**: Place in `icons/` folder, reference via `<img src="icons/name.png?v=1">`. Same border-radius applied via CSS.

## Animations

### Safe Patterns (No Lag)
```css
/* Icon glow synced to effects */
@keyframes iconLit {
  0%   { filter: brightness(1); }
  50%  { filter: brightness(1.28) drop-shadow(0 0 6px currentColor); }
  100% { filter: brightness(1); }
}
```

### Avoid (Causes WebView Lag)
- `animation: background-position` on gradients
- Animated `blur()` filters on large elements
- Simultaneous animations on 10+ elements

**Mitigation**: Use `transform` and `filter: brightness()` only. Limit animated elements to <10 at once.

### Syncing two visual effects (light-sweep + icon glow)
Two separate `@keyframes` on two elements will NEVER stay perfectly in phase — the peaks drift. Two correct fixes:
1. **One element, blend mode**: put a moving light band ABOVE the icon and use `mix-blend-mode: screen` so the icon lights up exactly where the band passes. One element = automatic sync.
2. **Shared timeline**: if you keep two animations, give them IDENTICAL `duration`, `ease`, `alternate`, and per-element `animation-delay` so they progress together.

**Stacking-context pitfall (cost me several rounds)**: `mix-blend-mode` only blends against what's in the SAME stacking context. If the overlay wrapper has `z-index`, `opacity<1`, `transform`, or `filter`, it becomes an isolated context and the blend does nothing (icon won't light up). Remove `z-index`/`isolation` from the wrapper so the blend reaches sibling content. See `references/webview-ui-techniques.md`.

### Animation is user-tuned, iteratively
Expect several rounds of "faster / thinner / lighter / delete that one / only on the app bubbles not the big cards". Default to SUBTLE and cheap; make speed/opacity trivial to dial. When user says "berat/lag", the real culprit is almost always repaint-triggering animation (background-position, blur) or too many animated blurred elements — not the effect itself.

## Product Data Structure

```javascript
const PRODUCTS = [
  { 
    id: 3, 
    name: 'Product Name', 
    desc: 'Short description', 
    price: 10000, 
    available: true, 
    sold: 128, 
    stock: 17,
    color: '#ffffff',
    plans: [ /* optional multi-tier plans */ ],
    icon: '<svg>...</svg>' // or '<img src="icons/name.png">'
  },
  // ... real products
  { 
    id: 20, 
    name: 'Coming Soon', 
    placeholder: true, 
    available: false,
    icon: '<svg>...</svg>' // "?" placeholder
  }
];
```

**Grid sizing**: Keep total products ≤ 20 (4 cols × 5 rows). User can request adding products to empty placeholders.

## Internationalization (i18n)

Two-language support (ID/EN) via key-based translation:

```javascript
const LANG = {
  id: { key: 'Teks Indonesia', ... },
  en: { key: 'English text', ... }
};

function t(key) { return LANG[currentLang][key] || key; }
```

Add product-specific translations in `PRODUCT_I18N` object when product names/descriptions differ by language.

## File Structure

```
project/
├── index.html          # Single-page app shell
├── style.css           # All styles, versioned (?v=N)
├── app.js              # All logic, versioned (?v=N)
└── icons/              # Product logos (PNG/SVG)
    ├── product1.png
    └── product2.svg
```

**Cache busting**: Increment `?v=N` in HTML on every CSS/JS change.

## Image Protection (Anti-Scrape)

Prevent long-press context menus on images:

```css
img {
  -webkit-touch-callout: none;
  -webkit-user-select: none;
  user-select: none;
  pointer-events: none; /* parent handles clicks */
}
```

```javascript
document.addEventListener('contextmenu', (e) => { 
  e.preventDefault(); 
  return false; 
});
```

## SVG Icon Creation from PNG

When user provides PNG logos, trace to SVG for smoothness:

1. **Analyze**: Identify shapes (circles, paths, text, gradients)
2. **Trace**: Rebuild as SVG paths/primitives
3. **Simplify**: Use solid colors for gradients unless critical to brand
4. **Size**: `viewBox="0 0 48 48"`, `rx="11"` for rounded rect background
5. **Embed**: Inline in product `icon` field as escaped string

**Example workflow**:
- Logo with letter + accent → `<path>` for letter + `<circle>`/`<ellipse>` for accent
- Logo with gradient → `<linearGradient>` in `<defs>`, or solid approximation
- Keep file size <1KB per icon

**REALITY CHECK — traced SVG rarely satisfies the user.** Manual/auto trace of a real brand logo comes out "close but not it", and the user often reverts to their original PNG. Set expectations up front: offer (1) trace to SVG, (2) higher-res PNG, or (3) find the official vector — and recommend the official vector or PNG over trace for brand fidelity. Keep the PNG in `icons/` even after tracing so revert is a one-line `?v=` bump. Don't spend many rounds perfecting a trace unless the user explicitly wants vector.

**Replacing a logo the user sends as an image**: copy the cached image into `icons/<name>.png`, point the product `icon` to `<img src="icons/<name>.png?vN">`, and BUMP the `?v=` query (not just the JS version) so WebView re-fetches the changed file.

## Common Patterns

### Profile Section Redesign
- Remove unnecessary fields (username, membership status) when user requests
- Add brand watermark (small logo + text) at top-right, `opacity: 0.4`
- Use FAQ/Guide as full-page views (not accordions) with back navigation
- **Guard init/render against removed DOM nodes**: when you delete an element (e.g. `#profile-username`), also delete the JS that writes to it or wrap in `if (el)` — a `null.textContent` throws and silently breaks the rest of `init()`.

### Static (no-scroll) dashboard
User may want the home tab to feel fixed, not scrollable. Add a `.no-scroll` class (`overflow-y:hidden; overscroll-behavior:none`) toggled per-tab in `goTab()` (`cw.classList.toggle('no-scroll', name === 'dashboard')`), and set it initially in HTML. Then shrink elements slightly (icon 72→64px, gaps, paddings) so everything fits one screen. Adding a row of product-name labels increases grid height — re-check it still fits without scroll.

### Pagination instead of scroll
User preference: history/notification lists use `Back  1 2 3  Next` pagination, NOT infinite scroll. One reusable `buildPagination(totalPages, current, fnName)` helper (windowed page numbers, disabled Back/Next at ends, `pg_back`/`pg_next` i18n keys) serves purchase history, deposit history, notifications — AND multi-topic info pages. This session it was also used to split the **Panduan (guide)** into separate paged TOPICS: "Cara Belanja" (page 1) and "Kode 2FA" (page 2), because the user said deposit and 2FA are two different subjects and shouldn't be one long scroll. Each topic renders its own methods/steps + the pagination bar; a `_guidePage` state var + `gotoGuidePage(p)` re-renders. Reset the page var to 1 when the section is opened fresh from the menu (wrap the menu handler, e.g. `openGuide()` sets `_guidePage=1` then `openInfoPage('guide')`). Localize labels ("Kembali"/"Selanjutnya" vs "Back"/"Next"). Page size is a single const. Reset scrollTop on page change.

### Product name labels (homescreen style) — EXPECT ITERATION
Label placement is user-tuned and went through 3 variants in one session; don't assume the first is final:
1. **Below the bubble**: wrap bubble + `.product-name` span in a `.product-cell` flex column; move `onclick` to the cell (not the bubble) to avoid double-trigger.
2. **Floating pill inside**: absolute-positioned pill (dark bg + blur + tilt) in a corner — reads on any icon color but can feel cluttered.
3. **Full-width name band inside the bubble ("bubble dipangkas")**: make `.product-bubble` a `flex-direction:column`; the icon wrapper becomes `flex:1; min-height:0` (fills the top), and `.bubble-name` is a full-width strip at the bottom with a `border-top` divider. Icon `<svg>/<img>` inside the wrapper switch to `height:100%; width:auto; aspect-ratio:1` so they shrink to leave room for the band. **This variant was later REJECTED as "jelek"** — the trimmed-icon look reads as clunky. Don't assume it's the finish line.
4. **Per-glyph glass outline in a tilted corner (where it landed after full loop)**: back to absolute corner (`right:5px; bottom:5px`), tilted `rotate(-6deg)`, NO pill box background. The "glass border follows the font shape" effect = `-webkit-text-stroke: 0.4px rgba(255,255,255,0.35)` on the glyphs + layered `text-shadow` (a tight dark `0 0 1px`, a drop `0 1px 2px`, and a soft glow `0 0 5px`) for contrast on any icon color. Font-weight 800 so the stroke reads. This satisfied the user's "border bubble mengikuti bentuk setiap font" request — the outline hugs each letter instead of being a rectangle.

Common to all: pull the display name from a SHORT field (add `short: 'ChatGPT'` per product) or `PRODUCT_I18N[currentLang][p.id]` so names stay compact and follow language; only label real products, not `?` placeholders; `pointer-events:none` on the label so the bubble handles the tap. Keep names short — user explicitly rejected long names (and shortens further mid-session: "Kiro AI" → "Kiro", updating `name`, `short`, AND both `PRODUCT_I18N` entries).

**The label style is a multi-round loop, not a linear path.** Expect: below-bubble → corner pill → full-width band → back to corner pill → per-glyph outline. When the user says "kembaliin ke sebelumnya tp percantik", they want the earlier PLACEMENT with a more refined treatment, not a literal revert. The through-line of their taste: subtle, not a visible rectangle box — "jangan keliatan banget kaya di lapisi", "border glass mengikuti bentuk font". Reach for text-stroke/shadow on the glyphs over a pill background.

### Stock-driven dynamic price & label (out-of-stock UX)
User distinguishes two empty states, and they read differently:
- **"Terjual Habis" (sold out)** — was available, now depleted.
- **"Item belum tersedia" (not available yet)** — never stocked. User asked for a SEPARATE i18n key (`detail_not_available`) for this, AND for the price to render **Rp0** while stock is 0 (not the real price).
Make price/total/button all stock-aware, driven off the same `stock > 0 ?` guard, in EVERY branch: plan-list rows, the single-product branch, the Total row, the buy button label, AND `updateDetailBuyBtn()` (the live updater on qty/plan change). Miss one and the number flickers back to the real price. Pattern: `formatRupiah(stock > 0 ? price : 0)` and `stock <= 0 ? t('detail_not_available') : ...`.
Also hide instructional copy (e.g. `detail_note` "Pilih opsi pembelian di bawah ini") when stock is 0 — wrap it `(stock > 0 ? '<p>...</p>' : '')`. Don't show a call-to-action for something that can't be bought.
Bulk-zeroing stock: do it in `execute_code` with a scoped regex (`stock:\s*\d+` → `stock: 0`) applied ONLY between the PRODUCTS array bounds, not the whole file (USAGE_I18N etc. also have numeric keys). Then set individual live stock with a targeted `patch`.

### Multi-plan default selection MUST skip out-of-stock plans
`openProductDetail` defaulting to plan index 0 is a real bug: if plan[0] is out of stock but plan[1] has stock, the user lands on a dead "Item belum tersedia / Rp0 / disabled" view and must manually tap another plan. Fix: default to the first in-stock plan, fall back to 0 only if ALL are out.
```javascript
var defIdx = 0;
if (p.plans && p.plans.length > 0) {
  var firstInStock = p.plans.findIndex(function(pl){ return (pl.stock||0) > 0; });
  if (firstInStock >= 0) defIdx = firstInStock;
}
window._selectedPlanIdx = defIdx;
```
Then thread `defIdx` through the render: the `selected` class (`idx === defIdx`, not `=== 0`), and the initial price/stock/total/buy-button (`p.plans[defIdx]`, `buyProductDetail(p.id, defIdx)`). Hardcoded `0` in any of these desyncs the initial view from the highlighted plan.

### Alphabetical / bulk reorder of PRODUCTS
Reorder the array programmatically in `execute_code` (parse entries, sort by name, rewrite) rather than hand-editing — entries span multiple lines (multi-plan products) and IDs must stay unique. Verify after: `node --check`, count `{ id:` entries, check for duplicate IDs, confirm order. Deleting a product means ALSO removing its `PRODUCT_I18N` entries (harmless if left, but clean up).

See `references/webview-ui-techniques.md` for the copy-paste recipes.

### Version Bumping
```bash
# After CSS change
patch(HTML, 'style.css?v=N', 'style.css?v=N+1')

# After JS change  
patch(HTML, 'app.js?v=M', 'app.js?v=M+1')
```

## Auth: Mini App (initData) vs. browser (Login Widget)

The storefront can run in two contexts and each needs its own identity source:
- **Inside Telegram (Mini App):** identity from `tg.initData`, verified with
  `HMAC(key="WebAppData", bot_token)`. This is the default path.
- **In a plain browser (public domain):** `initData` is empty, so add the official
  **Telegram Login Widget** ("Login with Telegram"). It verifies with a DIFFERENT secret
  (`SHA256(bot_token)`, not an HMAC), and you bridge it to a server session token that the
  frontend replays via an `x-web-token` header. Requires a one-time `/setdomain` in
  BotFather (user must do it). Full recipe — verify functions, session Map, auth precedence
  chain, dynamic widget injection, expired-token recovery, and a no-browser test harness —
  in `references/telegram-login-widget-browser.md`.

**Remove any DEV_UID/dummy-user fallback when you add real browser auth** — the hardcoded
dummy id (e.g. `999001`) is itself a "every browser user shares one account" bug.

## Backend Features (see references/backend-admin-payments.md)

When building a real storefront (not just a demo), the backend provides:
- **Product/plan CRUD with i18n** — admin panel forms for dual-language (ID+EN) product
  names, descriptions, durations, warranties. DB schema: `name_i18n`, `desc_i18n` JSON
  columns; migration script adds columns to existing DBs safely.
- **Promo codes** — admin creates codes (e.g. `FREECREDIT10K`), users claim for instant
  balance top-ups. Atomic server-side validation (1× per user, quota limits).
- **Stock management** — multi-line credentials (email+pass+2FA), admin fill/delete,
  never leaked in catalog API.
- **Payment flows** — e-wallet (manual approve), USDT (NOWPayments IPN auto-confirm).
- **iOS-style loading splash** — fade-in logo + breath animation + spinner, hides after
  backend data loads. Pattern: CSS-only animation, `hideSplash()` called after async loads
  + 3-5sec enforced minimum display time (prevents "looks like a bug" perception on fast loads).
  Logo optimization: compress large PNGs with PIL thumbnail+optimize before use. Full splash
  implementation + user feedback patterns in `references/ios-loading-splash.md`.
- **Background music** — looping BGM with mute toggle, persist mute in localStorage, volume 30%.
  CRITICAL timing: start music the instant the splash ends (preload early + call `startBgm()`
  inside `hideSplash()` + arm `pointerdown`/`touchstart` fallback from the splash). Waiting only
  for a post-load tap reads as "musik telat/bug" — a real user complaint. **Context-split autoplay
  (what the user ultimately settled on):** attempt real autoplay ONLY when `inTelegram()` (Telegram
  WebView usually permits it, so music can start from the loading screen); in a plain browser leave
  it as click/tap-first because Chrome hard-blocks autoplay and forcing it just fails silently. So:
  `if (inTelegram() && !_isMuted && bgm) bgm.play().catch(()=>{})` at init, plus the universal
  first-interaction listener as fallback. Don't promise browser autoplay — the user explicitly said
  "kalau gabisa jangan deh". Mute button can be floating (top-right) OR inline in the Profile header
  OR in the hamburger menu (label "Musik: On/Off") per user taste. **Must PAUSE when the app
  goes to background and RESUME on return** — and Telegram WebView Android does NOT reliably
  fire `blur`/`visibilitychange` on a direct app-to-app swipe, so a ~400ms `document.hidden`
  polling fallback is REQUIRED (naive event-only handlers leave music playing in WhatsApp).
  Full pattern + gotchas (incl. the background-pause section) in `references/bgm-integration.md`.
  **Background pause/resume via an INTENT flag + one polling loop (not event-only, not pause-only):**
  a naive implementation that only PAUSES on `document.hidden` leaves music dead when the user returns
  (Telegram WebView often doesn't fire `focus`/`visibilitychange` on app-to-app return either). The robust
  pattern: keep a boolean `_bgmIntent` = \"user wants music on\" (set true when playback starts / on unmute,
  false on mute). A single `_bgmSync()` reads `document.hidden`: if hidden → pause; if visible AND
  `_bgmIntent` AND not muted → play. Wire `_bgmSync` to `visibilitychange`/`blur`/`focus`/`pageshow`/`pagehide`/`freeze`/`resume`
  AND a ~400ms `setInterval` fallback (the interval is what actually saves you on Telegram Android, both
  directions). This makes resume-on-return reliable, which pure event handlers do NOT. User complaint that
  triggers this: \"balik ke mini app musik tiba tiba hilang\".

## Pitfalls

### 1. UI State Leak in Multi-Step Flows

**Problem:** When multiple child divs inside a single `<div class="screen">` container are populated via `innerHTML` at different stages (e.g., payment method selector + QR code + form), switching between stages leaves **visual artifacts** visible (QR codes, forms) even after the user navigates back.

**Cause:** All child divs inside an active screen render simultaneously. Setting `innerHTML` on one child doesn't hide the others.

**Solution:** Split multi-step flows into **separate screen containers** (one screen = one logical UI state). Use `goTab('screen-name')` / `navigateTo('screen-name')` for navigation. Only one screen is `.active` (visible) at a time.

**Example:** Payment method selection (`screen-payment`) → USDT QR + form (`screen-payment-info`) → both as separate top-level `<div class="screen">` siblings, not nested.

- `references/ui-state-leak-inline-vs-dedicated-screen.md` — full diagnosis, code examples, and test verification.
- `scripts/audit-screen-div-mismatch.js` — static sweep for pitfall #31: finds every function that writes non-empty innerHTML into one dynamic-content div but calls `goTab()` targeting the OTHER div's screen. Run after any refactor touching dual-screen routing.

### 2. Stale Payment Gateway Limits After Migration

**Problem:** After migrating from NOWPayments (12 USDT minimum) to on-chain USDT (no minimum), the frontend still blocks small purchases with "Minimum USDT payment is 12 USDT" warning, even though the backend accepts any amount.

**Cause:** Frontend constant `USDT_MIN` (typically defined near top of `app.js`) still set to the old gateway's minimum. The check `idrToUsdt(pm.price) < USDT_MIN` blocks the "Next" button and shows the error.

**Solution:** Update `USDT_MIN` to `0` when switching to on-chain direct payment. Also update the comment explaining the limit source.

**Example:**
```javascript
// BEFORE (NOWPayments era)
var USDT_MIN = 12; // NOWPayments minimum

// AFTER (on-chain)
var USDT_MIN = 0;  // On-chain direct: no minimum
```

**Where to check:** Search frontend code for `USDT_MIN`, `MINIMUM_USDT`, or hardcoded `12` in payment validation logic. Update both the constant and any inline checks.

### 3. WebView Performance
When NOTIFICATIONS array contains both numeric IDs (counter-based, in-session notifs) and string IDs (e.g. `'ann5'` from backend announcements), the template `onclick="readNotif(' + n.id + ')"` emits `readNotif(ann5)` for strings — `ann5` is undefined, throws. **Fix both**: quote the ID in the template (`onclick="readNotif(\'' + n.id + '\')"`), and compare loosely in the handler (`String(x.id) === String(id)`). This pattern applies any time you mix ID types in a list with inline handlers.

### Status filtering vs. backend reality

2. **Animation lag**: Don't animate `background-position`, `blur`, or properties that trigger repaints. Stick to `transform` and `filter: brightness/drop-shadow`.

3. **Icon aspect mismatch**: PNG icons with built-in borders look "boxy" even with `border-radius`. Crop or use SVG.

4. **Language mixing**: User writes Indonesian → respond ENTIRELY in Indonesian. Check every message, don't rely on session start language.

5. **Over-engineering**: User said "JANGAN subagent utk web project" (don't spawn subagents for web projects). For single-page Mini Apps, work directly—no delegation, no installs, no tracing unless explicitly needed.

6. **BOT_TOKEN empty = shared dummy user bug**: If `BOT_TOKEN` is not set in `start.sh`, backend falls back to `DEV_MODE`, accepting `x-user-id` from any request. In production (Telegram Mini App), `initData` is sent but verification FAILS (no BOT_TOKEN to verify HMAC) → all users become the same fallback user → **all users see same balance/history/purchases**. ALWAYS set `export BOT_TOKEN="..."` in `start.sh` before deploy. Symptom: "kenapa setiap user punya saldo yang sama?" = BOT_TOKEN kosong.

7. **Safe-area header crash (content ketiban native Telegram controls)**: Telegram Mini App shows native header (Close, progress bar, chevron) at top. Without safe-area padding, app content starts at `y=0` and gets **covered** by native controls. Fix: `.content-wrap { padding-top: calc(20px + env(safe-area-inset-top, 0px)); }` respects Telegram header height. Also call `tg.setHeaderColor('#01030a')` to blend header with app background (avoid jarring contrast). **Don't over-correct**: bumping padding-top way up (e.g. 60px) to "make room" can look BROKEN in the common launch mode and the user will tell you to revert. The overlap is inconsistent — it appears when the app is opened from OUTSIDE the chat (full-screen launch, native header shown) but is fine when opened from INSIDE the chat (inline, no native header). It's partly a device/Telegram-version quirk. Keep the modest `calc(20px + safe-area)` and don't chase pixel-perfect for every launch mode unless the user insists.

8. **USDT payment mode confusion**: Some users want USDT **only for direct purchase** (pay at checkout), NOT in top-up menu (avoids two-step flow: top-up → buy). Remove USDT from `PAYMENT_METHODS` array if requested: `{ key: 'usdt', ... }` line. USDT direct-purchase logic (`processUsdtPayment` at checkout) stays intact—just the top-up option is hidden. Ask user preference: "USDT di top-up atau cuma direct purchase?" before implementation.

9. **Hardcoded dummy notifications look shared across users**: A seeded `NOTIFICATIONS = [...]` array (promo/deposit/purchase samples) shows the SAME fake notifications to every user — user complains "kenapa notifikasi tiap user sama?". Same class of bug as any hardcoded demo data (see also the `totalBalance = 245000` hardcode). Fix: init `const NOTIFICATIONS = []` empty; let it fill only from real in-session activity (`addNotif()` on buy/topup/promo). If notifications must persist/sync per user, they belong in the backend keyed by user_id — not a frontend literal.

10. **Logout button is meaningless in a Telegram Mini App**: identity = Telegram user ID from `initData`, there is no session to end — tapping "Logout" does nothing useful (user stays logged in as the same TG account). When the user notices this ("logout kaya gaguna"), just REMOVE it from both the hamburger menu and the Profile page rather than trying to make it work. The freed hamburger-menu slot is a good home for a real toggle — in one session the BGM mute button was moved OUT of the Profile header and INTO this hamburger slot (label "Musik: On/Off"). A menu item with a text label is a DYNAMIC label that must be refreshed on language toggle — see pitfall #11's second bullet.

11. **Untranslated strings survive the i18n sweep — do a dedicated grep audit**: `applyTranslations()` only rewrites elements carrying `data-i18n`. TWO classes of text escape it and stay stuck in one language until you hunt them down:
    - **Hardcoded literals inside JS logic** — `showCustomAlert('Pilih metode pembayaran')`, `showPromoMsg('Kode sudah habis kuota')`, error concatenations like `'Gagal: ' + msg`. These never route through `t()`. Fix: add ID+EN keys and swap to `t('key')`. Audit with `grep -noE "showCustomAlert\('[A-Z][^']*'|showPromoMsg\('[A-Z][^']*'|'Gagal[^']*'|'Pilih[^']*'" app.js` — the hits inside the `id:`/`en:` i18n blocks are DEFINITIONS (fine); hits inside functions are the bugs.
    - **Dynamic labels set only inside their own render fn** — e.g. a `#mute-label` whose text is assigned inside `applyMuteState()`. On language toggle, `applyTranslations()` re-renders products/history/etc. but NOT that label, so it keeps the old language ("Musik" stays "Musik" when switched to EN). Fix: call the owning render fn from `applyTranslations()` too (`if (typeof applyMuteState === 'function') applyMuteState();`), and have it pick text via `currentLang === 'en' ? ... : ...` or `t()`.
    When the user says "ada beberapa bahasa yang ga ke-translate", run the grep audit across the whole `app.js`, not just the spot they named. EXCLUDE user-authored product/plan names — those follow `PRODUCT_I18N`/admin dual-language input, not the static `t()` dictionary, and the user will explicitly say to leave them alone.

12. **Deposit/transaction detail shows all "-" — frontend object keys must match what the renderer reads**: A very common self-inflicted bug. `confirmDeposit()` built the local `dep` object with keys `buyerName/buyerBank/buyerNumber` while `renderDepositDetail()` reads `senderName/senderBank/senderNumber` (and `recipientName/recipientNumber/recipientAccount`). Mismatch → every field renders `-` even though the data exists. Symptom: "pas approve/tolak topup, detail dana kosong, cuma muncul ID transaksi". Fix: build the `dep` object with the EXACT keys the renderer consumes, and populate `recipient*` from the `PAYMENT_METHODS` entry. When in doubt, grep the renderer for `dep.` to list the keys it expects before constructing the object.

13. **`sender_info` single-string round-trip (backend stores one column, frontend needs three fields)**: The backend `deposits.sender_info` is ONE text column; the frontend collects name+bank+rekening separately. Chosen convention: join with `" · "` on send (`buyer.name + ' · ' + buyer.bank + ' · ' + buyer.rekening`), and split back on load (`String(d.sender_info).split('·').map(x=>x.trim())`) in BOTH the Mini App history loader AND the admin panel deposit list. Keep the separator identical on both sides or the parse silently mangles. Enrich the `/api/deposits` SELECT to actually return `sender_info` (it was omitted originally) — the admin panel can't display what the API doesn't send. Transaction-ID sync is automatic once the Mini App uses the backend `deposit.id` as its `transId` (don't invent a separate client counter).

14. **Logout: remove from Mini App, ADD in browser**: Refinement of #10. In the Mini App logout is meaningless (identity = TG id, remove it). But in a plain-browser session (Login Widget → `x-web-token`) logout IS real: clear the web token from localStorage + `location.reload()` to return to the login screen. Gate it with `if (!inTelegram())` so it only appears in browser. Same idea for expired-token recovery: on a 401 from `/api/me` when `!inTelegram()`, drop the token and show the login screen instead of leaving a broken app.

15. **No local-only transaction state — backend is the single source of truth**: The original deposit flow had a client-side `waiting_pay` state with a 15-min JS timer that, on timeout, created a `failed` deposit LOCALLY that never hit the backend. Result: phantom "Gagal" records (e.g. `#7`) with all detail fields `-`, invisible to the admin, and confusing to the user. The professional rework: the moment the user confirms ("Saya Sudah Transfer"), POST straight to the backend (`/api/deposit/ewallet`) which persists everything and returns the canonical `deposit.id` used as `transId`; then render a simple "Menunggu Konfirmasi Admin" screen. DELETE the local timer, the `waiting_pay`/`held` auto-transitions, and any `createDeposit()` that mutates only client memory. Rule of thumb: if a status can change, only the server changes it (admin approve/reject); the client just reflects what the backend returns. Client countdown timers on money state read as "bug/telat" and orphan records that no admin tool can reach. When the user says "atur sebagus mungkin secara profesional", collapsing a multi-state local flow into one straight server-backed submit is usually the right move.

16. **"Refresh & cek saldo" reminder BEFORE the contact-CS text on the waiting-confirm screen**: With backend as source of truth (#15), an approved deposit only updates the client after a re-fetch. Users panic ("saldo belum masuk") when the admin already approved but their screen is stale. On the `waiting_confirm` detail screen, put — in this order — (a) a highlighted reminder note "sudah transfer? coba refresh dulu, mungkin admin sudah approve & saldo telat update", (b) a **"Refresh & Cek Saldo"** button that re-fetches `/api/me` (balance) + `/api/deposits` WITHOUT a full page reload and re-renders the top deposit (so an approved one flips to "Berhasil" in place), THEN (c) the "kalau setelah refresh tetap belum masuk >30 menit, hubungi CS" text. The refresh-first step deflects most CS contacts. Guard the `event`-based button-disable in the refresh fn (`refreshApp(ev)` with `ev || (typeof event!=='undefined'?event:null)`) so it works whether or not the inline handler passes `event`.

17. **Deposit failure has TWO distinct states — `rejected` (admin) vs `failed` (auto-expire) — with different messages**: Users want a topup that the admin explicitly declines to read differently from one that simply timed out. Model them as SEPARATE backend statuses, not one shared "failed":
    - **Admin reject** → status `rejected`. Message ≈ "Isi ulang credit di tolak, silahkan hubungi layanan pelanggan sesegera mungkin jika anda merasa sudah isi ulang credit." (`deposit_rejected_msg` + `deposit_status_rejected` = "Ditolak").
    - **Auto-expire** → after a grace window (user chose **24 jam**, NOT 30 min — 30 min stays pending) a `waiting_confirm` deposit flips to `failed`. Message ≈ "Isi ulang credit tidak pernah terdeteksi masuk, silahkan hubungi layanan pelanggan untuk detail lebih lanjut." (`deposit_auto_failed`).
    Backend does the expiry lazily: an `expireOldDeposits()` helper (`UPDATE deposits SET status='failed' WHERE status='waiting_confirm' AND created_at < now-24h`) called at the top of BOTH `/api/deposits` (user) and the admin deposits list — no cron needed. The admin `reject` endpoint sets `status='rejected'` (not `'failed'`). Frontend must thread the new status through EVERY status→color/text/icon/message switch (detail screen, history rows): both `rejected` and `failed` share the red-X icon and red color, but pull their own message + title. Miss one branch and you get a red X with the wrong (or blank) caption. The judul ("Gagal"/"Ditolak") and the top + bottom description must all be the SAME wording — user explicitly asked to "samakan saja". Keep the human copy in `references/admin-panel-extras.md` so it survives verbatim.

18. **Web session tokens MUST persist in the DB, not an in-memory Map**: The browser Login Widget flow (see auth section) originally stored issued session tokens in a JS `Map` in `auth.js`. Every backend restart (and you restart a LOT during dev — see deploy notes) wiped the Map → every browser user's token became invalid → \"unauthorized, gak bisa transaksi/beli di web\". Symptom is maddening because it works right after login then breaks after any redeploy. Fix: persist sessions in a `web_sessions` table (`token PK, user_id, name, username, created_at`); `createWebSession` INSERTs, `getWebSession` SELECTs + lazily deletes rows older than the TTL (e.g. 30 days). Mini App (initData) auth is unaffected because it re-verifies per request — only the browser token path needs persistence. Rule: anything that must survive a process restart belongs in SQLite, never a module-level `Map`/object.

19. **Duplicate function name silently shadows — one canonical `buildPagination`**: Adding a second `function buildPagination(...)` with a DIFFERENT argument order (e.g. `(totalPages, current, fn)` vs the existing `(current, totalPages, fn)`) doesn't error — the later declaration wins via hoisting and every OLD caller now passes args in the wrong order, breaking pagination that used to work. When you want a shared helper, grep first (`grep -c \"^function buildPagination\" app.js` must be 1) and reuse the existing signature. General rule for this large single-file `app.js`: before defining a helper, `grep` its name — the file already has helpers for pagination, i18n, history rendering, etc.

20. **`req.params.id | 0` TRUNCATES Telegram user IDs — never bitwise-coerce a user_id**: Express route params come in as strings; a common shorthand to intify is `req.params.id | 0`. But `| 0` forces a **signed 32-bit** integer (max ~2.1 billion), and Telegram user IDs are already well past that (e.g. `<OWNER_CHAT_ID>`). Result: the id wraps to a wrong/negative number → `WHERE id=?` matches nothing → \"user_not_found\" / empty tracking, even though the row exists. Symptom this session: admin \"View all from this user\" returned `user_not_found` for a real user. Fix: `const uid = Number(req.params.id) || 0;` (or `parseInt`). SAFE to use `| 0` on small autoincrement PKs (deposit/order ids) but NOT on anything holding a Telegram user_id. When a by-id lookup mysteriously finds nothing for a big id, suspect 32-bit truncation first.

21. **Changing the DEFAULT language exposes that `applyTranslations()` never ran at init**: The storefront's `currentLang` was hardcoded `'id'` and `init()` NEVER called `applyTranslations()` — it \"worked\" only because the HTML `data-i18n` fallback text WAS Indonesian, so the untranslated DOM happened to match the default. The moment you switch the default to English (user: \"jadikan default bahasa inggris\"), every `data-i18n` element still shows its Indonesian fallback until the user manually toggles, because nothing applied the dictionary on load. Fixes, all three together: (a) initialize `currentLang` from a persisted value with English default — `localStorage.getItem('lang')` else `'en'`; (b) **call `applyTranslations()` at the end of `init()`** so the active language paints on first render regardless of the HTML fallback; (c) persist the choice in `toggleLang` (`localStorage.setItem('lang', currentLang)`) so it survives reloads. Also flip any hardcoded-language fallbacks that render before init (login-screen copy, \"Verifying…\"/\"Login failed\" widget strings, a default profile name like `'Pengguna'` → `'User'`) to the new default language. Verify with a whole-file grep of the EN i18n block for stray Indonesian words (`Metode`, `Pilih`, `Saldo`, `Kembali`, …) — this session caught `deposit_payment_method: 'Metode Pembayaran'` and `profile_guide_title: 'Panduan'` (should be `'Guide'`) sitting in the EN block. Product/plan data stays user-controlled (`PRODUCT_I18N`/admin) — exclude it from the sweep.

22. **Global unique transaction ID across top-ups AND orders (proof-of-transaction)**: The user wanted a single unforgeable transaction number that is unique system-wide — NOT `deposits.id` and `orders.id` (both autoincrement from 1, so top-up #1 and purchase #1 collide). This confuses admins tracking a transaction and undermines it as a \"bukti transaksi sah\". Pattern: a shared counter in a `meta(key TEXT PK, val INTEGER)` table (`INSERT OR IGNORE ('txn_seq', 0)`), a `nextTxn()` helper (`UPDATE meta SET val=val+1 ... ; SELECT val`), and a `txn INTEGER` column on BOTH `deposits` and `orders`. Every insert (ewallet deposit, USDT deposit, credit buy, USDT order) calls `nextTxn()` and stores it. **Migration for existing rows**: `addColumnIfMissing` both tables, then backfill in ONE pass — `SELECT 'deposit' kind,id,created_at FROM deposits WHERE txn IS NULL UNION ALL SELECT 'order',id,created_at FROM orders WHERE txn IS NULL ORDER BY created_at ASC`, assign sequential txn, update `meta.txn_seq` to the max. Return `txn` in every user-facing response (`/api/buy/credit`, `/api/deposit/ewallet`, `/api/orders`, `/api/deposits`) and the mini app uses `txn` (fallback `|| id`) as its displayed `transId`/order-id and its history search key. Admin gets ONE unified `GET /api/admin/track/:txn` that tries `deposits WHERE txn=?` then `orders WHERE txn=?` and returns `{type:'topup'|'order', ...}` so a single search box auto-detects which kind it is. Verify no duplicates after migration: collect all txn from both tables into a Set, assert `size === length`.\n\n23. **Approve/reject HIDES a top-up but it stays trackable forever**: The user's mental model — pending deposits show in an actionable list; once approved/rejected they \"go to hide, not disappear\" but can STILL be looked up by transaction ID any time. Implementation is automatic if the pending list filters `status='waiting_confirm'` (so approved/rejected drop off it) while the by-txn track endpoint queries with no status filter (so it finds them in any state). Test the full lifecycle: create → track (finds it, pending) → approve → track again (still found, now `success`) → pending list no longer contains it. Don't delete rows on reject; set `status='rejected'` (see pitfall #17).\n\n24. **Admin panel consolidation: kebab menu + inline row actions (user minimalism)**: The user keeps pushing the admin panel toward less chrome. Two moves that landed well: (a) collapse header actions (Reset Summary + Sign Out) into a SINGLE kebab/three-dot icon menu (`.kebab` absolute-positioned dropdown, toggled by a ⋮ button, closed on outside-click via a document listener that ignores clicks inside `#kebab` or the trigger) — frees the top bar to just brand logo + name + ⋮. (b) Product list rows put the title and its Edit|Clear actions on ONE flex row (`ChatGPT Plus            ✎ Edit | 🗑 Clear`) instead of stacking a full-width button row below — `.item-row` with `align-items:center`, title on the left, a `.prod-actions` inline-flex group on the right with borderless transparent icon-buttons and a thin `|` separator. General direction: when unsure, make admin actions smaller/inline/icon-first, not bigger.\n\n25. **Don't gate the product-detail CTA on Credit balance when direct-pay methods exist**: The detail page had its buy button disabled / showing "Insufficient Balance" whenever `totalBalance < price`. But with multiple payment methods (Credit, IDR Direct, USDT Direct), a zero Credit balance must NOT block checkout — the user can still pay directly. Symptom: "kalo di detail produk saldo credit 0 tidak bisa lanjut ke pembayaran direct usdt/idr". Correct separation of concerns:
    - **Product detail is STOCK-gated only.** Button label = "Continue Payment" / "Lanjutkan Pembayaran" (`detail_continue`), enabled whenever `stock > 0`. Never reference balance here.
    - **The Payment screen owns method selection + the balance check.** The insufficient-balance test runs ONLY for the credit method: `var insufficient = method === 'credit' && totalBalance < pm.price`. For IDR/USDT Direct the Continue button stays enabled regardless of Credit balance.
    - **Reset `window._selectedPayMethod = 'credit'` in `openProductDetail`** (fresh entry to the product), NOT inside the per-method `buyProductDetail`/`buyProduct` handlers — otherwise re-entering the payment screen after switching methods desyncs the default.
    - The credit-insufficient message steers to a direct method: "Saldo Credit tidak mencukupi. Pilih IDR Direct atau USDT Direct untuk membayar tanpa saldo Credit." Each method needs its own hint key (`payment_idr_direct_hint`, `payment_usdt_direct_hint`, `payment_method_unavailable`, `payment_credit_ready`, `payment_credit_insufficient`) in BOTH `id` and `en` blocks — verify with a grep that all keys resolve, or they render as raw key strings.
    - Regression-test this as a source assertion (the payment-render section must NOT contain `pay-method-option disabled`, and the credit check must be method-scoped), so a future edit can't silently re-gate the detail page.

26. **Calling a helper that doesn't exist = silent loading-spinner hang (no visible error)**: A new flow that does `showLoading(null)` then routes via a helper that DOESN'T EXIST (this session: I wrote `navigateTo('payment-info')` and `showCatalog()` when the app only has `goTab(name)`) throws a ReferenceError mid-handler. The overlay is already up, the throw aborts before it's removed, and the async/onclick boundary swallows the error — so the user just sees an infinite spinner and reports \"gabisa diklik / muter terus\", NOT an alert. Before wiring any new screen transition, grep every helper you're about to call: `grep -c \"function <name>\\|<name> =\" app.js` must be ≥1 each. This app navigates with `goTab('<x>')` → shows `#screen-<x>`; there is NO `navigateTo`/`showCatalog`. Diagnosis heuristic: \"spinner stuck forever after tapping X\" ≈ an exception in X's handler after `showLoading` — most often an undefined function or a bad `getElementById` id. (Adding a new dedicated screen also means adding `<div class=\"screen\" id=\"screen-<x>\">` in index.html — see pitfall #1.)\n\n27. **Termux server dies + duplicate stale processes serve OLD routes**: Two related ops hazards on Termux. (a) Android kills the `node` process on low battery/memory while `cloudflared` keeps running → domain returns **502** (tunnel up, no backend). Durable fix: a `keepalive.sh` watchdog that calls `termux-wake-lock` once then loops every ~30s hitting `/health`, and on failure `pkill`s + relaunches `start.sh`; run it via `terminal(background=true)`. Tell the user wake-lock raises battery use. This is the answer to \"kenapa toko gua mati sendiri\". (b) After editing backend routes, NEW endpoints 404 and existing ones misbehave because a PREVIOUS `node server.js` never died and still holds the port — `pgrep -af 'node server.js'` shows TWO PIDs and the bot logs `Conflict: terminated by other getUpdates`. `pkill -f` sometimes misses it and the watchdog will happily spawn a second instance too. Force-clean: `pkill -9 -f keepalive.sh; pkill -9 -f 'node server.js'; sleep 3`, confirm `pgrep` empty, THEN restart (and restart the watchdog after). Also: this admin router authenticates via an `x-admin-token` header (POST `/api/admin/login` first to mint the token), NOT HTTP Basic — `curl -u user:pass` will 401 and send you down the wrong debugging path. A bare `Cannot GET /api/admin/<x>` (Express default 404 body) means the route truly isn't registered on the running instance = you're hitting a stale server.\n\n28. **USDT on-chain "waiting verification" screen must REUSE the deposit waiting-confirm template, not a bare card**: When the user submits a tx hash and the order goes to `waiting_confirm`, don't render a minimal `⏳` emoji card. The user expects the SAME treatment as the top-up waiting screen (`renderDepositDetail` for `waiting_confirm`): a colored circular clock icon (`payment-success-icon` with `var(--ios-blue)` bg/color), a title + message, a `deposit-refresh-note` hint block with the refresh SVG, a full-width **"Refresh & Check Status"** button that manually re-fetches `/api/order/:id` and flips to the delivered/credentials view in place (don't rely only on the 5s poll), plus a `payment-btn-row` with **Cancel** (`goTab('payment')`) + **Contact Admin** (`contactCS()` → opens the CS Telegram link). Add matching i18n keys in both blocks (`onchain_refresh_hint/btn`, `onchain_cs_btn`, `onchain_still_waiting`). NOTE: the deposit flow deliberately has NO local countdown timer (waits for admin indefinitely, backend is source of truth per #15) — so the USDT waiting screen also has no timer; if the user asks for a countdown, that needs extra auto-cancel + stock-restore logic. The refresh handler is `refreshOnchainOrder(ev)` storing the id in `window._onchainOrderId`; guard the button-disable with `ev && ev.target`.

29. **Credit Balance display placement (user preference): fold it into the payment-method status line, not the Order Summary**: The user did NOT want a standalone "Credit Balance Rp X" row inside the Order Summary card. Preferred shape: Order Summary shows only product/duration/warranty/Total; the balance surfaces in the payment-method **status message** as `'Your Credit Balance ' + formatRupiah(totalBalance)` and ONLY when the credit method is selected (the IDR/USDT hints replace it for other methods). This kills the duplicate-balance-info look. Implement in `updatePaymentAction()`'s status branch, not by toggling a separate `#payment-credit-row` element.

30. **Killing a payment method entirely ("hapus aja, tulis sedang perbaikan") — feature-flag + endpoint 503 + pending cleanup, not a UI-only hide**: When the user decides to REMOVE a payment method rather than fix it (their words: "hilangkan fitur payment direct usdt manual... kasih tulisan aja sedang perbaikan"), do all THREE layers or the feature keeps leaking:
    1. **Backend**: make the checkout-creating endpoint(s) a no-op returning `503 {ok:false, error:'FEATURE_MAINTENANCE'}`. Find EVERY endpoint that creates an order for that method — this session there were TWO (`/api/buy/usdt` legacy invoice AND `/api/buy/usdt-onchain`), and missing the legacy one is exactly how pending orders kept accumulating even after the "new" flow looked fixed. Grep `purchase\.(create|buy)` in `server.js` for every call site, don't rely on memory of "the" endpoint. Keep `cancel`/`submit-tx`/status-polling routes alive so any order ALREADY at `waiting_confirm` (tx hash submitted) can still be resolved by admin — don't strand real pending payments.
    2. **Frontend**: add a single `var METHOD_ENABLED = false` flag near the top of `app.js` (not scattered `if`s). Every place that renders the method's button/menu-option must read this flag: (a) render with a `maintenance` CSS class + disabled onclick + the existing `deposit_maintenance`/`detail_crypto_maintenance` label pattern already used for other disabled methods, (b) the confirm/checkout handler must ALSO check the flag server-independently and show a status message instead of calling the API (defense in depth — don't rely on the button being unclickable alone), (c) if `window._selectedPayMethod` can persist across renders (e.g. from a previous session), force it back to a safe default (`'credit'`) when the disabled method is currently selected, in the render function itself — otherwise a returning user with the old method pre-selected sees a dead screen.
    3. **Data cleanup**: after disabling, sweep EXISTING pending orders for that method (`status='pending'`) and flip them to `expired` + release any reserved stock (`UPDATE stock_items SET status='available',order_id=NULL WHERE order_id=? AND status='reserved'`). Backup the DB file first (`cp -p db.db db.db.backup-<feature>-disable-$(date +%Y%m%d-%H%M%S)`). Do NOT touch rows already at `waiting_confirm` or `delivered` — those are real transactions, only pre-payment abandoned checkouts get expired.
    Write a regression test that greps the disabled endpoints for the 503/FEATURE_MAINTENANCE literal AND greps the frontend for the flag + maintenance label — this is a source-assertion test (no live server needed) and it's what actually caught that the legacy endpoint was forgotten in an early pass.
    **Removing the "(soon)"/"(segera)" text label later — check the disabled-LOOK CSS exists for THAT specific button type before deleting the text**: once the feature has been maintenance-flagged for a while, the user often asks to drop the parenthetical label (e.g. "usdt soon nya hapus tulisan") because it reads awkward once permanent. The text was carrying 100% of the "this is disabled" signal if the `.maintenance` class was never styled for that button's specific selector. This codebase has TWO different maintenance-styled components that don't share CSS: `.payment-chip.maintenance` (deposit/top-up method chips) and `.pay-method-option.maintenance` (checkout payment-method buttons) — only one may have an `opacity`/`cursor` rule defined. Before deleting the text label, grep the stylesheet for the exact class+component combo (`.pay-method-option.maintenance` not just `.maintenance`) and add the dimmed/disabled look (`opacity:0.5; cursor:not-allowed;`) if it's missing, or the button silently becomes indistinguishable from an active one with no text cue at all.

31. **Blank screen ≠ leftover artifact — wrong `goTab()` target entirely (dual-screen `payment-content` vs `payment-info-content`)**: A DIFFERENT bug from pitfall #1's leftover-artifact case. A function writes real HTML into ONE dynamic-content div, writes `''` into the sibling div, then calls `goTab('payment')` or `goTab('payment-info')` — but the div it just populated isn't the one that target screen actually renders. Result isn't a stray artifact, it's a genuinely BLANK screen (user report: "kok kosong / gak muncul / ga bisa diklik"). One session hit FIVE call sites at once, all leftover from an earlier refactor that split the two screens: deposit top-up detail status (incl. GoPay), FAQ/Guide (`openInfoPage`), "view all" purchase history, "view all" deposit history, and the USDT-waiting screen. Fix: match the codebase's DOMINANT convention for the pair (in MyStore, `payment-content` + `goTab('payment')` is used by ~90% of call sites, incl. the one-time checkout flow); only the onchain USDT address/QR flow legitimately uses `payment-info-content` + `goTab('payment-info')`. **Audit, don't spot-fix one-by-one** — a targeted regex sweep over every function body (which div gets non-empty `innerHTML`, vs which `goTab()` is called in the same function) catches ALL mismatches in one pass instead of waiting for the user to report each dead screen individually. Reusable script: `scripts/audit-screen-div-mismatch.js path/to/app.js [divA] [divB]` — run it after ANY refactor that touches dual-screen dynamic-content routing, not just when a user reports a specific blank screen. Also sweep for orphaned functions while you're in there (`grep -c 'functionName(' app.js` === 1 means it's defined but never called — dead code from the same class of refactor, safe to leave but worth flagging). **Reuse `scripts/audit-screen-div-mismatch.js` directly** (`node <skill_dir>/scripts/audit-screen-div-mismatch.js path/to/app.js`) instead of re-deriving the same regex sweep inline with execute_code — it already encodes this exact check and exits 1 on any mismatch, so it doubles as a quick regression gate after touching dual-screen routing.

32. **Truncating a long copyable value (URL/credential) — ellipsis a nested text span, not the row itself, and never touch the copied data**: `.credential-val`/`.copyable-row` rows sometimes need to display a long value (an order URL, a wallet address, a long credential line) that otherwise wraps ugly across 3-4 lines. Two related mistakes to avoid: (a) applying `text-overflow:ellipsis` directly to the SAME element that also contains the copy-icon `<svg>` clips or hides the icon along with the text — `overflow:hidden` cuts off anything past the container edge, icon included; (b) truncating for DISPLAY must never truncate the value actually handed to the copy function (`copyText()`/`copyB64()`) — that call still gets the full untouched string, only the rendered `<span>` is shortened, so what the user copies is always complete and correct even though what they see is a clipped `...`. Fix: wrap just the text in its own inner `<span class="credential-val-text">` with `overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0`, keep the copy icon as a separate sibling element outside that span, and make the parent (`.credential-val`) a `display:flex;align-items:center;justify-content:flex-end;gap:4px` row so text and icon lay out side-by-side (`justify-content:flex-end` is needed to preserve the original right-aligned look once you switch the parent from `text-align` to `display:flex`). Rows WITHOUT a label sharing space (a full-width single value — e.g. this app's unlabeled `renderCredentials` branch, `style="width:100%"`) need an explicit inline `max-width:100%` override on that specific row — the shared `max-width:60%` meant for label+value pairs will over-truncate a full-width value.

33. **Two distinct "not implemented yet" treatments for a payment method — don't default to the harder one**: Pitfall #30 covers fully KILLING a method (503 endpoint, `.maintenance` CSS, disabled onclick, "(soon)" label). But the user may instead want the SOFTER treatment already used by another unimplemented method in the same screen — e.g. this app's "IDR Direct" was never wired to a backend either, yet it was never maintenance-styled: it renders normal (selectable, highlights, checkmark shows), and only fails when the user taps the final confirm button, via one generic `if (method !== 'credit') { showError('payment_method_unavailable'); return; }` branch. When the user says "samakan seperti [X], bisa dipilih tp gabisa diklik" (or similar — "selectable but can't proceed"), that's asking to CONVERT a hard-killed method to this soft pattern, not to build something new. Concretely, undo pitfall #30's hard-kill markup for that one method:
    - Remove the `<method>_ENABLED` flag's effect on the button markup: no more `(ENABLED ? '' : ' maintenance')` class and no more conditional/empty `onclick` — the button always gets `onclick="selectPayMethod(this, '<method>')"` like every other selectable method.
    - Delete the method-specific branch inside the confirm handler that intercepts BEFORE the generic `method !== 'credit'` check (e.g. a `if (method === 'usdt') { ...call startBuyX(pm)... }` block) — deleting it lets execution fall through into the same generic rejection every other soft-disabled method already uses. Do NOT leave the old branch calling a checkout starter function that hits a 503 endpoint; that reintroduces a dead click path.
    - Remove any method-specific button-disabling logic elsewhere (e.g. a `usdtBelowMin` check that set `btn.disabled = true`) if the sibling method (IDR) never disables the button either — parity means the button is never disabled for either method, only the confirm action reports "not available".
    - Leave the backend 503 + pending-cleanup layers from pitfall #30 untouched — this is a FRONTEND presentation change only; the checkout endpoint should stay dead regardless of how the button looks.
    - Update any source-assertion regression test written for the hard-kill state (checking for the `.maintenance` class or the `ENABLED` flag gating the button) to instead assert the soft pattern: button always clickable via `selectPayMethod`, and the confirm handler's generic `method !== 'credit'` branch is what rejects it (grep for the specific `onclick="selectPayMethod(this, '<method>')"` string and the absence of the old per-method confirm branch, rather than matching a stale flag name).

34. **"Ada yang mau DDOS/brute force admin panel" — check live health first, then harden, don't panic-rebuild**: When the user reports a suspected attack, the response has three phases, in order: (1) **verify current state** — `curl /health`, check active TCP connections, check recent admin `sessions` rows for anomalies (all one `admin_id` recently = probably just the owner re-logging-in, not an attacker) — don't assume an attack is in progress without evidence, and say so plainly if you find none; (2) **harden regardless** — lack of evidence of a past attack is not evidence the endpoint is safe; `/api/admin/login` with zero rate limiting is a real vulnerability whether or not it's been exploited yet; (3) **verify the fix live** — actually fire 6-7 bad login attempts at the running server and confirm the 401→429 transition, don't just trust unit tests. The full implementation (Cloudflare Tunnel IP-detection gotcha, in-memory sliding-window limiter, dual-axis IP+username login guard, wiring recipe, live verification commands) is in `references/rate-limiting-and-brute-force.md` — reuse `rate-limit.js` wholesale rather than re-deriving it, it has no external dependency. Key point the user won't think to ask about: **this codebase's only public exposure is Cloudflare Tunnel with no local reverse proxy**, so `req.ip` in Express is always loopback and a naive rate limiter would silently do nothing — always grep `~/.cloudflared/config.yml` and running `cloudflared` processes before choosing an IP-detection strategy. Also always exempt `/health` from the limiter or the watchdog script (`keepalive.sh`) can trip it and cause a false-positive restart loop.

35. **Editing an asset in `app.js` does NOTHING when the frontend loads it from the backend catalog DB**: This app's `loadBackendData()` overwrites the in-memory `PRODUCTS` array (icons included) from `GET /api/catalog`, which serves whatever is stored in the `products.icon` DB column. So changing the placeholder "?" icon (or any product icon/color) in the `app.js` literal has ZERO visible effect — the DB value wins on every load. Symptom: user says "masih abu / gak berubah" after you've clearly edited the JS and bumped `?v=`. Diagnosis: any product/plan field that the catalog API returns (icon, name, color, price, stock) is DB-sourced; the `app.js` literal is only a seed/fallback used before the fetch resolves. Fix in TWO places at once: (a) run a DB update for existing rows — e.g. `UPDATE products SET icon=? WHERE placeholder=1` via a one-off `node -e` using `node:sqlite` against `MyStore.db`; (b) fix the code that REGENERATES the value so it doesn't reintroduce the old asset — here the admin "reset to placeholder" endpoint (`admin.js`) had a hardcoded `phIcon` SVG string with the old faint-white `rgba(255,255,255,0.3)` fill that would overwrite any fixed row the next time a slot was cleared. Verify against the live API, not the source: `curl -s localhost:<port>/api/catalog | grep -o 'fill="[^"]*">?'`. General rule: before editing a visual value in the frontend literal, grep for a backend catalog/DB column of the same name — if it exists, edit the DB (and the code that writes it), not just the literal.

## Cloudflare Tunnel down: 502 vs 530 tells you which half died

"gabisa buka <domain>" on a Termux-hosted Mini App is almost always a dead process, NOT a
DNS/dashboard change. The Cloudflare error code pinpoints which half:
- **502** = the tunnel is up but the backend `node` process is dead → restart `start.sh`
  (or let `keepalive.sh` do it). Same root cause as "kenapa toko mati sendiri" (Android
  killed node on low memory/battery).
- **530 / "error code: 1033"** = the `cloudflared` tunnel process ITSELF is dead → relaunch
  it in background: `cloudflared tunnel --config ~/.cloudflared/config.yml run <TUNNEL_ID>`.
  The named-tunnel `config.yml` + credentials `<TUNNEL_ID>.json` already live under
  `~/.cloudflared/`, so there is NO re-login / `tunnel login` / re-auth needed — just re-run.

Diagnose in one shot BEFORE touching the Cloudflare dashboard or DNS records:
```bash
curl -sI https://<domain>          # read the HTTP code (502 vs 530)
pgrep -a cloudflared               # is the tunnel process alive?
curl -s localhost:<port>/health    # is the backend alive locally?
```
The tunnel config maps `hostname: <domain> → service: http://localhost:<port>`; if localhost
answers but the domain 530s, it's purely the tunnel process. DNS/CNAME is essentially never
the cause when the domain worked before — don't go editing records.

## Stock sourcing: refuse stolen/fraud goods, steer to legal B2B suppliers

This storefront sells digital goods, so the operator WILL eventually float sketchy
stock sources. This session the user probed several — all must be declined, then
redirect to legal sourcing (don't just refuse and stop):
- **Stolen-account dumps / "cookie" archives** — files/archives of account credentials
  or session cookies (often tagged with a "checker"/"HYDRA" tool name), "cookie
  injector" browser extensions (e.g. a Netflix "cookie injector" loaded unpacked),
  "cookie → link" session-hijack tricks. This is account theft / session hijacking.
- **Fraudulent free-credential schemes** — fake university admissions to mint `.edu`
  emails for student discounts; carding flows for ChatGPT Plus (steal
  `chatgpt.com/api/auth/session`, pay via a stranger-scanned UPI/QR). These are
  application fraud + payment fraud.
Response pattern: (1) name plainly what the thing actually is and that it's illegal,
(2) decline to build/process/integrate it, (3) add the self-interest angle — pirate
tools loaded unpacked can drain the user's OWN wallet/browser; stolen accounts get
reclaimed → chargebacks/refunds/gateway ban kill the store, (4) steer to LEGAL stock:
Indonesian B2B digital-goods marketplaces **Digiflazz / VIP Reseller** (game top-up,
vouchers, PLN token, pulsa — have APIs, no chargeback risk), official reseller programs,
or self-made products. The user's stated line ("kalo ilegal ya ga saya lakuin") means a
clear legal/illegal verdict + a legal alternative is more useful than a lecture.

## Full-stack bug-audit sweep (admin panel + Mini App + web + bot)

When the user says "cek bug di panel admin, mini apps, web utama, bot chat?" or "fix bug menyeluruh", run a concrete static + live probe sweep across all four surfaces — the passing test suite alone is NOT the answer they want. Full copy-paste recipe in `references/full-stack-audit-methodology.md`. Load-bearing gotchas learned this session:
- **Admin routes are mounted with a prefix**: `app.use('/api/admin', admin.router)` in `server.js`, and the handlers live in `admin.js` as `router.get('/stats', ...)`. A regex that only scans `server.js` for `app.get/post` reports every admin endpoint as "missing route" (false positive). Parse `router.(get|post|put|delete)` from `admin.js` and prepend `/api/admin`.
- **Suffix-bearing routes break naive segment matchers**: `/deposit/:id/approve`, `/deposit/:id/reject`, `/user/:id/history`, `/stock/:id` (DELETE). A matcher that splits on `/` and truncates after the id will flag these as unmatched — confirm them by grepping `admin.js` directly instead of trusting the matcher output.
- **The high-value checks that actually catch bugs**: (1) onclick→defined-function ("MISSING onclick handlers: none" is the pass), (2) frontend `apiGet/apiPost/api()` calls → backend route existence, (3) auth-guard probes (`curl` every admin + user endpoint expecting 401 without a token, public endpoints expecting 200), (4) DB-integrity one-liners (duplicate global txn, orphan plans/stock, delivered-without-credential, stuck outbox — all should be 0), (5) live asset hash-sync (`sha256sum` the deployed `app.js?v=N` vs local). `Telegram polling error: fetch failed` in the log is transient network, NOT a bug, as long as `getMe` succeeds and the loop has retry+backoff.

## Admin panel: English UI, minimalist, search-by-ID history (user preferences)

The user's settled preferences for the admin panel (distinct from the Mini App, which is ID/EN bilingual):
- **Admin interface text is ENGLISH-only.** Only the DATA (product/plan names, descriptions) stays Indonesian because that's what shows in the storefront. So: buttons, tabs, labels, headings, toasts → English (\"Sign In\", \"Overview\", \"Products\", \"Top-ups\", \"Orders\", \"Add Stock\", \"Reset\"). Don't bilingual-ize the admin chrome.
- **Minimalist, space-frugal.** Small font, tight spacing, no oversized buttons. Edit/Clear/Delete/Reset become small ICON buttons (pencil for edit, trash for delete/reset) with a tiny text label — not full-width bars. Reset Summary in particular is a small trash-icon button, not a big red slab.
- **Tabs WRAP, never horizontal-scroll.** `display:flex; flex-wrap:wrap; gap` on the tab row so tabs flow to a second line on narrow screens instead of a swipeable strip.
- **Transaction & top-up history are HIDDEN by default, revealed by search.** The user does NOT want long scrolling history lists in the admin. Current shape (evolved): Top-ups tab shows ONLY pending (actionable) deposits; a single **\"Track\"** tab has ONE search box that looks up ANY transaction by its global `txn` id via `GET /api/admin/track/:txn` (auto-detects top-up vs purchase — see pitfall #22) and renders the same detail card + a \"View all from this user\" button (`GET /api/admin/user/:id/history` returns that user's full orders+deposits). This keeps old transactions trackable without rendering thousands of rows. (Earlier split `GET /api/admin/deposit/:id` + `/order/:id` was merged into the unified `/track/:txn`.)
- Empty the login username field (no `value=\"admin\"` prefill), and there's no \"Admin Panel\" heading — just the brand logo + name.

Mirror on the Mini App side: history pages (top-up + purchase) also get a **\"Cari ID Transaksi\"** search box (`search_trans_id` i18n key, filter by `transId`/order `id`), because users need to track old transactions there too. Detail rows already carry time + warranty timer; the search just filters the existing list.

## Admin panel extras (reset-stats, notes, clean UI)

Beyond CRUD/stock/promo/deposit, two admin features recur and a UI-quality bar applies. Full recipe in `references/admin-panel-extras.md`:
- **Password-confirmed destructive reset** — "Reset Ringkasan" wipes `orders`+`deposits` history but NOT products/stock/users/balance. Re-verify the admin's own password server-side (`verifyPassword` against `admins` row) inside the endpoint, and gate the client behind a modal that asks for the password again. Never make a destructive admin action a one-tap button.
- **Notes / catatan** — a `notes` table (id, product_id nullable, title, body, timestamps) with CRUD, LEFT JOIN products for a display tag, lets the admin jot per-product credentials/reminders. Optional product link (`product_id` nullable = "Umum").
- **Clean admin UI bar** — the user WILL ask to make the admin panel "clean & profesional". Key-value rows (`.item-row` with `.k`/`.v`), a stat grid of boxes (not a flat list), brand header + logo, badges for status, monospace block for credentials, empty-state text, and a proper modal (not `confirm()`) for destructive confirms. Empty the login username field (no `value="admin"` prefill).

## Testing Checklist

- [ ] Refresh app, CSS/JS version incremented
- [ ] All icons render (no 404s) at correct size
- [ ] Animations smooth on mobile (no stutter)
- [ ] Long-press on images blocked (no context menu)
- [ ] Language toggle works (ID ↔ EN)
- [ ] Product grid fills exactly 5 rows

## References

- `references/webview-ui-techniques.md` — copy-paste recipes: synced icon light-sweep + blend-mode pitfall, gradient anti-banding, no-scroll dashboard, pagination helper, product-name labels, image long-press block, bulk PRODUCTS reorder/delete.
- `references/backend-admin-payments.md` — full-stack recipe: Node+Express backend, `node:sqlite` on Termux (no better-sqlite3, manual BEGIN/COMMIT tx), server-side credential security, NOWPayments USDT + IPN verify, admin panel (PBKDF2 auth), credit vs direct-pay flows, seeding DB from the frontend PRODUCTS literal, and the flexible multi-line credential renderer.
- `references/payment-gateway-migration.md` — migrate payment providers (NOWPayments → USDT on-chain manual-verify): backend new endpoint, frontend USDT_MIN=0 pitfall, admin approve flow, double-server debugging, rollback plan.
- `references/cloudflare-tunnel-deploy.md` — deploy backend to public HTTPS via Cloudflare Tunnel: quick tunnel (temp URL, instant test) vs. named tunnel (permanent domain), domain purchase (`.web.id` ~Rp15k/year), nameserver setup, BOT_TOKEN wiring, BotFather + NOWPayments IPN config, troubleshooting "pending" domain + 401 auth errors.
- `references/i18n-migration-pattern.md` — safe ALTER TABLE migration adding i18n columns to existing DBs (CREATE IF NOT EXISTS won't add columns), one-time seed from frontend PRODUCT_I18N literal, dynamic PRODUCT_I18N loading from backend catalog with legacy fallback.
- `references/promo-code-system.md` — atomic promo code claim (tx-wrapped, race-safe), admin CRUD, frontend claim flow (POST-first, no client validation, trust server balance), verified 5-step test sequence.
- `references/ios-loading-splash.md` — CSS-only loading splash: staggered entrance (logo→text→spinner), breath+glow animation, `hideSplash()` after data load + 3s fallback timeout, PIL logo compression (1.5MB→178KB), `showLoading()` null-guard pitfall.
- `references/telegram-login-widget-browser.md` — browser auth via Telegram Login Widget: SHA256(bot_token) verify (differs from initData's HMAC), in-memory session tokens + `x-web-token` header, auth precedence chain, `/setdomain` requirement, dynamic widget injection, expired-token→re-login recovery, no-browser test harness.
- `references/bgm-integration.md` — looping background music + mute toggle: autoplay-blocked reality (start on first click/touch `{once:true}`), localStorage-persisted mute, volume ~0.3, guard getElementById for controls in unrendered tabs.
- `references/announcements-and-reset.md` — Ads/announcement broadcast (admin writes custom promo/maintenance/info → appears in Mini App notifications via public `/api/announcements`; `active` toggle; dedup on fetch; string-vs-numeric notif-id onclick bug), and the full account-reset procedure (backup DB → wipe users/deposits/orders/sessions → reset `txn_seq=0` → restore sold→available stock → keep catalog/config).
- `references/admin-panel-extras.md` — password-confirmed "Reset Ringkasan" (wipes orders/deposits only, re-verifies admin password server-side), notes/catatan table + CRUD, the deposit-detail field-key bug (all "-"), `sender_info` join/split round-trip, and the clean-admin-UI checklist (stat grid, key-value item rows, modal confirms, empty login username).
- `references/backend-and-deploy.md` — **DEPLOY + full-stack recap**: Cloudflare Tunnel from Termux (`pkg install cloudflared`, `cloudflared tunnel --url`, quick-tunnel URL changes on restart → named tunnel/VPS for prod), restart hygiene (`pkill -f "node server.js"` or you serve STALE code — phantom 404s/missing columns), pre-deploy security grep on `/api/catalog`, BotFather + NOWPayments IPN wiring (user enters secrets, never via chat), node:sqlite/tx/migration/payments recap.
- `references/purchase-idempotency-and-history-limits.md` — cached-client compatibility refs, purchase response isolation, independent 8-item preview/12-item full-history pagination, cross-renderer status sweeps, and live asset/health verification.
- `references/full-stack-audit-methodology.md` — copy-paste "cek bug menyeluruh" sweep across all four surfaces: syntax check, test-suite runner, onclick→defined-fn audit, frontend-API→backend-route check (with the `app.use('/api/admin', admin.router)` prefix gotcha), live auth-guard 401 probes, DB-integrity one-liners, asset hash-sync, and the transient `Telegram polling error` non-bug.
- `references/crypto-payments-and-onchain.md` — crypto payment class notes: gateway minimum trap (invoice page \"muter\" = amount below the create-payment floor the `/min-amount` probe hides; probe the REAL floor via `POST /v1/payment` with delay/429-retry), USD vs direct-crypto pricing, IPN 200-vs-500 retry-storm rule, and the full no-minimum on-chain manual-verify flow (unique-amount matching, server-side `qrcode` QR data URL, tx-hash submit + admin approve/reject reusing deposit plumbing, public-RPC balanceOf works but eth_getLogs is blocked on free tier), plus refusing stolen-account/carding stock.\n- `references/nowpayments-minimum-and-invoice-modes.md` — NOWPayments real minimum (~$12, not the misleading `/min-amount` ~$0.05), invoice mode USD vs direct-crypto (USD has ~$18 fiat floor → blank checkout spinner; direct-crypto lowers it to $12), frontend UX pattern (display USDT amount + guard <min + disable button), backend IDR→USDT conversion, Custody vs direct-payout modes, symptom lookup table.
- `references/rate-limiting-and-brute-force.md` — application-layer DDoS/brute-force hardening behind Cloudflare Tunnel: the `req.ip`-is-always-loopback gotcha and `CF-Connecting-IP` fix, in-memory sliding-window limiter with no new dependency, dual-axis (IP + username) login lockout, Express wiring recipe, live curl verification sequence, and the health-endpoint-exemption pitfall.
- `references/dark-to-light-theme-swap.md` — converting a dark vanilla `style.css`+`app.js` Mini App to a light/"mode siang" theme: `:root` token swap is NOT enough — also flip the HARDCODED faint-white glass gradients/borders/shadows (invisible on light bg), white text `#fff`/`rgba(255,255,255,...)`, text-input `color:#ffffff` on `var(--glass2)`, `#000` inset punch-outs + overlays, `bubble-name` text-stroke/shadow inversion, and the SEPARATE splash.css + login-screen blocks; plus `tg.setHeaderColor/setBackgroundColor` + `<meta theme-color>` + cache-bump. Includes the pre-done verification grep and the execute_code bulk-replace approach.
- `references/react-vite-modern-stack.md` — building a non-storefront Mini App class (e.g. personal-finance tracker) with React+TS+Vite+Tailwind v4 instead of vanilla `app.js`: Tailwind v4's `@tailwindcss/vite` setup (no more `init -p`), Express 5 breaking `app.get('*', ...)`, Termux missing `/tmp` for test fixtures, using a Cloudflare quick tunnel for visual verification when no browser-automation tool is installed, the user-256-GCM encryption for sensitive text fields, why a full dark→light theme swap touches every component's className (not just CSS tokens), hiding edit/delete/create behind detail-view instead of the list row or a global FAB, deriving a net-worth/savings trend chart by reverse-accumulating a daily income/expense trend, a bounded-height "scroll the list not the page" container pattern (transaction history, in-note tables), a month/year lookback picker backed by from/to query params, smooth-curve sparklines (not pixel/LED blocks — user preference after seeing a blocky reference screenshot), a wishlist/savings-goal entity class, a Notion-style table note type, and two Termux tool quirks (npm install false-flagged as long-lived, orphaned cloudflared child after killing its wrapper). ALSO: category-first home (cards → per-type accumulation page), auto brand-detection logos + a user-editable custom logo editor (text≤10 + bg/fg color, reused for accounts AND wishlist), a freehand SVG-stroke sketch note type (with the "don't reuse the table-shape JSON parser for the sketch column" bug), a monthly savings-history calendar heatmap, and a rich logo+name+type+balance linked-account picker (not a bare `<select>`). Global no-emoji UI rule is embedded in SKILL.md Design Principles.
- Telegram Mini Apps: https://core.telegram.org/bots/webapps
- iOS Design Guidelines: SF Symbols, SF Pro font, iOS blue #0A84FF
- Glass morphism: `backdrop-filter: blur(40px)` + rgba backgrounds

---

## Catatan adaptasi Zeline
- File pendukung tidak di-inline (terlalu besar/biner): references/admin-panel-extras.md, references/announcements-and-reset.md, references/backend-admin-payments.md, references/backend-and-deploy.md, references/bgm-integration.md, references/cloudflare-tunnel-deploy.md, references/crypto-payments-and-onchain.md, references/dark-to-light-theme-swap.md, references/full-stack-audit-methodology.md, references/i18n-migration-pattern.md.

