---
name: newapi-daily-checkin
description: |
  Automate the daily reward check-in on any self-hosted "New-API / one-api"
  LLM token panel, using an account access token plus a Cloudflare Turnstile
  solve when the panel demands one. The panel describes itself through
  GET /api/status, so no site is hardcoded: point the runner at any fork and
  it discovers the captcha requirement, site key, credit divisor, and display
  currency. Load when a user asks to check in, claim a daily reward or quota,
  or wire a new token dashboard.
metadata:
  zeline:
    tags: [checkin, new-api, one-api, turnstile, captcha, automation]
    category: productivity
---

# Daily check-in for New-API / one-api token panels

Many LLM token resellers run the open-source **one-api / New-API** dashboard.
Every fork exposes the same REST surface and usually gates its daily reward
behind **Cloudflare Turnstile**. This skill drives *any* of them with one
runner: `scripts/newapi_checkin.sh`.

Only run this against panels you own or are explicitly authorised to use.

## Run it

```bash
PANEL=https://panel.example bash scripts/newapi_checkin.sh
```

Everything else is discovered or has a sane default:

| Variable | Meaning | Default |
| --- | --- | --- |
| `PANEL` | panel origin — the only required input | — |
| `TOKENS` | accounts file | `$ZELINE_HOME/scripts/<host>_tokens.txt` |
| `CAPTCHA_KEY` | solver API key | read from `CAPTCHA_KEY_FILE` |
| `CAPTCHA_KEY_FILE` | file holding the key | `~/.2captcha_key` |
| `SITEKEY` | override the discovered Turnstile key | from `/api/status` |

Accounts file, one per line, `#` starts a comment. Both layouts work and the
runner detects which one it is looking at:

```
TOKEN|label              # plain panel
TOKEN|USER_ID|label      # panel that requires the New-Api-User header
```

The token is the panel's **System Access Token** (Profile → System Access
Token), not an `sk-…` LLM API key. An `sk-` key authenticates model calls and is
useless for check-in.

## Why nothing is hardcoded

`GET /api/status` answers every site-specific question, so hardcoding a site key
or a credit divisor is wrong the moment the same script meets another fork:

| Field | Used for |
| --- | --- |
| `turnstile_check` | whether a captcha is needed at all — `false` means skip the solver and spend nothing |
| `turnstile_site_key` | the key to solve against; it differs per panel |
| `quota_per_unit` | credits per one display unit (commonly 500000) |
| `quota_display_type` | `USD` or `CNY` — decides the symbol |
| `usd_exchange_rate` | only needed when converting a CNY panel to USD |

**Check the currency before reporting.** A Chinese panel displays **¥**, and
reporting ¥7046 as "$7046" is a real error a user will catch. Also keep `quota`
(remaining balance) apart from `used_quota` (lifetime spend) — "how much do I
have" means `quota`.

## API shape (identical across forks)

- `GET /api/user/self` → `data.display_name`, `data.id`, `data.quota`
- `GET /api/user/checkin` → `data.stats.checked_in_today`,
  `data.stats.checkin_count` (streak), `records[0].quota_awarded`
- `POST /api/user/checkin?turnstile=<TOKEN>` → `{"success":true, "message":"签到成功"}`

The Turnstile token **must** be a query parameter. In the body or a header the
panel answers `Turnstile token 为空` (token is empty).

## Two rules the runner enforces for you

**Check `checked_in_today` before solving.** A solve costs money; an account that
already checked in must never pay for one.

**Read the POST response.** Discarding it is how a rejected token gets reported
as success — measured on a live panel: the POST returned
`{"data":{"quota_awarded":4295745},"message":"签到成功","success":true}` while an
earlier version of this runner printed `checked in: $0.29 -> $0.29`, a "success"
next to a balance that had not moved, because the quota read raced the panel's
own write. The runner now requires `success:true` **and** re-reads
`checked_in_today` from the panel before claiming anything.

## Diagnosing failures — in this order

**Every account fails the solve → it is the KEY, not the panel.** A run where all
accounts print a captcha failure is one dead solver key. Check first:

```bash
K=$(tr -d '\r\n' < ~/.2captcha_key)
curl -s "https://2captcha.com/res.php?key=$K&action=getbalance&json=1"
```

`{"status":1,"request":"2.59"}` means the key is fine. `ERROR_KEY_DOES_NOT_EXIST`
means it was rotated — write the new key to `~/.2captcha_key` (chmod 600) and
re-run. The runner probes the balance once at startup and refuses to start on a
dead key, precisely so this never appears as 13 separate "captcha failed" lines.

Keep the key in **one** place. A key pasted into several runners means one
rotation silently breaks every panel at once.

Then, in order of likelihood:

1. `ERROR_ZERO_BALANCE` — top up the solver.
2. `CAPCHA_NOT_READY` until timeout — worker pool is busy; poll longer or retry.
   Not a bug.
3. `ERROR_CAPTCHA_UNSOLVABLE` — the worker gave up; the runner retries 3×.
4. Token returned but the panel rejects it — a mismatch: wrong site key, wrong
   `pageurl` (must be the exact origin), token expired (solve → submit
   immediately), or passed somewhere other than `?turnstile=`.
5. Panel switched Turnstile to managed mode — the widget then needs `action` and
   `cdata` from the page's `cf-turnstile` div.

**Panel down is not a token problem.** Cloudflare 522/403 or a plain timeout is
the panel's infrastructure failing. Do not regenerate tokens; report it and move
on. Note also that `/api/status` can answer **HTTP 200 with an empty body** on
some panels, so a JSON parse error there is not proof the panel is dead — the
authenticated `/api/user/self` path may still work.

## Speed matters — check-in is time-critical

Check-in resets at the panel's local day rollover, and a missed day breaks the
streak permanently. Near a rollover, minutes count:

- Run the runner immediately. Do not explore with per-token curl probes first.
- Fire one process per panel in parallel — they read independent token files.
- Write logs to a path that exists (`~/ci_<panel>.log`); `/tmp` does not exist on
  Termux and the job dies with no log.
- A `process wait` timeout of 60s is not a failure. Several Turnstile solves take
  longer; call wait again.
- If asked for results mid-run, dump the balances known so far rather than
  holding everything for a prettier final pass.

## Report format

A markdown table per panel — Account | Balance | Streak, sorted by balance
descending — reads better than stacked bullets. Close with the per-panel total,
the grand total, the solver balance, and one line naming any account that was
already checked in before the run so a stale streak is not read as a failure.
Report in the panel's own currency symbol, never in raw credits.

## Adding a panel

There is nothing to add. Point `PANEL` at it and create its tokens file. If
`/api/status` shows `checkin_enabled` and a `turnstile_site_key`, the runner
drives it.

## Credentials

Keep access tokens and solver keys in local, git-ignored files only — never in a
skill, a script, a commit, or chat output. Runner output may show a short
redacted fingerprint at most. If a key is pasted into a transcript, advise
rotation rather than quoting it back.
