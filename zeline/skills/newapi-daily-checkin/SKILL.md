---
name: newapi-daily-checkin
description: |
  Automate daily check-in on ANY "New-API / one-api" LLM token panel
  (tabitoken.com, gorouter.app, and the many other one-api forks) using
  an account access token + Cloudflare Turnstile solved via 2Captcha.
  Panel-agnostic: detect support, read config from /api/status, run the
  generic template. Works across multiple accounts per panel. Load when
  the user says "checkin <panel>", adds a new token panel, or asks to
  claim daily quota/credit on a token dashboard.
metadata:
  zeline:
    tags: [checkin, new-api, one-api, turnstile, 2captcha, automation]
    category: productivity
---

# Daily check-in for New-API / one-api token panels (any panel)

Tons of LLM token resellers run the open-source **one-api / New-API**
dashboard. They all share the same REST shape and gate their daily
reward check-in behind **Cloudflare Turnstile**. This skill checks in
**any** such panel using an account **access token** + a Turnstile
token solved by **2Captcha**. tabitoken.com and gorouter.app are just
the first two wired up — the method is identical for any fork.

**500,000 credit = $1.** ALWAYS report balances/rewards in `$`, never
raw M/credit (user preference).

## Step 0 — is this panel supported?

```
curl -s <BASE>/api/status | python3 -m json.tool
```

If the JSON `data` has `checkin_enabled:true` and a
`turnstile_site_key`, it's a one-api panel this skill can drive. Note
the `turnstile_site_key` and `quota_per_unit` (usually 500000).
`turnstile_check:false` → no captcha needed, skip 2Captcha entirely.

## Auth model (differs per panel — check both)

Header always: `Authorization: Bearer <token>` + UA `Mozilla/5.0`.
Some forks (e.g. gorouter) ALSO require `New-Api-User: <numeric id>` on
every authed request, else `Unauthorized, New-Api-User header not
provided`. Test with `GET /api/user/self`:
- Works with token alone → plain panel (tabitoken-style).
- Says "New-Api-User header not provided" → needs the id header.

Finding the id: it's on the account's Profile page. If the user hands
you tokens and candidate ids **separately/unordered**, cross-match by
looping every id × every token against `/api/user/self` and keep the
pairs that authenticate (one id ↔ one token; rest error). No captcha
needed for this probe.

## API shape (same across forks)

- `GET /api/user/self` → `data.display_name`, `data.id`, `data.quota` (÷500000 = $).
- `GET /api/user/checkin` → `data.stats.checked_in_today`, `checkin_count` (streak), `records[0].quota_awarded`, `min_quota`/`max_quota`.
- `POST /api/user/checkin?turnstile=<TOKEN>` → success `{"success":true}` / `签到成功`; already done `今日已签到`.
  - Turnstile token MUST be a **query param** `?turnstile=`, not body/header (body → `Turnstile token 为空`).

## Golden rule (user-stated) — save 2Captcha fees

**ALWAYS check `checked_in_today` BEFORE solving Turnstile.** A solve
costs ~$0.003. Skip accounts already checked in. The template does this;
never bypass it.

## Turnstile via 2Captcha

1. Submit: `POST https://2captcha.com/in.php` params `key, method=turnstile, sitekey=<site_key>, pageurl=<BASE>/, json=1` → task id.
2. Poll: `GET https://2captcha.com/res.php?key=..&action=get&id=<id>&json=1` every ~7s until `status:1`; `request` = the ~800-char token.
3. Token is single-use, expires in minutes. Site key is per-panel (from `/api/status`).

Check 2Captcha balance:
`curl -s "https://2captcha.com/res.php?key=$CAP&action=getbalance&json=1"`

## Adding a new panel (the whole point)

The runner is generic — one script per panel, differing only in a few
top vars. Copy `scripts/checkin_template.sh`, fill:
- `BASE` = panel origin, `SITEKEY` = from `/api/status`,
- `NEEDS_USER_HEADER` = `true`/`false`,
- `TOKFILE` = a per-panel tokens file.

Tokens file line format:
- plain panel: `TOKEN|label`
- New-Api-User panel: `TOKEN|USERID|label`
- `#` = comment line.

Keep secrets (2Captcha key, tokens, ids) ONLY in local gitignored files
— never commit them. The template ships with `REPLACE_ME` placeholders.

## Example panel scripts

- tabitoken.com — plain token; create a project-local script and gitignored token file.
- gorouter.app — needs `New-Api-User`; create a project-local script and gitignored token file.

Run the script from its actual project path. This skill does not claim those
panel-specific scripts or credentials are preinstalled.

## Report format (user preference — use verbatim)

Per panel, per account, capture balance BEFORE and AFTER:

```
GoRouter
• Username : <display_name>
• Yesterday : $<balance before check-in>
• Now       : $<balance after check-in>
```

Always `$` (quota ÷ 500000), never M/credit. "Yesterday" = the `/self`
balance read right before check-in.

## When 2Captcha "won't work" — troubleshooting (common!)

Turnstile solving fails intermittently. It's almost never random — check
these in order (the `res.php` response tells you which):

1. **`ERROR_ZERO_BALANCE`** — top up 2Captcha. Check with
   `action=getbalance`. Each Turnstile solve ≈ $0.003.
2. **`CAPCHA_NOT_READY` on every poll then times out** — worker pool
   busy/slow. Poll longer (up to ~120s) or resubmit. Not a code bug.
3. **`ERROR_CAPTCHA_UNSOLVABLE`** — worker gave up. Just resubmit the
   task; 2nd/3rd attempt usually succeeds. Build in 1–2 retries.
4. **Token returned but server says `Turnstile 校验失败 / token 为空`** —
   the token is being rejected, meaning a MISMATCH:
   - **Wrong `sitekey`** — must be the panel's live key from
     `/api/status` (they differ per panel; don't reuse another panel's).
   - **Wrong `pageurl`** — must be the exact origin (`https://<panel>/`).
     A token minted for domain A is invalid on domain B.
   - **Token expired** — Turnstile tokens live only a few minutes.
     Solve → submit check-in IMMEDIATELY, don't batch-solve then wait.
   - **Passed in the wrong place** — MUST be `?turnstile=` query param.
     Body or header → `token 为空`.
5. **Panel switched Turnstile to managed/interactive mode** — then the
   widget needs extra `action` / `cdata` params. Add them to the 2Captcha
   submit (`&action=<x>&data=<cdata>`), read from the panel's page HTML
   (`data-action` / `data-cdata` on the `cf-turnstile` div). Rare.
6. **IP mismatch** — a few strict Turnstile configs bind the token to the
   solver's IP. Use 2Captcha's `proxy`/`proxytype` params so the token is
   minted from the same IP that submits. Rare; only if all else passes.

Rule of thumb: if `/api/status` shows the right sitekey and you pass a
fresh token as `?turnstile=` on the correct origin, it works. "Oon"
failures are usually **expired token** (waited too long) or
**UNSOLVABLE** (needs a retry).

## Pitfalls

- **`UID` is read-only in bash** — using it as a var aborts the loop
  silently (`UID: readonly variable`). Use `UUID`.
- **Access tokens are static** until the user regenerates them in
  Profile → System Access Token. Expired → template prints
  `SKIP: token/id invalid`; ask for a fresh one.
- Only run for panels the user owns or is explicitly authorized to use.

See `scripts/checkin_template.sh` for the sanitized generic runner.
