# one-api / New API panel — daily check-in reference

Many LLM token resellers run the open-source **one-api / New API** dashboard, and
every fork gates its daily reward behind Cloudflare Turnstile. The complete,
site-agnostic runner lives in the `newapi-daily-checkin` skill
(`scripts/newapi_checkin.sh`); this page is the protocol reference behind it.

Only automate panels you own or are explicitly authorised to use.

## Ask the panel, do not hardcode it

`GET /api/status` answers every site-specific question. Hardcoding any of these
values breaks the moment the same code meets another fork:

| Field | Meaning |
| --- | --- |
| `checkin_enabled` | whether this panel has a daily reward at all |
| `turnstile_check` | `false` → no captcha; skip the solver and spend nothing |
| `turnstile_site_key` | the key to solve against — different on every panel |
| `quota_per_unit` | credits per one display unit (commonly 500000) |
| `quota_display_type` | `USD` or `CNY`; decides the symbol you print |
| `usd_exchange_rate` | only for converting a CNY panel into USD |

A panel may answer `/api/status` with **HTTP 200 and an empty body**, so a JSON
parse failure there does not prove the panel is down — the authenticated
endpoints below can still work.

## Tokens file

```
# one account per line, "#" starts a comment
<access_token>|label                # plain panel
<access_token>|<user_id>|label      # panel requiring the New-Api-User header
```

Get the token from panel → Profile/Settings → **System Access Token**. It stays
valid until regenerated. An `sk-…` key is an LLM API key and cannot check in.

Which layout a panel needs is discoverable: call `GET /api/user/self` with the
token alone. If the panel answers `Unauthorized, New-Api-User header not
provided`, it needs the numeric id form. Some forks use ids in the 80000+ range,
so never brute-force a small range — read it from the profile page.

## Endpoints (`Authorization: Bearer <token>` + a normal User-Agent)

| Endpoint | Returns / does |
| --- | --- |
| `GET /api/user/self` | `data.display_name`, `data.id`, `data.quota`, `data.used_quota` |
| `GET /api/user/checkin` | `data.stats.checked_in_today`, `checkin_count` (streak), `records[].quota_awarded`, `data.min_quota`/`max_quota` |
| `POST /api/user/checkin?turnstile=<token>` | performs the check-in |
| `GET /api/status` | the discovery fields above |

`quota` is the remaining balance; `used_quota` is lifetime spend. "How much do I
have" means `quota` — mixing them up is a reporting error users notice.

## Turnstile delivery

The token **must** ride as the query parameter `?turnstile=`. In the body or a
header the panel replies `Turnstile token 为空` (token empty). Tokens live only a
few minutes, so solve and submit immediately instead of batch-solving first.

Error strings worth recognising:

| Response | Meaning |
| --- | --- |
| `Turnstile token 为空` | no token seen — wrong delivery location |
| `Turnstile 校验失败，请刷新重试！` | token seen but invalid or stale |
| `今日已签到` | already checked in today |
| `AUTH_UNAUTHORIZED` / `invalid access token` | token dead; skip the account |

A successful POST returns the reward, which is the only trustworthy confirmation:

```json
{"data":{"checkin_date":"2026-09-03","quota_awarded":4295745},"message":"签到成功","success":true}
```

**Read that body.** Discarding it and printing a balance diff instead reports a
rejected token as success, and the balance read can race the panel's own write —
measured live: a real `success:true` with `quota_awarded` alongside a balance that
still showed the old value. Require `success:true`, then re-read
`checked_in_today` from the panel before claiming anything.

## Cost discipline

Each solve costs money, so check `checked_in_today` **before** solving. That makes
re-running a partial list safe: only still-pending accounts spend a solve.

## When every account fails the solve, suspect the key

A run where *all* accounts report a captcha failure is one dead solver key, not a
panel outage. Verify before touching anything else:

```bash
K=$(tr -d '\r\n' < ~/.2captcha_key)
curl -s "https://2captcha.com/res.php?key=$K&action=getbalance&json=1"
```

`{"status":1,...}` means the key is good; `ERROR_KEY_DOES_NOT_EXIST` means it was
rotated. Keep the key in exactly one file so a single rotation cannot silently
break every panel at once.

Cloudflare 522/403 or a plain timeout is the panel's infrastructure failing —
report it and move on rather than regenerating working tokens.
