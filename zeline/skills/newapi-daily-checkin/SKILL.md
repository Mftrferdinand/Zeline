---
name: newapi-daily-checkin
description: |
  Automate daily check-in on "New API" LLM token panels (tabitoken.com,
  gorouter.app, and similar one-api forks) across MULTIPLE accounts.
  These panels gate check-in behind Cloudflare Turnstile, solved via
  2Captcha. Load when the user says "checkin tabitoken", "checkin
  gorouter", or asks to claim daily quota/credit on these panels.
metadata:
  hermes:
    tags: [checkin, tabitoken, gorouter, new-api, 2captcha, automation]
    category: productivity
---

# New API daily check-in (multi-account, Turnstile via 2Captcha)

Panels are one-api / New-API forks. Daily check-in awards ~2.5M–5M
quota. **500,000 credit = $1** — ALWAYS report balances/rewards in `$`,
never raw M/credit (user preference).

## Files (this profile)
- Tokens: `~/.hermes/scripts/tabi_tokens.txt` — one per line, `TOKEN|label`, `#` = comment.
- Script: `~/.hermes/scripts/tabi_checkin.sh` — loops tokens, checks status, solves, checks in.
- 2Captcha API key lives inside the script (`CAP=`). Balance check:
  `curl -s "https://2captcha.com/res.php?key=$CAP&action=getbalance&json=1"`

## Golden rule (user-stated)
**ALWAYS check `checked_in_today` BEFORE solving Turnstile** — a solve
costs 2Captcha fee (~$0.003). Skip accounts already checked in. The
script already does this; never bypass it.

## API shape
Base = panel origin (e.g. `https://tabitoken.com`). Header:
`Authorization: Bearer <token>` + UA `Mozilla/5.0`.
- `GET /api/status` → `checkin_enabled`, `turnstile_check`, `turnstile_site_key`, `quota_per_unit`.
- `GET /api/user/self` → `data.display_name`, `data.id`, `data.quota` (÷500000 = $).
- `GET /api/user/checkin` → `data.stats.checked_in_today`, `checkin_count` (streak), `records[0].quota_awarded`, `min_quota`/`max_quota`.
- `POST /api/user/checkin?turnstile=<TOKEN>` → success `{"success":true,"message":"签到成功"}`; already done `{"message":"今日已签到"}`.
  - Turnstile token MUST be passed as **query param** `?turnstile=`, not body/header (body → "Turnstile token 为空").

## Turnstile via 2Captcha
1. Submit: `POST https://2captcha.com/in.php` params `key, method=turnstile, sitekey=<site_key>, pageurl=<base>/, json=1` → returns task id.
2. Poll: `GET https://2captcha.com/res.php?key=..&action=get&id=<id>&json=1` every ~7s until `status:1`; `request` = token (~800+ chars).
3. Token is single-use, expires in minutes. Site key is per-panel (tabitoken `0x4AAAAAAEGV81TArluaPQGB`; gorouter `0x4AAAAAAELziOpg1Y2gFtAt`).

## gorouter.app quirk (IMPORTANT)
gorouter requires an extra header `New-Api-User: <numeric user id>` on
EVERY authed request, else `Unauthorized, New-Api-User header not
provided`. The user id is per-panel (NOT the same as tabitoken id). Get
it from the account's Profile page, or brute-force scan `/api/user/self`
with `New-Api-User: N` (no captcha needed) until it stops returning
"does not match logged in user". tabitoken does NOT need this header.

Faster than blind scanning: when the user hands you a batch of tokens
AND a batch of candidate user-ids separately (unordered), cross-match
by looping every id × every token against `/api/user/self` and printing
the (token,id)→name pairs that authenticate. One id matches exactly one
token; the rest error out. This resolves ownership without asking the
user to pair them manually.

gorouter files now exist in this profile:
- `~/.hermes/scripts/gorouter_tokens.txt` — `TOKEN|USERID|label` per line.
- `~/.hermes/scripts/gorouter_checkin.sh` — gorouter variant that sends
  the `New-Api-User` header and reports $ balances.

## Bash pitfall: `UID` is read-only
Do NOT use `UID` as a shell variable name in these scripts — bash treats
`UID` as a read-only builtin, so `UID="..."` aborts with
`UID: readonly variable` and the loop silently does nothing. Use `UUID`
(or any other name) for the per-account user-id field.

## Run
`bash ~/.hermes/scripts/tabi_checkin.sh` — prints per-account result.
After a fresh check-in, fetch `/api/user/self` and report new balance in $.

## Report format (user preference — use verbatim)
Per panel, per account, capture balance BEFORE (yesterday/pre) and AFTER:

```
GoRouter
• Username : <display_name>
• Yesterday : $<balance before check-in>
• Now       : $<balance after check-in>
```

Same block for TabiToken. Always `$` (quota ÷ 500000), never M/credit.
"Yesterday" = the `/self` balance read right before check-in.

## Access tokens
Static; only change if user regenerates them in Profile → System Access
Token. Expired token → script prints `SKIP: token invalid/expired`; ask
user for a fresh one. Keep separate token files per panel if adding
gorouter (e.g. `gorouter_tokens.txt` + a gorouter variant of the script
that sends the `New-Api-User` header).
