---
name: 9router-antigravity-batch-bind
description: When to use: User wants to bind Google / Workspace accounts (@paragadis.com etc.) to 9Router provider 'antigravity' (Gemini Code Assist) in batch or single-run on Termux Android headless Chromium.
category: automation
---

# 9Router Antigravity Batch Account Binding

> When to use: User wants to bind Google / Workspace accounts (@paragadis.com etc.) to 9Router provider 'antigravity' (Gemini Code Assist) in batch or single-run on Termux Android headless Chromium.

## Overview
Binding Antigravity accounts to 9Router involves:
1. Triggering 9Router Antigravity OAuth login (`/api/connections/antigravity/login` -> get auth URL + state).
2. Automating Google login using Termux headless Chromium (`chromium-browser --headless=new --no-sandbox`).
3. Handling Image CAPTCHAs on the Google sign-in identifier page if present (solving via 2Captcha normal image solver).
4. Handling Google Workspace speedbump (`workspacetermsofservice` / "I understand" / "Saya mengerti").
5. Clicking Google first-party OAuth consent ("Login" / "Allow").
6. Intercepting the redirect to `http://localhost:8085/api/connections/antigravity/callback?code=...` or polling current URL / network logs.
7. Submitting code & state back to 9Router `/api/connections/antigravity/callback`.

## Ingesting a new account list (repeatable pattern)
The script hardcodes `ALL_ACCOUNTS`. When the operator sends a fresh batch as a `email:password` txt file (usually a Telegram attachment at `/storage/emulated/0/Download/Telegram/`), regenerate the block in-place instead of editing by hand:

```python
import re
p = os.path.expanduser('~/ag_bind_all.py')
src = open(p).read()
accs = []
for line in open('~/ag_paragadis_25.txt'):
    line = line.strip()
    if not line or ':' not in line: continue
    em, pw = line.rsplit(':', 1)
    accs.append((em.strip(), pw.strip()))
block = "ALL_ACCOUNTS = [\n" + "".join(f'    ("{e}", "{w}"),\n' for e, w in accs) + "]\n"
src = re.sub(r"ALL_ACCOUNTS = \[.*?\n\]\n", block, src, flags=re.S)
open(p, 'w').write(src)
```

