# one-api / New API panel — daily check-in reference

Worked example: **tabitoken.com** (a self-hosted "New API" / one-api LLM
token gateway panel). The panel owner is a friend of the user and
permits this automation.

## Tokens file format (`~/.zeline/scripts/tabi_tokens.txt`)

```
# one token per line, optional |label ; # = comment
<access_token>|aes-utama
<access_token>|kaiaagain10-cpu
```

Get each access token from: panel → Profile/Settings → **System Access
Token**. Static until the user regenerates it.

## Endpoints (Bearer <access_token> + User-Agent header)

| Endpoint | Returns / does |
|---|---|
| `GET /api/user/self` | `data.display_name`, `id`, `github_id`, `quota` |
| `GET /api/user/checkin` | `data.stats.checked_in_today`, `checkin_count`, `records[].quota_awarded`; `data.min_quota`/`max_quota` |
| `POST /api/user/checkin?turnstile=<tok>` | check-in; `message:"签到成功"` on success |
| `GET /api/status` | `checkin_enabled`, `turnstile_check`, `turnstile_site_key`, `quota_per_unit` |

## Key facts observed

- **Reward:** 2.5M–5M credits/day, random. At 500000 credits/$1 that is
  **$5–$10 per account per day**.
- **quota_per_unit = 500000** (credits per $1). ALWAYS report $ not credits.
- Turnstile site key for tabitoken: `0x4AAAAAAEGV81TArluaPQGB`.
- Turnstile token delivery: **query param `?turnstile=`** (NOT body/header).
- Error strings:
  - `Turnstile token 为空` = server saw no token → wrong delivery location.
  - `Turnstile 校验失败，请刷新重试！` = token seen but invalid/stale.
  - `今日已签到` = already checked in today.
  - `AUTH_UNAUTHORIZED` / `invalid access token` = token dead, skip account.

## Workflow the user wants

1. Collect access tokens once (they're static). Store in tokens file.
2. On "check-in tabitoken": run `scripts/tabi_checkin.sh`.
   - For each account: read self (name+balance) → check `checked_in_today`
     → ONLY solve Turnstile + POST if not yet done → report per-account.
3. Report a table: account | status | $ awarded or $ balance.
4. User triggers manually ("pas gua suruh aja"); no fixed cron schedule
   was requested — do not auto-schedule unless asked.

## Cost discipline (user correction)

Balance is ~$2.90 of 2Captcha credit; each solve ~$0.003. Checking
`checked_in_today` first means a re-run over N accounts only spends
solves on accounts still pending — re-running the whole list after a
partial day is safe and cheap.
