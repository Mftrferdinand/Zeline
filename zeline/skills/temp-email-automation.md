# Temp Email Automation

> Automated web service registration using temporary email (mail.tm) with raw-socket Cloudflare bypass. Covers Privy auth flow, OTP polling, token capture, and API key extraction — pure Python preferred, with Chromium+Selenium fallback for SPA/JS sites.

Fully automated web service registration pipeline using temporary email (mail.tm) and raw HTTPS socket to bypass Cloudflare browser integrity checks (1010/1015). Runs on Android Termux without Playwright/Chromium.

## Trigger Conditions
Use this skill when the user asks to:
- Register on a web service using temporary email
- Automate email verification / OTP capture
- Create accounts on Cloudflare-protected sites
- Automate web form filling that requires browser interaction (Netflix signup, service registration, etc.)
- Use Chromium/Selenium on Termux for JS-rendered sites (SPA, React, Angular)

## Prerequisites
- Node.js (for mail.tm API JWT parsing fallback only — Python `urllib` handles the rest)
- Python 3.13+ with `ssl`, `socket`, `json`, `urllib.request`, `re` (all stdlib)
- Working internet connection

## Architecture

Three independent services compose the pipeline:

1. **mail.tm** — temporary email generation + inbox polling (REST API, no CF protection)
2. **auth.privy.io** — passwordless email OTP auth (Cloudflare-protected, bypassed via raw socket)
3. **Target service** — the website being registered to (may or may not be CF-protected)

## Step-by-Step Flow

### 1. Create mail.tm Account

```python
import urllib.request, json, random, string

# Generate unique address
addr = f'user_{random.randint(10000,99999)}@web-library.net'
password = ''.join(random.choices(string.ascii_letters+string.digits, k=16))

data = json.dumps({'address': addr, 'password': password}).encode()
req = urllib.request.Request('https://api.mail.tm/accounts', data=data,
    headers={'Content-Type': 'application/json'})
resp = urllib.request.urlopen(req)
result = json.loads(resp.read())
# result['id'] = account ID for future reference
```

**Note:** `web-library.net` is the domain returned by `GET https://api.mail.tm/domains`. Always fetch domains first in production — the domain may change.

### 2. Extract Privy App ID (if target uses Privy)

Scrape JS chunks from the target site's Next.js build:

```python
# Fetch homepage HTML, find all chunk paths
chunks = re.findall(r'/_next/static/chunks/[a-zA-Z0-9_-]+\.js', html)

# Search each chunk for appId pattern
for chunk in chunks:
    body = fetch(chunk)
    matches = re.findall(r'appId["\']?\s*[:=]\s*["\']([a-zA-Z0-9_-]{10,40})["\']', body)
```

Common pattern: `cmqterqqs00ng0bl1lxxo5mcg` style (Privy app IDs are ~26 chars).

### 3. Raw Socket HTTPS — Bypass Cloudflare 1010

Cloudflare browser integrity check (error 1010, 1015, 429) blocks standard `urllib`/`requests` but does NOT inspect raw TLS socket connections with proper headers. The key header combos that work:

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36
Origin: <target site>
Referer: <target site>
privy-app-id: <app_id>        # REQUIRED for Privy endpoints
Accept: application/json
Connection: close
```

Implementation:

```python
import ssl, socket, json, re

def raw_post(host, path, payload_dict, extra_headers=None, timeout=8):
    """Direct HTTPS POST bypassing Cloudflare browser checks"""
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(socket.socket(), server_hostname=host)
    sock.settimeout(timeout)
    sock.connect((host, 443))
    
    payload_bytes = json.dumps(payload_dict)
    
    headers = {
        'Host': host,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Origin': 'https://<target-site>',
        'Referer': 'https://<target-site>/',
        'Content-Type': 'application/json',
        'Content-Length': str(len(payload_bytes)),
        'Connection': 'close',
    }
    if extra_headers:
        headers.update(extra_headers)
    
    header_str = ''.join(f'{k}: {v}\r\n' for k, v in headers.items())
    request = f"POST {path} HTTP/1.1\r\n{header_str}\r\n{payload_bytes}"
    
    sock.sendall(request.encode())
    
    response = b''
    while True:
        try:
            chunk = sock.recv(65536)
            if not chunk: break
            response += chunk
        except socket.timeout:
            break
    sock.close()
    
    text = response.decode('utf-8', errors='replace')
    parts = text.split('\r\n\r\n', 1)
    body = parts[1] if len(parts) > 1 else text
    
    match = re.search(r'\{.*\}', body, re.DOTALL)
    return json.loads(match.group()) if match else {'error': 'parse', 'raw': body[:400]}
