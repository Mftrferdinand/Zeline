---
name: network-route
description: Owner-only network routing for public websites blocked by country/IP. Configure, health-test, and automatically use private HTTP/HTTPS/SOCKS5 routes without proxying Telegram, the model provider, localhost, or 9Router.
---

# Network Route

Use this skill when a public URL redirects to `/block/XX.html`, says it is unavailable in the current country, or otherwise proves an IP/geo restriction.

## Core behavior

- Diagnose the blocker from real response URL/body.
- Do not confuse a geo-block with CAPTCHA.
- Do not claim the route exists before `network_route list` confirms it.
- Routes are owner-only and stored locally with mode `0600`.
- Route credentials must never appear in narration, logs, or final output.
- Routing is per web request. Do not set global `HTTP_PROXY`, `HTTPS_PROXY`, or `ALL_PROXY`; Telegram, the provider, localhost, and 9Router must remain direct.

## Workflow

1. Call `network_route` with `action=list`.
2. If routes exist, health-test the best non-blocked country with `action=test`.
3. Retry the public URL using `web_fetch`; owner/full `web_fetch` automatically tries healthy configured routes when direct access is geo-blocked.
4. Verify the final content, not merely HTTP 200:
   - final URL is not `/block/...`;
   - requested page text/data is present;
   - response reports which route label/country succeeded, never its credential.
5. If the route gets past geo-block but a Turnstile/reCAPTCHA/hCaptcha challenge remains, keep the same route/session and load `captcha-solving-2captcha`.
6. If no route exists, report that one owner proxy endpoint is required. Never fabricate or scrape random open proxies.

## Add a route

Call:

```json
{
  "action": "add",
  "label": "uk-residential",
  "proxy_url": "socks5h://USER:PASSWORD@HOST:PORT",
  "country": "GB"
}
```

Supported schemes: `http`, `https`, `socks5`, `socks5h`. Prefer `socks5h` so DNS resolution uses the route.

## Safety and scope

Supported:

- public pages, pricing, catalogs, articles, and public data;
- actions on accounts owned or explicitly authorized by the operator.

Do not use routes for account takeover, OTP/MFA/KYC circumvention, fraud, ban/access-revocation evasion, or abusive mass scraping.
