---
name: captcha-solving-2captcha
description: |
  Solve Cloudflare Turnstile (and similar) CAPTCHAs programmatically via
  the 2Captcha API so you can automate endpoints that gate actions behind
  a captcha. Includes the one-api / New API panel daily check-in workflow
  (multi-account, e.g. tabitoken.com) as the worked example. Load when a
  task needs to pass a Turnstile/reCAPTCHA to hit an API, or to automate
  daily check-in / sign-in rewards on a one-api panel.
version: 1.0.0
metadata:
  zeline:
    tags: [captcha, turnstile, 2captcha, one-api, automation, checkin]
    category: devops
---

# CAPTCHA solving with 2Captcha (Cloudflare Turnstile)

You CANNOT forge a Turnstile token with curl. Turnstile tokens are
single-use, short-lived (~minutes), and only mintable by real browser JS
tied to fingerprint + IP. A paid solver (2Captcha / CapSolver) runs a
real browser farm and returns a valid token for a given site key. That
is the only clean path from a headless/Termux environment.

## 2Captcha Turnstile flow

1. Get the **site key** from the target page (`turnstile_site_key` in the
   panel's `/api/status`, or the `data-sitekey` attr in page HTML).
2. Submit a task:
   ```
   curl -s https://2captcha.com/in.php \
     --data-urlencode "key=$CAP" \
     --data-urlencode "method=turnstile" \
     --data-urlencode "sitekey=$SITEKEY" \
     --data-urlencode "pageurl=https://target.com/" -d "json=1"
   # -> {"status":1,"request":"<taskid>"}
   ```
3. Poll for the result (token ready in ~10-40s):
   ```
   curl -s "https://2captcha.com/res.php?key=$CAP&action=get&id=$ID&json=1"
   # status:0 = CAPCHA_NOT_READY, retry; status:1 -> request = the token
   ```
4. Check balance any time: `res.php?key=$CAP&action=getbalance&json=1`.
   Turnstile solves cost ~$0.003 each — cheap, but not free (see pitfalls).

Ready-to-run script: `scripts/tabi_checkin.sh` (multi-account check-in
loop) and token list format in `references/one-api-checkin.md`.

## Delivering the token — WHERE it goes matters

The single biggest time-sink is guessing how the server wants the token.
For **one-api / New API** panel check-in the token goes as a **QUERY
STRING param**, not the body and not a header:

```
POST /api/user/checkin?turnstile=<TOKEN>     # ✅ works
POST /api/user/checkin  (turnstile in body)  # ❌ "Turnstile token 为空" (empty)
header cf-turnstile-response: <TOKEN>         # ❌ "Turnstile token 为空"
```

`"Turnstile 校验失败"` = token received but verification failed (wrong/stale
token or wrong param). `"Turnstile token 为空"` = server never saw a token
(wrong delivery location). Use these two messages to distinguish
"delivery wrong" vs "token wrong". Other panels may want a body field or
`cf-turnstile-response` — if query fails, try body then header, and use
the two error strings to tell which is wrong.

## one-api / New API panel API (tabitoken.com pattern)

- Auth: `Authorization: Bearer <access_token>` + a `User-Agent`.
- `GET /api/user/self` → `data.display_name`, `id`, `github_id`, `quota`.
- `GET /api/user/checkin` → `data.stats.checked_in_today`, `checkin_count`
  (streak), `records[0].quota_awarded`; and `data.min_quota`/`max_quota`.
- `POST /api/user/checkin?turnstile=<tok>` → `success:true`,`message:"签到成功"`.
- `GET /api/status` → `checkin_enabled`, `turnstile_check`,
  `turnstile_site_key`, `quota_per_unit` (credits per $1).

### Access tokens vs captcha tokens
- **Access token** = static. Valid until the user clicks Regenerate in
  Profile → System Access Token, or admin resets. Does NOT rotate on its
  own — collect once, store, reuse for months. Expired token returns
  `AUTH_UNAUTHORIZED` / `invalid access token` — skip it, don't error.
- **Turnstile token** = per-action, single-use, expires in minutes.
  Always freshly solved via 2Captcha at run time.

## PITFALLS / user corrections (obey these)

- **ALWAYS check `checked_in_today` BEFORE solving the captcha.** Solving
  a Turnstile for an account that already checked in wastes 2Captcha fee.
  The multi-account loop must skip already-done and invalid accounts and
  only spend a solve on accounts that actually need it. (User correction.)
- **Report balances in dollars, not raw credits.** Convert with
  `quota / quota_per_unit` (tabitoken: 500000 credits = $1). Say "$6.14",
  not "3068562 credits" / "3M". (User preference.)
- Store one token per line in a tokens file (`TOKEN|label`), loop over it,
  and report a per-account table: name + status + $ awarded/balance.
- Verify success by re-reading `/api/user/self` quota or `records[0]`
  after check-in — the self-report `签到成功` is confirmed by the balance rise.
- Authorization check before automating: confirm the user owns / is
  permitted on the accounts and the site owner allows it. This session
  the user confirmed the panel owner is a friend and it's allowed.