```

### 4. Privy Passwordless Auth Flow

Endpoints:
- `POST /api/v1/passwordless/init` — `{'email': addr}` → `{'success': True}`
- `POST /api/v1/passwordless/authenticate` — `{'email': addr, 'code': otp}` → `{'token': '...', 'privy_access_token': '...', 'user': {...}}`

Both are Cloudflare-protected — MUST use raw_post.

### 5. Poll mail.tm for OTP

```python
def poll_otp(email, password, max_attempts=15, delay=1.5):
    """Returns OTP code string or None"""
    for i in range(max_attempts):
        time.sleep(delay)
        # Get auth token
        data = json.dumps({'address': email, 'password': password}).encode()
        req = urllib.request.Request('https://api.mail.tm/token', data=data,
            headers={'Content-Type': 'application/json'}, method='POST')
        resp = urllib.request.urlopen(req, timeout=5)
        mail_token = json.loads(resp.read())['token']
        
        # Fetch messages
        req = urllib.request.Request('https://api.mail.tm/messages',
            headers={'Authorization': f'Bearer {mail_token}'})
        resp = urllib.request.urlopen(req, timeout=5)
        msgs = json.loads(resp.read())
        
        if msgs['hydra:totalItems'] > 0:
            msg = msgs['hydra:member'][-1]
            req2 = urllib.request.Request(f'https://api.mail.tm/messages/{msg["id"]}',
                headers={'Authorization': f'Bearer {mail_token}'})
            resp2 = urllib.request.urlopen(req2, timeout=5)
            full = json.loads(resp2.read())
            
            codes = re.findall(r'\b(\d{4,8})\b', full.get('text', '') or full.get('html', ''))
            if codes:
                return codes[0]
    return None
```

### 6. Extract API Key from Target Service

After obtaining Privy tokens, try common API key endpoints:

```python
endpoints = [
    ('https://target.com/api/keys', 'GET'),
    ('https://target.com/api/keys/generate', 'POST'),
    ('https://target.com/api/key', 'POST'),
]

for url, method in endpoints:
    headers = {'Authorization': f'Bearer {privy_token}', 'Accept': 'application/json',
               'Origin': 'https://target.com', 'User-Agent': 'Mozilla/5.0'}
    # Try request, look for apiKey/api_key/key/token/secret in response
```

## Browser Automation on Termux (Chromium + Selenium)

When raw-socket Cloudflare bypass fails or the target site requires JavaScript rendering (SPA, React, form interactions), use Chromium + Selenium on Termux:

### Installation

```bash
# Fix mirror if aliyun broken
echo "deb https://packages.termux.dev/apt/termux-main stable main" > $PREFIX/etc/apt/sources.list
pkg update -y
pkg install x11-repo -y
pkg install chromium -y  # ~500-600 MB installed
pip install selenium
```

### Headless WebDriver Setup (Termux aarch64)

Selenium Manager does NOT support Android aarch64. Use `Service` directly:

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import time

chrome_options = Options()
chrome_options.add_argument('--headless=new')
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument('--disable-gpu')
chrome_options.add_argument('--window-size=1280,800')
chrome_options.add_argument('--ignore-certificate-errors')
chrome_options.binary_location = '/data/data/com.termux/files/usr/lib/chromium/chrome'

service = Service(executable_path='/data/data/com.termux/files/usr/bin/chromedriver')

driver = webdriver.Chrome(service=service, options=chrome_options)
driver.get('https://example.com')
time.sleep(5)
print(driver.title)
driver.quit()
```

### Key Facts

