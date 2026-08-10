# Prop Firm Evaluation

> Evaluate funded/prop trading firms (forex/CFD) — verify existence, compare pricing & T&C, spot payout-denial red flags, rank worth. Use when user asks about FundingPips, FTMO, FundedNext, The5ers, WeMasterTrade/WMT, 2-step/1-step/instant, payout, on-demand, or "firm yang bayar".

## Trigger
User asks about prop firms: FundingPips, FTMO, FundedNext, The5ers, WeMasterTrade (WMT), "2-step / 1-step / instant", harga akun, payout, on-demand, "firm yang bagus/membayar", "worth atau nggak". the user is a funded trader — treat questions as decisions, not trivia.

## the user's fixed context
- Funded (FundingPips 2-step): 100k ×1, 50k ×2, 25k ×3 → total **$275k**. Target **3%/bulan konsisten** ≈ $8.2k/bln ≈ 132jt IDR. Style: jangka panjang, no-timer firms preferred.
- Sanity math: $5.2k/bln dari 50k = 10.4%/bln → unsustainable (pros top out at 2–5%/bln). 275k × 3% = $8.25k/bln ≈ **1.58 Miliar IDR/tahun**.
- Prop firm model = simulation account + copy-to-live — normal, not inherently scam.
- 1-step vs 2-step vs instant answer depends on proof of consistency: not-yet-proven → 2-step (cheap fail); 3+ bulan konsisten → instant baru worth (instant blow = duit asli hangus).

## Workflow
1. **Exists?** Check site live (DNS + curl). Domain variants matter: `wemastertrader.com` dead vs `wemastertrade.com` live.
2. **T&C**: propfirmmatch.com/prop-firms/<firm> = reliable rules source (profit target, daily/max loss, min days, split, payout). FTMO pricing scrapeable via r.jina.ai on `ftmo.com/en/2-step-challenge/`.
3. **Payout reputation**: Trustpilot via r.jina.ai. Scan pattern: payouts accepted repeatedly, then denied on vague "scalping/HFT" rule = classic trap.
4. **Badge check**: "Best X 2025" + regulator logo (FinCEN, IAC, etc.) on site = fake-award gimmick (FinCEN is a regulator, not an award body).
5. **Prices**: pricing pages often JS/anti-bot (Vercel Security Checkpoint 429 on fundingpips.com) → label estimates "~, cek dashboard". Never state unverified prices as fact.
6. Deliver as table: harga | T&C | payout | verdict worth→nggak, ranked.

## Red flags (payout-denial pattern)
- Vague "scalping/high-frequency" rule enforced **after** trader proves profitable. WMT case: 57 days of work denied, "42.82% scalping" cited, rule not in published T&C; reviewer pattern "they give you payout until they confirm you're capable of multiple withdrawals, then start rejecting".
- Low first-payout split (WMT 50% vs FTMO 80–90% from the start).
- Fake awards / regulator badges.
- Discriminatory T&C clauses (WMT 510Zero: reward cut for everyone except Vietnam/Indonesia/Thailand).

## Firm database (Aug 2026, forex/CFD)
Full verified table + dead firms: `references/firm-database.md`.

TL;DR ranking for long-term 3%/bln: **FTMO** (aman, no timer, €345 verified) > **The5ers** (no time limit, tight 4%/8%) > **FundedNext** (fast payout, 24h reward) > **FundingPips** (minor/on-demand) ; **WeMasterTrade = payout-denial risk, jangan akun utama**.

## Research technique (anti-bot, Termux)
- **r.jina.ai reader works** for: Bing search (`r.jina.ai/https://www.bing.com/search?q=...`), Trustpilot, FTMO, The5ers, wemastertrade.com, propfirmmatch, FundedNext (partial).
- **Blocked**: fundingpips.com (Vercel checkpoint 429), web.archive.org via jina (403 abuse), direct curl to Google/Bing/DuckDuckGo (429), searx instances (captcha/rate-limit).
- Delegate subagents for live prices: they hit the SAME blocks — put the jina technique in the delegation context.
- Bing-via-jina can return garbage/irrelevant results (fell back to "Canvas", "Wells Fargo" for a prop-firm query) — never conclude "firm doesn't exist" from one search; check domain DNS + jina reader on the site itself.

## Pitfalls
- Never fabricate exact prices; mark unverified as "~est, cek dashboard".
- FundingPips "On Demand" payout = request anytime outside bi-weekly schedule, kena fee, min profit ~2%; buat profit tipis, nunggu jadwal lebih hemat.
- Dead firms to warn about: MyForexFunds (tutup 2024), True Forex Funds (regulator), The Funded Trader (payout chaos).


