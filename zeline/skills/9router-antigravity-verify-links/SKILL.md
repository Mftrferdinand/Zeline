---
name: 9router-antigravity-verify-links
description: When to use: user runs 9Router with multiple "antigravity" (Gemini Code Assist / aicode-consumers) accounts and some are stuck at HTTP 403 "user validation required". Generate the fresh per-account Google verification links, or check which accounts are healthy vs need verify.
category: ops
---

# 9Router Antigravity Verify Links

> When to use: user runs 9Router with multiple "antigravity" (Gemini Code Assist / aicode-consumers) accounts and some are stuck at HTTP 403 "user validation required". Generate the fresh per-account Google verification links, or check which accounts are healthy vs need verify.

## What this does
Reads each antigravity account's OAuth access token straight out of 9Router's SQLite DB, calls Google's quota endpoint, and extracts the `validation_url` (the Google sign-in link) from the 403 error. 200 = healthy, 403 = needs verify (link returned).

## Steps

1. Confirm DB path + schema (adjust if 9Router version differs):
   ```bash
   python3 -c "
   import sqlite3, os, json
   db = os.path.expanduser('~/.9router/db/data.sqlite')
   c = sqlite3.connect(db)
   print([r[1] for r in c.execute('PRAGMA table_info(providerConnections)')])
   rows = c.execute('SELECT email, data FROM providerConnections WHERE provider=\"antigravity\"').fetchall()
   print('accounts:', len(rows))
   print('data keys:', list(json.loads(rows[0][1]).keys()))
   "
   ```
   Table `providerConnections`, filter `provider='antigravity'`, `data` is JSON with `accessToken` / `refreshToken` / `projectId`.

2. Mint verify links for the target accounts (pass the emails you care about in `targets`):
   ```bash
   python3 -c "
   import sqlite3, os, json, urllib.request, urllib.error, time
   url = 'https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota'
   c = sqlite3.connect(os.path.expanduser('~/.9router/db/data.sqlite'))
   rows = c.execute('SELECT email, data FROM providerConnections WHERE provider=\"antigravity\"').fetchall()
   c.close()
   targets = ['a@gmail.com','b@gmail.com']   # <-- edit
   for email, ds in rows:
       if email not in targets: continue
       tok = json.loads(ds).get('accessToken','')
       req = urllib.request.Request(url, method='POST',
           headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json',
                    'User-Agent': 'antigravity/ide/2.1.1'},
           data=b'{}')   # <-- EMPTY body, critical
       try:
           urllib.request.urlopen(req, timeout=20)
           print(f'{email}: OK (no verify)')
       except urllib.error.HTTPError as e:
           body = e.read().decode(errors='replace')
           if e.code == 403:
               for det in json.loads(body).get('error',{}).get('details',[]):
                   vu = det.get('metadata',{}).get('validation_url')
                   if vu: print(f'{email}: VERIFY -> {vu}')
           else:
               print(f'{email}: HTTP {e.code} {json.loads(body).get(\"error\",{}).get(\"message\",\"\")[:120]}')
       except Exception as e:
           print(f'{email}: ERR {str(e)[:120]}')
       time.sleep(1)
   "
   ```