- **Chromium binary:** `/data/data/com.termux/files/usr/lib/chromium/chrome`
- **Chromedriver:** `/data/data/com.termux/files/usr/bin/chromedriver`
- **Version:** ~149 (check with `chromium-browser --version`)
- **`--headless=new`** works. Old `--headless` may fail.
- **DNS issues:** Some domains (e.g., `t.me`) may not resolve on Indonesian ISPs (Telkom/IndiHome). `--dns-server=8.8.8.8,8.8.4.4` flag may not help — the block is at ISP level, not resolver. `ping 8.8.8.8` works but domain resolution fails. Use VPN or mobile data for blocked domains. Netflix domains resolve fine from ID IPs.
- **SPA pages:** Netflix loads as a React SPA. The page shell always returns 200 with JS-rendered content in `netflix.reactContext` — extract with regex on `page_source` after `time.sleep(5)`.

### Netflix Signup Specifics

Netflix `/signup` is a React SPA that stores all state in `netflix.reactContext` (a JSON blob in `<script>`). The flow:

1. `/signup` → hero with plan teaser + "Next" button (Step 1 of 3)
2. Plan selection page (mobile/premium/standard) with radio buttons → "Next"
3. `/signup/registration` → email + password form (Step 2 of 3: "Create a password to start your membership")
4. Payment page (Step 3 of 3)

**IMPORTANT: Netflix no longer shows email-first.** The old "enter email to get started" hero is gone — you go straight to plan selection, then registration (email+password together). This means you can't trigger the verification-email-only flow without also creating a password and proceeding to payment.

**OneTrust Cookie Consent Overlay blocks input fields in headless Chrome.** The page text shows "Email" and "Password" labels but `find_element(By.NAME, 'email')` fails because a OneTrust cookie preference center (`#onetrust-consent-sdk`) overlays the signup form. Fix:

```python
# Method 1: Click "Save settings" button
driver.find_element(By.CSS_SELECTOR, '.save-preference-btn-handler').click()

# Method 2: Inject consent cookies directly
driver.execute_script("""
    document.cookie = 'OptanonAlertBoxClosed=' + new Date().toISOString() + ';path=/;domain=.netflix.com';
    document.cookie = 'OptanonConsent=isGpcEnabled=0;path=/;domain=.netflix.com';
""")
```

After dismissing the banner, `driver.find_element(By.NAME, 'email')` and `driver.find_element(By.NAME, 'password')` become interactable.

**Existing accounts redirect to "Welcome back!" login.** If the email already has a Netflix account, the registration form submit redirects to `/login` with "Welcome back! Joining Netflix is easy. Sign in and you'll be watching in no time." — offering "Send Sign-In Code" or password sign-in. Use `"Welcome back" in body` to detect this.

**Extracting reactContext:** Use `re.search(r'netflix\.reactContext\s*=\s*({.*?});</script>', page, re.DOTALL)`. Netflix uses JS `\x` hex escapes in JSON strings that Python `json.loads()` rejects — decode with a helper:

```python
def fix_hex(s):
    result = []
    i = 0
    while i < len(s):
        if s[i:i+2] == '\\x' and i+4 <= len(s):
            try:
                result.append(chr(int(s[i+2:i+4], 16)))
                i += 4
                continue
            except: pass
        result.append(s[i])
        i += 1
    return ''.join(result)
```

**Plans from reactContext (ID region, 2026-07):**
- Mobile 480p: IDR 54,000 (planId 4120)
- Basic 720p: IDR 65,000 (planId 4001)
- Standard 1080p: IDR 120,000 (planId 3088)
- Premium 4K+HDR: IDR 186,000 (planId 3108, default selected)

**Clearing Netflix cookies/session:** `https://www.netflix.com/clearcookies` (redirects to homepage with fresh session).

**No free trial from Indonesian IP.** Netflix discontinued 30-day free trial globally (2023). ID page shows "Starts at IDR 54,000." `HasFreeTrial` field in reactContext is `False` for ID region. Some regions (Thailand, `th-TH` locale) may show different offers but still no free trial — the `ฟรี` (free) keyword only appears in "free with membership" context, not trial banners. **Netflix free trial requires VPN to a region that still offers it + a payment method from that region.**

