---
name: account-automation
description: |
  Let an agent act on the user's own online accounts the way a human
  assistant would — push to GitHub, call service APIs, log into one-api
  token panels and claim rewards, manage anything that exposes an API or
  CLI. API-first (headless, works on any platform); browser-driven login
  for sites with no API is the harder Phase-2 path. Load when the user
  asks the agent to "manage my accounts", log into a service, or automate
  actions on GitHub or a web dashboard.
metadata:
  zeline:
    tags: [accounts, automation, github, api, credentials, 2captcha, playwright]
    category: automation
---

# Account automation — acting on the user's own accounts

Goal: the agent manages the user's accounts like a trusted assistant —
commit/PR on GitHub, hit service APIs, log into token dashboards and
claim daily rewards, etc. Two tiers, pick the lightest that works.

**Authorization first.** Only ever operate accounts the user owns or
explicitly authorizes. Never touch third-party/other-people accounts.
Read the credential-safety section before storing anything.

## Decision order — always try these top-to-bottom

1. **Official CLI** (best) — `gh` for GitHub, `glab` for GitLab, vendor
   CLIs. Auth once, reuse. This is how the agent already pushes to
   GitHub. Zero scraping, no ban risk.
2. **Official REST/GraphQL API + token** — anything with an API:
   GitHub, Telegram, Notion, Airtable, Cloudflare, any one-api /
   New-API token panel (see the `newapi-daily-checkin` skill). Send
   `Authorization: Bearer <token>`; some forks also require
   `New-Api-User: <id>`.
3. **API + captcha solver** — when the API itself works but a Cloudflare
   Turnstile or reCAPTCHA gates one specific action (a daily check-in,
   say), mint the captcha token with a solver and pass it to the API. No
   browser needed, and it is the cheapest way past a captcha-gated
   endpoint.
4. **Headless browser (Playwright/Selenium + Chromium)** — LAST resort,
   only for sites with NO usable API that require a rendered login
   (JS forms, OAuth click-through). Heavy; see Phase 2.

Prefer the highest tier that works. 90% of "manage my account" tasks
are solved at tiers 1–3 without a browser at all.

## Phase 1 — API-based (works today, even on Termux/Android)

### GitHub (fully working)
- Auth: `gh auth login` (token in keyring) or `GH_TOKEN` env.
- Push flow: never push straight to protected `main`. Branch → commit →
  `gh pr create` → wait CI (`gh pr checks <n>`) → `gh pr merge --squash
  --admin`. Verify merged via `gh pr view <n> --json state,mergedAt` and
  re-read the file from the remote (`gh api .../contents/<path>`).
- Never commit secrets; scan the diff before pushing.

### Any REST API service
Use `http_request` (Zeline tool) or `curl`. Store the token locally,
send it as a header. Read the vendor's API docs for the exact endpoints.

### Token panels (one-api / New-API forks)
See the dedicated `newapi-daily-checkin` skill: `/api/status` to detect
support + sitekey, `/api/user/self` for balance, `/api/user/checkin?
turnstile=<token>` to claim, 2Captcha for the Turnstile.

### 2Captcha (captcha-gated API actions)
- Submit `POST 2captcha.com/in.php` (`method=turnstile|userrecaptcha`,
  `sitekey`, `pageurl`, `json=1`) → task id.
- Poll `res.php?...action=get&id=..` until `status:1` → token (~800ch).
- Token is single-use & expires in minutes: solve → use immediately.
- Retry 2–3× on `ERROR_CAPTCHA_UNSOLVABLE`.

## Phase 2 — browser-driven login (roadmap, heavy)

Needed only for sites with no usable API that force a rendered login —
notably **X/Twitter**, and dashboards behind OAuth click-through.

Requirements:
- **Chromium** engine + **Playwright** (preferred) or Selenium to drive
  it. Termux/Android generally CANNOT run headless Chromium reliably —
  run this on a **Linux PC/VPS**, not the phone.
