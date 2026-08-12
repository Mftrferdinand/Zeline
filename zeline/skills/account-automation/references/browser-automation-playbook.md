# Browser & web automation — full playbook

How an agent drives a real browser to act on the user's accounts when
APIs aren't enough. This is the deep reference behind Phase 2 of
account-automation. Order of preference is ALWAYS: CLI > API > API+captcha
solver > **browser** (this doc) — because a browser is the slowest,
heaviest, most ban-prone, and most fragile tier.

## When you actually need a browser

Use a browser ONLY when ALL of these are true:
- The site has no usable API/CLI for the action.
- The action requires rendered JavaScript (SPA forms, OAuth
  click-through, wallet popups, drag/slider captchas).
- You've confirmed there's no raw-socket / API shortcut (check the JS
  bundle + network tab first — many "SPA-only" sites have a hidden JSON
  API you can hit with curl).

If a plain `curl`/`http_request` or a documented API works, DO NOT open a
browser.

## Engines & drivers (what runs where)

| Layer | Options | Notes |
|---|---|---|
| Engine | Chromium, Firefox, WebKit | Chromium most compatible |
| Driver | **Playwright** (preferred), Selenium, Puppeteer | Playwright = best API, auto-waits, contexts, storage_state |
| Stealth | undetected-chromedriver, playwright-stealth, patchright, Camoufox | reduce (not eliminate) bot fingerprints |

Platform reality (verified on this user's setup):
- **Termux/Android**: Selenium + system Chromium WORKS for plain sites
  (`pkg install chromium`, chromedriver at
  `/data/data/com.termux/files/usr/bin/chromedriver`, binary at
  `.../usr/lib/chromium/chrome`, use `--headless=new`, explicit
  `Service`, NOT Selenium Manager). **Playwright has NO aarch64 wheel →
  unavailable on Termux.** Camoufox depends on Playwright → also
  unavailable. So Termux browser automation = Selenium + Chromium only.
- **Linux PC/VPS**: full Playwright + Chromium. This is where heavy
  browser jobs (X login, OAuth) should run.

## Selenium on Termux (working recipe)

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
opt = Options()
opt.add_argument('--headless=new'); opt.add_argument('--no-sandbox')
opt.add_argument('--disable-dev-shm-usage'); opt.add_argument('--disable-gpu')
opt.add_argument('--window-size=1280,800')
opt.binary_location = '/data/data/com.termux/files/usr/lib/chromium/chrome'
d = webdriver.Chrome(service=Service('/data/data/com.termux/files/usr/bin/chromedriver'), options=opt)
d.get('https://example.com'); print(d.title); d.quit()
```

## Session persistence — the single most important technique

Re-logging in every run is what triggers 2FA, captchas, and bans. Log in
ONCE, then reuse the authenticated session forever.

- **Playwright**: `context.storage_state(path="state.json")` after login;
  next run `browser.new_context(storage_state="state.json")` → already
  logged in. Stores cookies + localStorage.
- **Selenium**: `pickle.dump(driver.get_cookies(), f)`; next run `driver.get(domain)` once, `for c in cookies: driver.add_cookie(c)` (pop `sameSite` first — Selenium rejects some values), then reload.
- **Cookie import from the user's real browser**: the cleanest path for
  hard sites (X). User exports cookies from a logged-in session; agent
  loads them via storage_state. Skips login + 2FA entirely, lowest ban
  risk. Store one state file per account, gitignored.

## Cloudflare / bot-detection tiers (know which wall you're at)

1. **Browser integrity check (error 1010/1015/429)** — static
   header/TLS-fingerprint check. **Beaten by raw-socket HTTPS** with
   proper headers (see temp-email-automation). No browser needed.
2. **Turnstile challenge (JS)** — must execute JS and return a solved
   token. Plain Selenium/undetected-chromedriver/Xvfb all FAIL against a
   managed Turnstile (CDP connection is fingerprinted, not just
   `navigator.webdriver`). **Beaten by 2Captcha** minting the token via
   `method=turnstile` + site key, then submitting the token to the API.
   Diagnostic tell: if `driver.title` stays `"Just a moment..."` after
   30s+, stop trying display-mode tricks — that's not the bug.
3. **Full interactive challenge / arkose (X, some banks)** — behavioral
   fingerprinting + device checks. No reliable headless bypass. Use a
   real persisted session (imported cookies) or a paid solver that
   supports that captcha type; accept ban risk.

Where to get a Turnstile/reCAPTCHA site key without scraping HTML: on
one-api/New-API panels, `GET /api/status` returns `turnstile_site_key`.
Elsewhere, read the `data-sitekey` attr on the `cf-turnstile` /
`g-recaptcha` div.

Turnstile error decoder: `token 为空` = server looked in the wrong place →
change token placement (query param vs body vs `cf-turnstile-response`
header), don't re-solve. `校验失败` = token found but stale/invalid →
tokens are single-use & expire in minutes → re-solve.

## Anti-detection checklist (reduce, never guarantee)

- Realistic `User-Agent` + viewport + locale + timezone matching the IP.
- Don't blast actions at machine speed — add human-like delays/jitter.
- Reuse one context (cookies, canvas fingerprint) instead of fresh
  browsers each run.
- Residential/mobile IP for strict sites; datacenter & shared VPN
  (WARP/1.1.1.1) IPs are flagged fast and do NOT bypass aggressive rate
  limits.
- `undetected-chromedriver`/`playwright-stealth` patch obvious tells but
  won't beat behavioral/CDP fingerprinting.
- Expect bans on ToS-hostile targets (X). Tell the user the risk up front.

## Rate-limit reality (mass actions)

- Per-IP throttling is common (~20-25 signups/IP on aggressive panels
  before cascade). Need rotating residential/mobile IPs for 100+.
- DNS changes do NOT help IP rate limits. WARP does NOT help (shared CF
  IPs). Only fresh/rotating IPs help.
- A "429 / rate limit" during key creation is OFTEN fake — the action
  frequently succeeded server-side; retry by re-logging-in (acts as a
  browser refresh) and re-reading the list before discarding the account.
- On Termux, do NOT run a parallel watcher script — 2+ Python processes
  crash the Hermes gateway. Do retries sequentially in one script.

## Building a Zeline `browser` tool (owner-gated)

To let Zeline itself drive a browser (not just this agent), add a tool in
`zeline/tools.py` wrapping Playwright with actions: `goto`, `fill`,
`click`, `wait_for`, `get_text`, `screenshot`, `eval`, `save_state`,
`load_state`. Keep one persistent context per session. Gate to
owner/`full` profile ONLY — never expose on public gateways (it can act
as the user on live sites). Confirm with the user before any
state-changing action (post, delete, transfer, purchase); never bypass
MFA/KYC. Full build plan in `zeline-browser-tool-roadmap.md`.

## Golden rules

- Try CLI/API/raw-socket/2Captcha before ever opening a browser.
- Persist the session; never re-login when you can reload cookies.
- One state file per account, gitignored, never committed.
- Be honest about ban risk on ToS-hostile targets.
- Owner-only for any browser tool; loop the user in for money/MFA/KYC.