3. Hand each `VERIFY ->` URL to the user. They open it logged in as that exact Google account (separate incognito tab per account so `authuser` doesn't cross over), finish the prompt/SMS challenge until `auth_success_gemini` loads. Then re-run step 2 to confirm the account flips to OK, and re-enable it in 9Router's rotation.

## Refreshing access tokens manually (when `expiresAt` is in the past)

Check expiry first: parse `data.expiresAt` (ISO, `Z` suffix) vs `datetime.now(timezone.utc)`. If expired, refresh before diagnosing — a stale token can mask the real state.

1. Find the working OAuth client pair — do NOT guess/hardcode from memory; grep 9Router's installed source (path: `$(which 9router)` dir, usually `/usr/lib/node_modules/9router` or Termux equivalent):
   ```bash
   grep -rhoE "[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com" <9router-src> | sort -u
   grep -rhoE "GOCSPX-[A-Za-z0-9_-]+" <9router-src> | sort -u
   ```
   There are usually 2 client-ids × 2 secrets but only ONE working pair. Test combinations against one refresh token; the wrong one returns 401 `invalid_client`, the right one returns an `access_token`. (Values are secrets — keep them out of chat/skills; note only "the pair that works was found via grep".)

2. Refresh + re-test + save back into the DB in one pass:
   ```python
   # POST https://oauth2.googleapis.com/token (form-encoded):
   #   client_id, client_secret, refresh_token=<data.refreshToken>, grant_type=refresh_token
   # -> access_token; then re-run the retrieveUserQuota probe with the NEW token.
   # On HTTP 200, write accessToken + new expiresAt (now + expiresIn) back:
   #   conn.execute('UPDATE providerConnections SET data=? WHERE id=?', (json.dumps(d), row_id))
   ```

## Interpreting re-checks after the user says "already verified"

- Still 403 `VALIDATION_REQUIRED` **with a freshly refreshed token** → the verification genuinely did not land. Most common causes: user completed the challenge on the wrong Google account (browser logged into a different one), the link had expired before opening (plt= tokens are short-lived — always regenerate fresh right before sending), or Google propagation delay (can take 1–2 h after success page).
- Each probe call generates a NEW validation_url different from the previous one — that's normal; always deliver the latest batch.

## Onboarding new Antigravity accounts via 9Router OAuth

To add a new Google / Google Workspace account to 9Router under the `antigravity` provider:

### Manual (Browser Link):
1. Generate OAuth link + PKCE verifier using 9Router's internal CLI client:
   ```javascript
   const client = require('/data/data/com.termux/files/usr/lib/node_modules/9router/src/cli/api/client.js');
   const res = await client.getOAuthAuthUrl('antigravity');
   // returns: { authUrl, state, codeVerifier, redirectUri }
   ```
2. User opens `authUrl` in browser, signs in, and authorizes permissions.
3. Browser redirects to `http://localhost:20128/callback?code=...`.
4. Exchange the code and save token into 9Router:
   ```javascript
   await client.exchangeOAuthCode('antigravity', {
     code: '<extracted_code>',
     redirectUri: res.data.redirectUri,
     codeVerifier: res.data.codeVerifier,
     state: res.data.state
   });
   ```

### Automated Headless Flow (Selenium + 2Captcha in Termux):
When automating bulk Google Workspace/GSuite logins in Termux (`chromium-browser --headless=new`):

0. **CRITICAL — bypass "Browser tidak lagi didukung / not-supported":** Termux Chromium headless gets redirected to `myaccount.google.com/not-supported?pli=1` right after password submit unless you spoof the UA and hide automation. A plain `--user-agent=` arg is NOT enough. You MUST use CDP:
   ```python
   opts.add_argument("--disable-blink-features=AutomationControlled")
   opts.add_argument("--lang=en-US")
   opts.add_experimental_option("excludeSwitches", ["enable-automation"])
   # after driver start:
   UA = drv.execute_cdp_cmd("Browser.getVersion", {})["userAgent"].replace("Headless", "")
   drv.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": UA})
   drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source":
       "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
       "window.chrome={runtime:{}};"
       "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
       "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"})
   ```
1. Navigate directly to `authUrl` generated by `getOAuthAuthUrl('antigravity')`.
2. Input identifier (`email`) into `input#identifierId`, then submit using `send_keys(Keys.ENTER)`. (Avoid clicking `#identifierNext` directly as it frequently raises `ElementNotInteractableException`).
3. If Google triggers "Type the text you hear or see": the image is at `//img[contains(@src,'Captcha')]` and the input box is `name='ca'`. Grab the CAPTCHA image bytes via the element's OWN screenshot (`imgs[0].screenshot_as_png`) — NOT by re-fetching the src URL (the ctoken URL is single-use/session-bound and re-fetching returns a different/expired image). Send base64 to 2Captcha (`method=base64`), type solution into `name='ca'`, press `Keys.ENTER`. Note: CAPTCHA can appear on the IDENTIFIER page (before email submit) for Workspace domains, not only later — check for it both before and after the email step.
4. Input password into `input[type="password"]`, then submit using `send_keys(Keys.ENTER)`.
5. If GSuite speedbump ("I understand" / "Saya mengerti") appears, click it. Pastikan klik menggunakan execute_script JS click dan scrollIntoView, serta matcher mencakup role="button" dan button tags.
6. On OAuth consent page: button text is often nested inside spans (e.g. `'Login'`, `'Allow'`, `'Continue'`, `'Izinkan'`). Iterate `driver.find_elements(By.XPATH, "//button | //*[@role='button'] | //input[@type='submit']")` dan lakukan JS click (`drv.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", el)`). Hati-hati dengan single vs double quote string Python di xpath.
7. Once clicked, driver lands on `http://localhost:20128/callback?code=...&state=...`.
8. Extract `code` and `state`, then call `client.exchangeOAuthCode('antigravity', ...)` via node to insert directly into 9Router DB.

### Testing / Verifying Registered Connections via 9Router Internal Client

Untuk menguji langsung status koneksi tanpa perlu curl/probe manual ke Google:
```javascript
const client = require('/data/data/com.termux/files/usr/lib/node_modules/9router/src/cli/api/client.js');
// Single connection test:
const res = await client.testConnection('<connection_id>');
// res.data -> { valid: true, error: null, refreshed: false }
```
Menguji koneksi via `testConnection()` secara otomatis memperbarui record `testStatus: 'active'` pada SQLite `providerConnections`.

## "Quota API access forbidden" / chat 403 for ALL accounts → check client-version deprecation FIRST

Trigger: 9Router dashboard shows every antigravity card with **"Antigravity quota API access forbidden. Chat may still work."** and/or chat returns 403 — several accounts at once, not one.

Do NOT conclude "bad accounts" or "need re-verify". Diagnose in this order (2 tokens max, ~2 min):

1. Token is valid → `POST https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist` with body
   `{"metadata":{"pluginType":"GEMINI"}}` and header `User-Agent: Google-Api-Client/2.0 antigravity`.
   **200 = token good.** Read the body — this is the real diagnosis:
   ```json
   {"allowedTiers":[{"id":"standard-tier", ...}],
    "ineligibleTiers":[{"reasonCode":"UNSUPPORTED_CLIENT",
      "reasonMessage":"This client is no longer supported for Gemini Code Assist for individuals. To continue using Gemini, please migrate to the Antigravity suite of products: https://antigravity.google",
      "tierId":"free-tier"}]}
   ```
   `UNSUPPORTED_CLIENT` + `free-tier` in `ineligibleTiers` + only `standard-tier` allowed ⇒ **Google retired the free-tier client path.** This is a 9Router-side regression, NOT an account problem. Every account (old and new) fails identically.

2. Cross-check endpoints (all from the same token):
   - `v1internal:loadCodeAssist` → **200** (token fine)
   - `v1internal:fetchAvailableModels` (body `{}`) → **403** FREE-TIER-only account: `"You must be a named user on your organization's\nGemini Code Assist Standard edition subscription to use this service."`
   - `v1internal:retrieveUserQuota` → **404** → the URL 9Router probes is gone/renamed; that 404 is what renders the "quota API access forbidden" warning.
   - `v1internal:streamGenerateContent?alt=sse` (body `{"model":"gemini-2.5-flash","request":{"contents":[{"role":"user","parts":[{"text":"Reply with exactly: OK"}]}]}}`) → **403 PERMISSION_DENIED** same message. Proves chat is dead too, not just quota.

3. Loop all accounts in one pass to prove it's global, not per-account:
   ```python
   # for email, data in rows: call loadCodeAssist + fetchAvailableModels with data['accessToken']
   # all-403 "does not have permission" / "named user ... Standard edition" => client-version issue, stop re-verifying
   ```

**Interpretation table**
| Symptom pattern | Real cause | Fix |
|---|---|---|
| All accounts 403 "named user ... Standard edition" | Google retired the free-tier/legacy client | Update 9Router's antigravity provider to the new Antigravity client; no account change helps |
| All accounts 403 with `validation_url` in `error.details[].metadata` | Per-account verify challenge | Use the verify-link flow above |
| One account 403, others 200 | That account only | Refresh token / re-verify that one |
| `retrieveUserQuota` 404 | Endpoint renamed upstream | Don't treat as account fault |

**Pitfalls specific to this case**
- The warning text **"Chat may still work"** is misleading for `UNSUPPORTED_CLIENT` — verify with the real `streamGenerateContent` probe before telling the user chat is fine. Here it was NOT fine (403).
- `retrieveUserQuota` returning a Google **HTML 404 page** means the method no longer exists at that URL — do not retry-loop it.
- When the whole pool fails the same way, **stop binding more accounts.** Adding accounts cannot fix a client-version block.
- Do not delete/re-add the accounts to "fix" it — tokens are valid, the call path is the problem.

**Legacy vs newly-bound accounts (critical distinction)**
- A newly-OAuth'd account (bound AFTER Google retired the free-tier client) gets `UNSUPPORTED_CLIENT` / "named user ... Standard edition" on ALL calls — loadCodeAssist returns `ineligibleTiers: [free-tier: UNSUPPORTED_CLIENT]`.
- An older account bound BEFORE the cutoff may STILL work because Google grandfathered its session/tier. This is NOT a domain difference — same domain, same password, same 9Router, only the binding date differs.
- **Diagnostic proof:** if ANY old account still works (200 on loadCodeAssist AND 200 on streamGenerateContent), the 9Router endpoint path is alive. The problem is per-account tier state, not the endpoint.
- **Therefore: don't conclude "domain blocked" or "endpoint dead" when old accounts work.** Compare an old working account vs a new failing one with the same 3-probe sequence. If old=200 and new=403 `UNSUPPORTED_CLIENT`, the fix is NOT more binding — it's either (a) wait for 9Router to update to the new Antigravity client, or (b) find the new Antigravity-native endpoint that replaces `cloudcode-pa.googleapis.com/v1internal`.

## Pitfalls
- **Re-bind setelah password salah: HAPUS dulu record lama dari DB.** Jika binding pertama gagal (Google `enter your password → wrong`), record tetap tidak masuk DB. Tapi JIKA somehow record sudah ada (entah dari attempt sebelumnya yang partial-success, atau dari batch berbeda), script binding akan SKIP dengan log `SKIP (already bound): email@domain` — padahal akun itu mungkin di-bind dengan password/kredensial yang berbeda. Sebelum re-bind dengan password baru, hapus record lama: `DELETE FROM providerConnections WHERE email=?`, baru jalankan binding. Cek dengan `SELECT email,isActive,data->testStatus FROM providerConnections WHERE email=?` — jika ada, hapus dulu.
- **Password salah ≠ binding/domain/endpoint issue.** User sering memberi password salah/lama, lalu mengoreksi di pesan berikutnya. Jangan bertele-tele mendiagnosis "domain diblokir" atau "client deprecated" saat Google log menunjukkan `enter your password → wrong → redirect challenge/pwd`. Itu murni password salah. Konfirmasi password ke user, hapus record lama jika ada, lalu re-bind. Jangan over-analyze.
- **"Akun lama masih oke" = jangan salahkan domain/endpoint/binding.** Jika user bilang akun lama jalan dan akun baru kena 403, bedakan dulu: apakah akun baru di-OAuth SETELAH Google retirement (→ `UNSUPPORTED_CLIENT`, tidak bisa di-fix dengan re-bind) atau sebelum (→ mungkin token expired/verify). Test akun lama vs baru dengan 3-probe sequence yang sama.
- **Wrong password ≠ binding failure.** Log Google `enter your password → wrong` + redirect ke `challenge/pwd` berarti password salah, bukan CAPTCHA/selector/domain. Konfirmasi password ke user sebelum retry binding.
- **Jangan buru-buru menyimpulkan "domain diblokir Google / Account Access Restricted":** jika flow mental kembali ke identifier, kemungkinan besar ada CAPTCHA yang belum selesai disolve, password salah, atau selector tombol speedbump/consent tidak cocok (misal mencari 'Allow' padahal tombolnya bertuliskan 'Login'). Buktikan lewat dump text/screenshot sebelum klaim akun tidak bisa diikat.
- **Transient HTTP 429 after bulk onboarding:** Right after binding multiple accounts, automated probes or immediate concurrent requests may trigger HTTP 429 (`Resource has been exhausted / quota`). 9Router will mark them `unavailable` with backoffLevel > 0. This is a temporary burst rate-limit cooldown, NOT an invalid token or account ban. After ~1-2 minutes cooldown, test isolated requests through 9Router's `/v1/chat/completions` (model `ag/...`) to verify they recover to `active`.
- **Body MUST be `{}`.** Sending `{"project_id":...}` or `{"projectId":...}` returns HTTP 400 `Unknown name "project_id": Cannot find field` — and that 400 *masks* the real 403 verify state, so you wrongly conclude the account is fine. Empty body is what surfaces the true 403 + validation_url.
- The verify link lives at `error.details[].metadata.validation_url` (a `accounts.google.com/signin/continue?...GlifWebSignIn...` URL), NOT in the top-level message.
- Links are freshly generated each call and short-lived — deliver them right after generating, don't reuse old ones from a prior run.
- `accessToken` may be expired (`expiresAt` in the past); if you get 401 instead of 403/200, the token needs refresh first (9Router refreshes on use, or trigger a request through it).
- Don't print raw tokens to chat — only the validation_url and email are safe to show.