Then run detached (each account takes 40-80s, so a 25-account batch is ~25-35 min — never foreground). The plain `nohup ... &` one-liner can return `exit=-15` (the whole call gets SIGHUP'd and no log appears) — use `setsid` + `disown`-free redirect so the batch survives the turn:

```bash
cd /data/data/com.termux/files/home && setsid nohup python3 /data/data/com.termux/files/home/ag_bind_all.py \
  > /data/data/com.termux/files/home/ag_bind_batch.log 2>&1 < /dev/null &
sleep 25; pgrep -f ag_bind_all.py; tail -15 ag_bind_batch.log
```

Do NOT chain sleeps in one foreground turn for the whole batch (see CRITICAL section above: `sleep 180` will blow the 60s cap).

Poll with `grep -c exchanged <log>` + `tail`. Do NOT chain sleeps in one foreground turn for the whole batch.

## CRITICAL — resolve the correct source list BEFORE touching ALL_ACCOUNTS
The `ALL_ACCOUNTS` block is **sticky**: whatever was in the script from the previous session is still there, and the helper glob that scrapes `/storage/emulated/0/Download/Telegram/` will happily return the *old* batch files. Do NOT assume "the accounts in the script" or "the newest-looking filenames in Telegram" are the accounts the operator means.

Before regenerating the block, confirm the source explicitly:
- Which file / which chat message carries the new accounts, and how many are expected.
- If the operator says "akun baru" / "bukan yang lama" / "gua kirim datanya" → the data is *incoming*; STOP and wait for them to send it. Do not start a batch off a list you guessed.
- A glob like `TG + ["GSUITE-*.txt", "BAL-*.txt"]` silently mixes batches. Print the resolved count and the first 3 emails, and compare against what the operator said before launching.

Symptom of getting this wrong: a long detached run starts binding the previous batch forever, DB grows, and the operator has to interrupt and shout. Verify source = cheapest step in the whole procedure.

## Pick the right resume/running pattern per run size
For a batch of **many accounts (say >30)**, sleeping in the foreground to poll is guaranteed to time out (`sleep 180` exceeds the default 60s run_shell cap). Instead:
- Start detached, then poll with `run_shell(..., timeout=300)` and a *short* sleep (e.g. `sleep 45`) per call, or
- Poll without sleeping at all — one `grep -c` + `tail -5` call costs nothing and the batch keeps running between turns; just call it again a bit later.
Never chain `sleep` ≥ the tool timeout inside one call.

## Failure classification (verified across batches)
- `no_code` with url still on `accounts.google.com/v3/signin/identifier` → image CAPTCHA on the identifier page not solved in time. Transient; retry passes ~80%.
- `error` with `urlopen error [Errno 7] No address associated with hostname` → **phone connectivity dropped mid-run**, NOT an account or 9Router problem. Just retry.
- `Cannot take screenshot with 0 width` → headless window collapsed to 0px (glitch); retry.
- `access restricted` in body → the domain is actually restricted; do NOT retry blindly.

## Key Pitfalls & Hard Lessons
1. **Termux Chromium Path:**
   Always use explicit binary and service path in Selenium:
   ```python
   opts.binary_location = '/data/data/com.termux/files/usr/bin/chromium-browser'
   service = Service(executable_path='/data/data/com.termux/files/usr/bin/chromedriver')
   ```
2. **Do NOT use plain driver.find_element().click() for OAuth buttons:**
   Buttons like "I understand", "Login", "Allow" often sit inside complex web components or outside visible viewport in headless mode. Always use JS click with scroll:
   ```python
   drv.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", el)
   ```
3. **Workspace speedbump & firstparty consent target text:**
   Target keywords for clicking through:
   `["i understand", "saya mengerti", "login", "continue", "allow", "izinkan", "confirm", "agree", "accept"]`
   Search with XPath `//button | //*[@role='button'] | //input[@type='submit']`.
4. **Isolated user-data-dir:**
   Use a unique temp folder per account (e.g. `~/.ag_p_<safe_username>`) and clean it up immediately after the run to prevent session pollution and save disk space.
5. **Database verification:**
   Check DB directly at `~/.9router/db/data.sqlite`:
   ```sql
   SELECT id, email, provider, createdAt FROM providerConnections WHERE provider='antigravity';
   ```
   Note: `providerConnections` does NOT have a `status` column.
6. **`no_code` failures at identifier page:**
   Some accounts (~30%) fail with status `no_code` — the Google identifier page serves an image CAPTCHA that 2Captcha doesn't solve in time, or the CAPTCHA element isn't detected by the script. These are transient; retry the same accounts in a second batch run and they usually pass. The script auto-skips accounts already in DB, so safe to re-run with the full list.
7. **Retry workflow for failed accounts:**
   After a batch run, filter failed accounts and retry:
   ```bash
   grep -B2 '"no_code"' ag_bind_all.log | grep "BINDING:" | awk '{print $3}'
   ```
   Update `ALL_ACCOUNTS` di script hanya dengan email tersebut, lalu re-run — script auto-skip yang sudah di DB.
   Dari sesi 25 akun cayadi: 19 OK round 1, 5/6 OK di retry pertama (83%). Satu akun yang tetap `no_code` setelah 2x retry kemungkinan butuh verifikasi manual Google. Common retry errors:
   - `no_code` → CAPTCHA timing, usually passes on retry.
   - `ERR_NAME_NOT_RESOLVED` / `Network is unreachable` → phone connectivity dropped; wait for stable connection and retry.
   - HTTP 429 / `Resource exhausted` → Google rate-limit cooldown; wait 5-10 min.
   - `Cannot take screenshot with 0 width` → Chromium headless window collapsed to 0px (transient glitch); simply retry.
8. **Typical success rate:** ~70-84% first-run, ~90-100% after one retry (assuming stable network). Accounts that fail 3+ times may need manual Google login to clear a security challenge.
9. **Batch 25 akun paragadis (Sep 2026 verified):**
   Round 1: 21/25 (84%). Failures: 4× `no_code` (CAPTCHA), 2× network error (DNS drop + conn reset — transient HP connectivity).
   Retry 1: lasma + boru OK (2/4). kezia masuk DB dari round 1 ternyata (gaduh + ivan + ketut juga sudah masuk di round 1 — script log `no_code` tapi exchange tetap jalan sebelum timeout).
   Retry 2: miko OK (1/1). Final: **25/25 (100%)**.
   Total waktu: ~25 menit untuk 25 akun (batch 1) + ~8 menit retry.
9b. **Batch 10 akun kalianda.online (2026-10-01 verified): 10/10 (100%) first run, ~50s/akun, ~9 menit total.**
   Flow yang terlihat di log: `clicked 'i understand'` di `/v3/signin/speedbump/workspacet...` → `clicked 'login'` di `/signin/oauth/v3/firstparty/...` → `EXCHANGE OK` (~10-18s setelah klik). Zero `no_code`, zero CAPTCHA. Menunjukkan: kalau batch sebelumnya gagal banyak `no_code`, batch berikutnya bisa 100% — jangan generalisasi failure rate ke domain baru.
   Command lengkap yang dipakai:
   ```bash
   cd ~ && cp ag_bind_all.py ~/.backup_ag_bind_all_<domain_lama>.py   # backup sebelum ganti list
   # ... regenerate ALL_ACCOUNTS dari ag_new10.txt (pola regex di atas) ...
   rm -f ag_bind_new10.log && setsid nohup python3 ~/ag_bind_all.py > ~/ag_bind_new10.log 2>&1 < /dev/null &
   sleep 30; tail -5 ~/ag_bind_new10.log        # konfirmasi '>>> BINDING:' pertama muncul
   # poll tiap ~4 menit: grep -c 'EXCHANGE OK' + status uniq -c + pgrep
   ```
   Verifikasi akhir (jangan percaya log saja):
   ```bash
   python3 -c "
   import sqlite3,os
   c=sqlite3.connect(os.path.expanduser('~/.9router/db/data.sqlite'))
   rows=[r[0] for r in c.execute('SELECT email FROM providerConnections WHERE provider=\"antigravity\"').fetchall()]
   print('total:',len(rows),'| baru:',len([e for e in rows if '<domain>' in e]))"
   ```
   Catatan: baris `if "paragadis" in em:` di akhir script cuma filter print — setelah ganti domain, hitungan `grep -c paragadis` = 1 itu wajar, bukan sisa akun lama.
10. **DB count after bind:** Verify with `python3 -c "import sqlite3; ..."` query, NOT from script log (log may show `no_code` for accounts that actually got exchanged before the polling loop ended).
11. **Killing stuck batch:** If the script hangs after printing DB summary (tail shows `*` list but process still alive), safe to `kill $(pgrep -f ag_bind_all.py)` — all exchanges already committed to DB.
12a. **Cleanest kill pattern (verified 2026-10-01): `pgrep | xargs kill` in one pass.**
    ```bash
    pgrep -f ag_bind_all.py | xargs -r kill -9; sleep 2; pgrep -f ag_bind_all.py | xargs -r kill -9; sleep 1; pgrep -f ag_bind_all.py | xargs -r kill -9; pgrep -f ag_bind_all.py || echo STOPPED
    ```
    `xargs kill` avoids self-match (the shell's own argv is `pgrep`/`xargs`, not the target string) and the doubled pass catches a respawned child. Then verify the LOG stops growing — `ls -l <log>` twice ~10s apart; an unchanged mtime is the proof, not `pgrep`.
12. **Killing from inside a `run_shell` call is unreliable — the killer dies with the target:**
    `pkill -f ag_bind_all.py` run in a shell whose own command line contains the string `ag_bind_all.py` matches and kills *itself* (exit `-9`/`-15`, no output), and `pgrep` will then keep "finding" the now-dead killer. To stop a batch reliably, loop over the PIDs and kill by number, then confirm after a pause:
    ```bash
    for p in $(pgrep -f ag_bind_all.py); do kill -9 "$p" 2>/dev/null; done
    sleep 2
    for p in $(pgrep -f ag_bind_all.py); do echo "alive: $p"; done; echo "done"
    ```
    Re-run once if a PID survives, and verify the batch is actually gone by checking that the log file's mtime stops changing and `ps` no longer shows the python process — a bare `pgrep -f ag_bind` call *always* matches its own shell, so an apparent "still running" PID with `cat /proc/<pid>/cmdline` returning empty is just the probe, not the job.
13. **Never bind a batch you were not asked to bind.** Re-check the source list, count, and first 3 emails against the operator's message before launching; a wasted 70-minute run on the wrong list is the single most costly mistake in this procedure (see CRITICAL section).

## Post-bind API health check (verifikasi bahwa chat benar-benar jalan, bukan cuma masuk DB)
DB count alone is NOT proof the account can generate. Google can revoke/migrate client tiers days or hours after binding, so a 100% bind can still produce a 0% working chat. After every batch (or when the operator says "akun lama masih oke, kenapa yang baru gak jalan?"), run this 3-endpoint probe on one sample account:

```python
import sqlite3, os, json, urllib.request
c = sqlite3.connect(os.path.expanduser('~/.9router/db/data.sqlite'))
c.row_factory = sqlite3.Row
r = c.execute("SELECT email,data FROM providerConnections WHERE provider='antigravity' AND email LIKE '%<domain>%' LIMIT 1").fetchone()
tok = json.loads(r['data'])['accessToken']
hdr = {'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}

def hit(url, body=None, label=''):
    data = json.dumps(body).encode() if body else b'{}'
    rq = urllib.request.Request(url, data=data, headers=hdr)
    try:
        b = urllib.request.urlopen(rq, timeout=30).read().decode()
        print(f'[{label}] 200 OK ->', b[:200])
    except Exception as e:
        try: m = e.read().decode()[:300]
        except: m = str(e)
        print(f'[{label}] HTTP {getattr(e,"code","?")} ->', m)

BASE = 'https://cloudcode-pa.googleapis.com'
hit(BASE + '/v1internal:loadCodeAssist', {'metadata': {'pluginType': 'GEMINI'}}, 'loadCodeAssist')
hit(BASE + '/v1internal:fetchAvailableModels', {}, 'fetchModels')
hit(BASE + '/v1internal:streamGenerateContent?alt=sse',
    {'model': 'gemini-2.5-flash', 'request': {'contents': [{'role': 'user', 'parts': [{'text': 'Reply: OK'}]}]}},
    'streamGenerateContent')
```

**Reading the probe results (verified 2026-10-01):**
- `loadCodeAssist` 200 → token valid, akun dikenali Google.
- `fetchAvailableModels` 403 `PERMISSION_DENIED` + "must be a named user on your organization's Gemini Code Assist Standard edition" → akun tidak punya akses Standard tier (butuh langganan berbayar/org admin). Chat juga akan 403.
- `streamGenerateContent` 403 → chat tidak jalan. Tidak ada gunanya re-bind akun yang sama; masalahnya di sisi Google tier, bukan binding.
- Jika `loadCodeAssist` mengembalikan `ineligibleTiers` dengan `reasonCode: UNSUPPORTED_CLIENT` dan message "migrate to the Antigravity suite" → Google sudah pensiunkan client Gemini Code Assist for individuals. **Ini bukan bug binding, bukan salah domain, bukan salah akun.** Semua akun (lama+baru) kena hal yang sama kalau client-nya usang. Perbaikan harus di sisi implementasi provider antigravity di 9Router (versi client/endpoint baru), bukan ganti akun.

**Key takeaway:** When the operator reports "akun lama masih oke, yang baru forbidden", do NOT assume domain/account is the cause. Probe the API first — if `loadCodeAssist` 200 but `streamGenerateContent` 403 for ALL accounts (old and new alike), the issue is client deprecation, not the accounts. Tell the operator the real root cause and offer to upgrade 9Router or patch the provider handler.

**Wrong password vs API forbidden — distinguishing in logs:**
When a bind fails with `no_code` and the log shows `enter your password` → `wrong`, that is a **password error** (Google rejected the credential), NOT a binding/API issue. Ask the operator for the correct password. Do NOT confuse this with the `UNSUPPORTED_CLIENT` 403 above, which happens *after* successful binding.