---

## Lampiran: `references/firm-database.md`

# Prop Firm Database — verified Aug 2026 (forex/CFD)

Prices marked `~` = couldn't scrape (anti-bot), estimate from knowledge — ALWAYS verify at dashboard before payment. FTMO prices verified live via r.jina.ai.

## Ranking (untuk target jangka panjang 3%/bln)

1. **FTMO** — paling trusted, no time limit, T&C jelas. Kurang: payout awal lambat.
2. **The5ers** — no time limit + risk ketat 4%/8% → paling cocok strategi pelan-konsisten. Pasangan FTMO.
3. **FundedNext** — payout cepat (reward 24 jam), Trustpilot 4.5★ 73rb+ ulasan. Jago buat instant.
4. **FundingPips** — on-demand 90%, free trial 14 hari, up to 100% split promo. Kurang: T&C kurang bersih (1.2% risk guideline "striking rule" di funded). Minor doang.
5. **WeMasterTrade (WMT)** — real & operates, tapi pola tolak payout → jangan akun utama.

## Per-firm

### FTMO
- 2-step 50k = **€345** (verified live). 1-step 50k ~€490.
- T&C: target 10%→5%, daily 5%, max 10%, min 4 trading days, NO time limit (2-step).
- Split: 80% → max 90%. Payout: bulanan → biweekly.
- **Instant: TIDAK ada** (hanya 2-step & 1-step Challenge).

### FundingPips
- 2-step Standard 50k ~$299–390. Instant 50k ~$490–595. (Situs blocked Vercel 429 — semua `~`.)
- T&C: target 8%→5%, daily 5%, max 10%, min 5 days, no time limit. 2-Step Flex: 10%→6%.
- Split: 80% (on-demand 90% setelah syarat). Payout: biweekly, on-demand (kena fee, min profit ~2%).
- Model lain: 1-Step Flex, 2-Step Pro, 2-Step Flex, Zero, Instant. Free trial 14 hari. 100k: 1-Step Flex $533, 2-Step Std $522, 2-Step Flex $499 (update harga).
- Per-trade risk guideline 1.2% di funded (2-step Std 8%/5%) — "striking rule".

### FundedNext
- Stellar 2-step 50k ~$219–289. Stellar Instant ~$290–350. (JS-heavy site — `~`.)
- T&C: 8%→5%, daily 5%, max 10%, min 5 days; Stellar = no time limit.
- Split 80%→90%. Payout: mingguan, reward dalam 24 jam. Trustpilot 4.5 (73k+).
- Catatan: copy trading dibatasi; trade 5 menit sebelum/sesudah news High Impact kena rule 40% news profit split; Instant ada Drawdown Adjustment Rule.

### The5ers
- 2-step ~$149–199. **Instant: TIDAK ada**.
- T&C: 8%→5%, daily 4%, max 8% (paling ketat), **NO time limit**.
- Split 80% (High Stakes 90%). Payout: bulanan / on-request.
- High Stakes = 3-step, kapital sampai $1M+.

### WeMasterTrade (wemastertrade.com)
- Model: sim + "exclusive algorithms copy ke live" (normal prop model). Instant tersedia.
- Split: 50% (payout 1) → 75% (payout 2) → 90% (payout 3+). Payout daily request.
- Red flags: fake awards (FinCEN, "Best Instant Funding 2025" — badge palsu), klausa 510Zero diskriminatif, Trustpilot pola tolak payout: kasus 57 hari kerja ditolak "scalping 42.82%" padahal rule nggak jelas di T&C; "banned permanently"; "5x withdraw lancar lalu mulai nolak".
- `wemastertrader.com` (pakai -r) DNS mati — jangan tertukar.

### Futures (di luar forex/CFD)
- Topstep (payout paling solid), Apex Trader Funding (murah). Topstep = pilihan aman kalau user mau expand ke futures.

## DEAD / JANGAN SENTUH
- **MyForexFunds** — tutup 2024, kabur bawa duit.
- **True Forex Funds** — kena regulator, berhenti.
- **The Funded Trader** — payout kacau, banyak komplain.

## Sumber
- propfirmmatch.com/prop-firms/<firm> — T&C terverifikasi (update log harga 100k FP: $533/$522/$499).
- trustpilot.com/review/wemastertrade.com — pola payout WMT.
- r.jina.ai reader untuk site yang block bot (ftmo.com/en/2-step-challenge/).