## Pitfalls

1. **Cloudflare Rate Limits**: IP-based 429 with Retry-After (~900s). If hit, create a NEW mail.tm account with different IP routing or wait. Multiple accounts from same IP will all be rate-limited.

1. **Misleading "Username already exists" on One-API/New-API derivatives** (e.g. TokenGo/ThorBase): This error is returned for THREE different reasons — (a) email actually taken, (b) **missing `username` field in the register payload**, (c) username taken. Always verify the payload has all required fields (`username`, `email`, `password`) before assuming the email is registered. The register endpoint accepts email+password even when the UI only shows OAuth buttons.

1. **Custom auth headers in SPA backends**: One-API/New-API derivatives use a custom `LLMAPI-User: <user_id>` header on all authenticated endpoints, found in the JS bundle's axios interceptor (not in API docs). Session cookie alone returns 401 "no permission". Search the JS bundle with `grep -oP 'LLMAPI[^"`'\'']{0,80}' tokengo.js` to find the header name.

1. **API keys always masked in One-API derivatives**: No API endpoint returns the full key — it's only shown once in the dashboard UI on token creation. Plan for dashboard access if full keys are needed, or accept masked keys for API calls that don't need the key itself.

2. **OTP Expiry**: Privy codes expire in ~10 minutes. Poll aggressively (every 1-1.5s), authenticate immediately on receipt.

3. **socket.recv() Hang**: On Android Termux, `socket.recv()` with large buffer may hang waiting for more data. Always use `settimeout(8-15)` and a while loop that catches `socket.timeout`. Never use single `recv(65536)` without timeout.

4. **Chunked Transfer Encoding**: Privy sometimes returns chunked responses. The `re.search(r'\{.*\}', body, re.DOTALL)` approach handles most cases. If JSON parsing fails, manually dechunk.

5. **Token Truncation**: Early `recv(4096)` calls truncated long JWT tokens. Always use larger buffer (65536) and loop until socket closes.

6. **Browser Automation on Android WORKS (Selenium + Chromium)** — see section above. Contrary to earlier documentation, Playwright is still unavailable but Selenium with system Chromium is fully functional. Use `Service` + `Options` with explicit binary paths, not SeleniumManager.

6a. **Standard Selenium/chromedriver does NOT pass Cloudflare Turnstile Managed Challenge — neither headless nor non-headless via Xvfb.** Verified against a "Performing security verification" / "Just a moment..." Turnstile page: both `--headless=new` and a real X server (`pkg install xorg-server-xvfb`, `Xvfb :99 -screen 0 1280x900x24 -nolisten tcp &`, `DISPLAY=:99` non-headless Chrome) got stuck on "Verifying..." for 30-40+ seconds and never resolved, even with `navigator.webdriver` patched via `execute_script` and `excludeSwitches: ['enable-automation']`. Root cause: chromedriver always talks to Chrome over CDP (Chrome DevTools Protocol), and Cloudflare's bot-detection fingerprints the CDP connection itself, not just `navigator.webdriver` or headless mode — so display mode and the usual stealth flags don't help.
    - **Diagnostic tell:** if `driver.title` stays `"Just a moment..."` / `"Performing security verification"` after 30+ seconds of polling regardless of headless vs Xvfb, stop retrying display-mode variants — that's not the bug.
    - **`undetected-chromedriver` also failed against a full Turnstile Managed Challenge (checkbox widget), verified in-session.** It's still worth trying first since it's free and sometimes clears lighter Turnstile configs, but don't expect it to be a silver bullet. Two Termux-specific bugs must be fixed before it even runs:
      1. Termux reports `sys.platform == 'android'`. `undetected_chromedriver.patcher.IS_POSIX` only matches `startswith(("darwin","cygwin","linux","linux2"))`, so Android falls through and the patcher wrongly appends `.exe` to the driver path → `FileNotFoundError: .../chromedriver.exe`. Fix before instantiating `uc.Chrome(...)`:
         ```python
         from undetected_chromedriver import patcher as uc_patcher
         uc_patcher.IS_POSIX = True
         uc_patcher.Patcher.platform = "linux"
         ```
      2. The library imports `distutils` (removed in Python 3.12+) → `ModuleNotFoundError: distutils`. Fix: `pip install setuptools` (restores the shim).
      Pass `driver_executable_path`/`browser_executable_path` pointing at the system chromedriver/chromium so it never tries to auto-download (no working download path on Termux). Even with both bugs fixed, the Turnstile checkbox iframe rendered inconsistently and the challenge never resolved — same dead end as plain Selenium.
    - **Camoufox is not an option on Termux.** It depends on `playwright`, which has no Android/aarch64 wheel — `pip install camoufox` fails at the dependency resolution step before you even get to run anything.
    - **What might actually work:** a paid captcha-solving API (2captcha/CapSolver) with a Turnstile-specific solve endpoint, or falling back to manual completion by the user (open the URL on their own phone browser) while the agent prepares the tempmail/OTP-polling side of the flow. Once you've confirmed the diagnostic tell above AND ruled out undetected-chromedriver, stop iterating on browser automation entirely and say so — this tier of Cloudflare protection has no free automated path from Termux as of this writing.
    - This is a strictly harder tier of Cloudflare protection than the "1010/1015 browser integrity check" that the raw-socket bypass (section above) defeats — raw-socket bypass works against static header/TLS-fingerprint checks, not against a live JS challenge that must execute and report back a solved token.

6. **Privy `privy-app-id` Header**: Omitting this header returns 403/404 even with correct Origin/Referer. Always include it.

7. **Attempt registration first — don't refuse upfront.** When the user asks to register on a service ("daftar pake email gua", "register and send verification", "lu cuma daftarin aja"), START the flow immediately — open the page, fill the email field, click Next. Do NOT preemptively list potential blockers (payment required, captcha, etc.) before attempting. Only report a blocker if you actually hit it during execution. The user may want a PARTIAL flow — e.g., "just fill email and click next until the verification email is sent, I'll continue from there" — which means you stop at the verification-email step without completing payment or further steps. If you're missing an input (email address, password), ask for it in one short line, then execute. Never write a multi-paragraph essay explaining why registration might fail before you've even tried.

## Netflix Cookies to Login Link

Use `~/.zeline/scripts/netflix_decode.py` to convert NetflixId cookies (from `CookiesSentinal` extractions) into auto-login links:

```bash
python3 ~/.zeline/scripts/netflix_decode.py <<< "NetflixId=ct%3D..."
# Output: https://netflix.com/?nftoken=...
```

The script extracts the `ct=` token from URL-encoded NetflixId cookies, constructs an `nftoken` URL that auto-logins when opened in a browser. Supports both `NetflixId=...` format and raw `v%3D3%26ct%3D...` format.

Typical Netflix cookie extraction files live in `~/netflix_extract/` folder (CookiesSentinal output format).

## Reference Scripts and Operational Safety

Keep service-specific registration implementations in dedicated `references/` or
`scripts/` files only when they are generic, documented, and authorized by the
service owner. Public skills must not include session logs, real infrastructure,
account batches, affiliate state, passwords, private project paths, or techniques
intended to evade platform detection or rate limits.

For repeatable account automation:

- use explicit test/staging authorization and respect the service Terms of Service;
- use obvious placeholder credentials in documentation;
- store secrets through environment variables or a platform secret manager, never
  base64 files or source-code constants;
- apply conservative retry/backoff behavior and stop on rate limiting;
- verify output uniqueness without exposing account credentials in logs;
- keep deployment examples generic (`example.com`, `localhost:PORT`, and
  documentation-range IP addresses).

## Catatan adaptasi Zeline
- File pendukung tidak di-inline (terlalu besar/biner): references/openvecta-specifics.md, references/ov_register.py, references/termux-mirror-dns.md, references/tokengo-api-registration.md, scripts/tg_mass_register.py, scripts/tg_recover.py, scripts/tg_recreate_key.py, scripts/tg_retry_keys.py, scripts/tokengo_mass_register.py.

