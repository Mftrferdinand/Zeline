---
name: captcha-solving-2captcha
description: |
  Generic CAPTCHA-solving workflow for public websites and authorized web/API
  actions using 2Captcha or a compatible solver. Covers Cloudflare Turnstile,
  reCAPTCHA v2/v3, hCaptcha, site-key discovery, managed-challenge parameters,
  browser token injection, API token delivery, cookies/session continuity,
  verification, retries, and cost control. Use for ANY web page or API blocked
  by CAPTCHA—not only daily check-in panels. Daily check-in is one worked example.
version: 2.0.0
metadata:
  zeline:
    tags: [captcha, turnstile, recaptcha, hcaptcha, 2captcha, cloudflare, web, browser, api, automation]
    category: automation
---

# Generic CAPTCHA solving with 2Captcha

Use this skill whenever a public website or an authorized action is blocked by
Cloudflare Turnstile, reCAPTCHA, hCaptcha, or a similar CAPTCHA. This is a
general web/API workflow. The one-api daily check-in procedure is only one
example, not the boundary of the skill.

## Scope decision: assess the underlying action

A CAPTCHA is an anti-bot checkpoint, not automatically MFA and not an automatic
reason to refuse automation.

Supported scope:
- Reading a public page, catalog, pricing table, article, documentation, or
  other public information.
- Actions on the user's own account or an account/system where the user has
  explicit permission, including allowed check-ins and form submissions.
- Low-volume troubleshooting and testing requested by the operator.

Do not proceed with:
- OTP/MFA/KYC bypass, account takeover, credential stuffing, fraud, evading a
  ban or explicit access revocation, or abusive/high-volume scraping.
- Do not claim a ToS violation without checking the site's actual current terms.
  Never invent a blanket rule that solver services are always forbidden.

FTMO rule for this workflow: retrieving the public pricing table is a public,
read-only action and is supported unless concrete evidence shows an
authentication boundary or an explicit applicable prohibition. Do not refuse
merely because FTMO is a third-party website.

## Secrets and cost discipline

- Treat the solver API key as a secret. Prefer an environment variable such as
  `CAPTCHA_API_KEY`; do not echo it, include it in reports, commit it, or save it
  into skills/history.
- A token is single-use and short-lived. Solve only after the target request,
  browser session, and delivery method are ready.
- Check whether the desired content/action is already available before spending
  a solve. Never solve repeatedly without changing the diagnosed failure.
- Use low request volume and verify the requested result immediately.

## 1. Identify the challenge type

Inspect HTML, rendered DOM, network logs, or application config for:

- Turnstile: `challenges.cloudflare.com/turnstile`, `cf-turnstile`,
  `data-sitekey`, `turnstile.render`, `cf-turnstile-response`.
- reCAPTCHA v2: `google.com/recaptcha/api.js`, `g-recaptcha`,
  `g-recaptcha-response`, visible checkbox/invisible widget.
- reCAPTCHA v3: `grecaptcha.execute(sitekey, {action: ...})`; record `action`
  and required score if known.
- hCaptcha: `hcaptcha.com/1/api.js`, `h-captcha`, `h-captcha-response`.
- Cloudflare managed challenge: `_cf_chl_opt`, `cType: 'managed'`,
  `chlPageData`, `data`/`pagedata`, or redirect to a challenge/block page.

Do not assume every Cloudflare block is a simple standalone Turnstile widget.
Managed challenges may require extra parameters and session/IP continuity.

## 2. Collect parameters before creating a solver task

At minimum collect:
- Exact final `pageurl` after redirects.
- `sitekey`.
- Challenge type.

When present also collect:
- Turnstile `action`, `data`, and `pagedata`/`chlPageData`.
- reCAPTCHA v3 `action` and target `min_score`.
- User-Agent used by the target browser/session.
- Proxy details when the target binds challenge verification to client IP.
- Existing cookies/session state needed after the challenge.

Sitekey discovery methods, in order:
1. Target API/config/status endpoint if one exposes it.
2. HTML attributes (`data-sitekey`) or script arguments.
3. Rendered DOM/network events in a browser.
4. Search downloaded JS only when the first three do not expose it.

## 3. Submit and poll a 2Captcha task

### Cloudflare Turnstile (legacy endpoint)

```bash
curl -s https://2captcha.com/in.php \
  --data-urlencode "key=$CAPTCHA_API_KEY" \
  --data-urlencode "method=turnstile" \
  --data-urlencode "sitekey=$SITEKEY" \
  --data-urlencode "pageurl=$PAGE_URL" \
  --data-urlencode "json=1"
```