- A Zeline tool that exposes browser actions (goto, fill, click, wait,
  screenshot, cookies). This does not exist in Zeline yet — it must be
  built as a new tool (wrapping Playwright's async API), gated to the
  owner/`full` profile only (never public gateways).
- Session reuse: after a manual first login, persist cookies/storage
  state to disk and reload it, so the agent skips re-login and 2FA on
  every run.

### X/Twitter — read this before trying
- No free API tier for meaningful actions; the paid API is expensive.
- Login is aggressively anti-automation (device checks, arkose captcha,
  behavioral fingerprinting). Automated login risks **suspension/ban**.
- If the user insists, safest route is a persisted logged-in browser
  session (cookies exported from a real login) + Playwright, running on
  a stable IP — and accept the ban risk. Set this expectation clearly.

## Credential safety (hard rules)

- Store tokens/keys ONLY in local, gitignored files inside the target project
  or the OS keyring. NEVER in a repo,
  skill, or chat log you might push.
- When pushing anything, scan the diff for token/key patterns first.
- Prefer scoped tokens (least privilege) over full-account passwords.
- Never type/store the user's raw password when a token works instead.
- Tokens are static until the user regenerates them; on `401/invalid`,
  ask for a fresh one rather than guessing.
- For 2FA/OAuth consent screens, do the sensitive step with the user;
  don't try to bypass MFA.

## Report format (user preference)
For balance/reward tasks, per account: `Username / Yesterday (before) /
Now (after)` in `$` (credit ÷ conversion), never raw credits.

## Full account lifecycle (the ecosystem — how the skills connect)

"Manage my accounts" spans a whole lifecycle. This skill is the **index**;
each stage has a specialized skill. Walk them in order:

```
0. CREATE ACCOUNT (from nothing)        → skill: temp-email-automation
   temp mail (mail.tm) → signup form → poll inbox for OTP/verify link →
   confirm. Handles Cloudflare (raw-socket bypass) + Turnstile (2Captcha)
   + SPA sites (Selenium/Chromium). Reads OTP itself, no user needed.

1. ONBOARDING BONUS                     → skill: temp-email-automation
   right after signup: enable "free models", claim welcome gift ($5),
   apply invite/aff code. Do these BEFORE creating the API key — they're
   separate onboarding actions, not optional.

2. EXTRACT CREDENTIAL                   → skill: temp-email-automation
   grab the API key / token (often shown once — capture immediately),
   or the referral link, or wallet address. Store locally, gitignored.

3. RECURRING VALUE                      → skill: newapi-daily-checkin
   daily check-in / claim rewards on token panels. Fee-safe: check
   `checked_in_today` before solving captcha. Reports $ before/after.

4. ONGOING MANAGEMENT                   → THIS skill (account-automation)
   push to GitHub, call service APIs, rotate keys, recreate expired
   keys, run any API/CLI action on the account.
```

So when the user says "buatin akun di web X pakai temp mail, ambil OTP,
login, ambil API key / ref / checkin" — that's stages 0→3, all already
doable. Load `temp-email-automation` for 0-2, `newapi-daily-checkin` for
3, and this skill for the GitHub/API management layer.

### What the agent CAN do end-to-end today (no user in the loop)
- Generate a fresh inbox, sign up, **read the OTP/verify email itself**,
  complete verification.
- Solve the signup/login Turnstile via 2Captcha.
- Claim onboarding bonuses + apply referral codes.
- Extract and store the API key.
- Do daily check-ins across many accounts.
- Manage GitHub (push/PR/merge) and any REST API.

### What still needs the user or a PC/VPS (be honest about these)
- **Sites with only OAuth signup** (Google/GitHub consent, no email+pass):
  each account needs a real logged-in Google/GitHub session — not
  mass-automatable; do it with the user, one at a time.
- **X/Twitter & JS-only logins with no API**: need the Phase-2 browser
  tool on a PC/VPS (Termux can't drive Chromium reliably for these).
- **Payment / MFA / KYC steps**: always loop the user in; never bypass.

### Registration etiquette (from temp-email-automation)
When asked to register, START immediately (open page, fill email, click
next) — don't pre-list hypothetical blockers. Only report a blocker if
you actually hit it. The user may want a partial flow (stop at
verification-email) — respect that. Ask for a missing input in one line,
then execute.

## Related skills (load as needed)
- `temp-email-automation` — signup, temp mail, OTP capture, Cloudflare/
  Turnstile bypass, API-key extraction, mass registration.
- `newapi-daily-checkin` — daily reward check-in on any one-api /
  New-API panel; the runner discovers each panel's own settings.
- `github-pr-workflow` / `github-auth` — GitHub specifics.

## Deep references (in this skill)
- `references/browser-automation-playbook.md` — full browser/web
  automation playbook: engines and drivers, a portable Selenium recipe,
  session persistence (the key technique), Cloudflare/Turnstile tiers,
  anti-detection, rate-limit reality, building a Zeline browser tool.
- `references/zeline-browser-tool-roadmap.md` — step-by-step plan to add
  a Playwright `browser` tool to Zeline (owner-gated).

## Pitfalls
- `UID` is read-only in bash — use `UUID` for a user-id variable.
- Don't batch-solve captchas then use them later — they expire.
- Don't push to protected `main` directly — use a PR + admin merge.
- Termux can't drive Chromium — Phase 2 needs a PC/VPS.
