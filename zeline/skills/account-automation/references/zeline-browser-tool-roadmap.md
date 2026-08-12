# Roadmap: give Zeline a browser tool (Phase 2)

Goal: let Zeline log into web accounts that have no usable API (X, OAuth
click-through dashboards) the way a human does — render JS, fill forms,
click, solve captchas, keep a session. API-first tiers (see SKILL.md)
cover most needs; this is the fallback for the rest.

## Why it's not "just install something"

Zeline today can only `curl`/fetch raw HTML (`web_fetch`, `http_request`,
`download_file` in `zeline/tools.py`). It has no engine that renders
JavaScript or clicks. Logging into a modern site needs a real browser
(Chromium) plus an automation layer (Playwright/Selenium) that drives it.

Termux/Android cannot reliably run headless Chromium — Phase 2 targets a
**Linux PC/VPS**.

## Build plan

1. **Environment (on a PC/VPS, not the phone)**
   - `pip install playwright && playwright install chromium`
   - Verify: a tiny script that opens example.com and prints the title.

2. **New Zeline tool: `browser`** (new entry in `zeline/tools.py`)
   - Wrap Playwright's async API behind one tool with actions:
     `goto(url)`, `fill(selector,text)`, `click(selector)`,
     `wait_for(selector|url)`, `screenshot()`, `get_text(selector)`,
     `eval(js)`, `save_state(path)`, `load_state(path)`.
   - Keep one persistent `BrowserContext` per session so cookies/login
     survive across tool calls within a task.
   - Return concise results (text/status/screenshot path), never dump
     full DOM into the model context.

3. **Session persistence (the key to not re-logging-in)**
   - After a first successful (possibly manual) login, call
     `context.storage_state(path=...)` to dump cookies + localStorage.
   - On later runs, create the context with `storage_state=<path>` so
     the agent is already logged in — skips password + most 2FA.
   - Store state files locally, gitignored, one per account.

4. **Captcha inside the browser**
   - For Turnstile/reCAPTCHA on a page: either solve via 2Captcha and
     inject the token into the widget's response field via `eval`, or
     use a 2Captcha browser-extension flow. Prefer the API tiers first.

5. **Safety gating (must-have)**
   - Expose `browser` ONLY to the owner / `full` tool profile. NEVER on
     public gateways (it can act as the user on live sites).
   - Confirm with the user before any state-changing action on a real
     account (post, delete, transfer, purchase).
   - Do sensitive MFA/OAuth-consent steps with the user in the loop.

## Per-service notes

- **GitHub**: DON'T use the browser — `gh` CLI + API already do
  everything (push, PR, merge). Browser only if a web-only setting has
  no API.
- **X/Twitter**: no meaningful free API; login is heavily
  anti-automation (device checks, arkose, fingerprinting). Automated
  login risks suspension. Safest: export cookies from a real logged-in
  session, load via `storage_state`, run on a stable IP, accept ban
  risk. Set this expectation explicitly with the user.
- **Generic dashboards**: try API + token first; browser only if the
  action truly has no endpoint.

## Definition of done

- `browser` tool merged into Zeline, owner-gated.
- A demo: log into a test site, persist state, reuse it next run without
  re-entering credentials.
- Docs page + this skill updated to move X/generic-login from "roadmap"
  to "supported (PC/VPS)".