If the managed challenge exposes extra values, include them as supported by the
current 2Captcha API (`action`, `data`, `pagedata`). Use proxy/proxytype and the
matching browser User-Agent when IP/fingerprint continuity is required.

Poll only after receiving a task id:

```bash
curl -s "https://2captcha.com/res.php?key=$CAPTCHA_API_KEY&action=get&id=$TASK_ID&json=1"
```

`CAPCHA_NOT_READY` means wait and retry. A successful response contains the
short-lived token. Do not create a second paid task while the first is pending.

### Other challenge mappings

- reCAPTCHA v2: `method=userrecaptcha`, `googlekey=$SITEKEY`, `pageurl=$PAGE_URL`.
- reCAPTCHA v3: same base method plus `version=v3`, `action`, and `min_score`.
- hCaptcha: `method=hcaptcha`, `sitekey=$SITEKEY`, `pageurl=$PAGE_URL`.

Provider APIs evolve; if an endpoint rejects a documented parameter, check the
current solver error code rather than guessing or spending repeated solves.

## 4. Deliver the token through the correct path

A valid token is useless if delivered to the wrong session or field. Determine
which of these the target expects.

### Browser-rendered website

Keep the original browser page/session open. Set the widget response field and
trigger the site's callback or normal form submission:

- Turnstile: `cf-turnstile-response`.
- reCAPTCHA: `g-recaptcha-response`.
- hCaptcha: `h-captcha-response` (and sometimes `g-recaptcha-response`).

For SPA pages, merely changing a hidden textarea may not notify the framework.
Dispatch `input`/`change` events or invoke the callback captured from the widget
configuration, then submit using the page's normal button/form. Preserve the
same cookies, User-Agent, page URL, and proxy/IP used for the solve when the
challenge requires continuity.

After successful challenge completion, capture the resulting cookies or browser
state and fetch/read the requested page in that same session. A solver token by
itself is not always a reusable HTTP bearer token.

### Direct API/action endpoint

Inspect the browser network request or application code to determine whether the
token belongs in:
- JSON/form body (`turnstile`, `cf-turnstile-response`,
  `g-recaptcha-response`, `h-captcha-response`, or site-specific field).
- Query string.
- Header.

Do not blindly cycle all locations with paid tokens. First reproduce the exact
request shape from the browser/network trace.

## 5. Verify success from the requested outcome

A solver response marked successful proves only that a token was minted. Verify
the target result:

- Public page: challenge/block markers are gone and the requested content is
  present (for FTMO: account sizes and fee/pricing rows, not merely HTTP 200).
- Browser: final URL and DOM show the real page, not `/block/...`,
  `Just a moment`, or an error shell.
- API/action: response indicates success and a follow-up read confirms the state
  change.

Report the actual extracted data/result. Do not present the CAPTCHA token to the
user unless they explicitly need it for debugging.

## Failure diagnosis

- `ERROR_WRONG_USER_KEY` / auth error: key invalid; stop and ask for rotation.
- `ERROR_ZERO_BALANCE`: no solver funds; do not retry.
- `ERROR_CAPTCHA_UNSOLVABLE`: retry at most once after validating parameters.
- Token rejected immediately: stale/wrong sitekey/pageurl/action/data, wrong IP
  or User-Agent, or wrong delivery field/session.
- HTTP 200 but challenge remains: token was not consumed by the actual page
  callback/form, or cookies/session continuity was lost.
- Redirect to block page: inspect final URL, challenge parameters, and whether a
  proxy-bound solve is required.

## Worked example: one-api/New API daily check-in

For these panels only, the common endpoint is:

```text
POST /api/user/checkin?turnstile=<TOKEN>
```

Check `checked_in_today` before paying for a solve. Verify by reading the check-in
record or balance afterward, and report monetary balance rather than raw quota.
This panel-specific pattern must not be generalized to unrelated websites.
Read `checked_in_today` from `GET /api/user/checkin`; use `/api/user/self` for
authentication and balance. Reuse securely stored account credentials before
asking the operator to resend secrets in chat.

## Final checklist

1. Underlying action is within supported scope.
2. Target result is not already accessible.
3. Exact challenge type, final URL, sitekey, and extra parameters collected.
4. Browser/API delivery path known before paying.
5. Solver key kept secret; one task at a time.
6. Token used immediately in the matching session/IP context.
7. Requested page/action verified from real target output.
