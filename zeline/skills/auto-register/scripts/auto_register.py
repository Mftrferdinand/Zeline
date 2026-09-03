#!/usr/bin/env python3
"""Auto-register akun untuk signup form generic (prop firm, exchange, dll).

Script reusable untuk situs dengan form signup mirip Meridian Funded:
- Step 1: firstName, lastName, email, password, confirmPassword → Next
- Step 2: phoneNum, city, state, zipCode, street, referrerCode → Create Account

Cara pakai:
    python3 ~/auto_register.py <URL> --ref <KODE> --count <N>
    python3 ~/auto_register.py https://situs.com/auth/sign-up --ref REF123 --count 5
    python3 ~/auto_register.py https://situs.com/auth/sign-up --ref REF123 --count 1 --domain myemail.com

Fitur:
- Temp email otomatis (mail.gw, 5 domain random) atau custom domain
- Email = namadepan + namabelakang + digit (gampang diingat = nama profil)
- Retry 3x per akun jika halaman belum ke-load
- Output: ~/auto_register_accounts.txt (append mode)
- Semua field bisa di-override via flag

Override field (optional):
    --first-name James    (default: random)
    --last-name Carter    (default: random)
    --phone-area 512      (default: 512)
    --city "New York"     (default: random)
    --state NY            (default: random)
    --zip 10001           (default: random)
    --street "123 Main St" (default: random)
    --password MyPass123   (default: random 16 char)
    --domain myemail.com  (default: random mail.gw)
"""

import json, random, string, os, shutil, sys, time, re, argparse
from urllib.request import Request, urlopen
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ─────────── Config ───────────
SIGNIN_URL_DEFAULT = ""  # di-override via --login-url
OUTPUT_FILE = os.path.expanduser(
    os.environ.get("AUTO_REGISTER_OUTPUT", "~/auto_register_accounts.txt")
)

# Chromium/chromedriver dicari lewat PATH (lihat _resolve_browser); override
# dengan CHROME_BIN / CHROMEDRIVER kalau terpasang di lokasi tidak standar.

# mail.gw API
MAIL_GW_API = "https://api.mail.gw"

# ─────────── Data pools ───────────
FIRST_NAMES = [
    "James", "Michael", "David", "Robert", "John", "Daniel", "Thomas",
    "Chris", "Kevin", "Brian", "Jason", "Ryan", "Eric", "Justin",
    "Sarah", "Emily", "Jessica", "Lauren", "Megan", "Rachel", "Anna",
    "Lisa", "Maria", "Sophia", "Olivia", "Hannah", "Grace", "Lily",
]
LAST_NAMES = [
    "Wilson", "Taylor", "Anderson", "Mitchell", "Carter", "Roberts",
    "Phillips", "Campbell", "Stewart", "Reed", "Cook", "Bell", "Bailey",
    "Rivera", "Cooper", "Richardson", "Cox", "Howard", "Ward", "Torres",
]
CITIES = [
    ("New York", "NY", "10001", "123 Main Street"),
    ("Los Angeles", "CA", "90001", "456 Sunset Blvd"),
    ("Chicago", "IL", "60601", "789 Lake Shore Dr"),
    ("Houston", "TX", "77001", "321 Oak Avenue"),
    ("Phoenix", "AZ", "85001", "654 Desert Rd"),
    ("Miami", "FL", "33101", "111 Ocean Dr"),
    ("Seattle", "WA", "98101", "222 Pine St"),
    ("Denver", "CO", "80201", "333 Mountain Ave"),
]


# ─────────── Helpers ───────────

def create_tempmail(first_name, last_name, custom_domain=None):
    """Buat temp email di mail.gw (5 domain) atau pakai custom domain.

    Email format: firstname.lastname##@<domain>
    """
    # Pilih domain
    if custom_domain:
        domain = custom_domain
    else:
        req = Request(f"{MAIL_GW_API}/domains")
        resp = urlopen(req, timeout=10)
        domains = json.loads(resp.read())
        domain = random.choice(domains["hydra:member"])["domain"]

    # Email = firstname + lastname + 2-3 digit random
    suffix = random.randint(10, 999)
    username = f"{first_name.lower()}{last_name.lower()}{suffix}"
    email = f"{username}@{domain}"
    password = "".join(random.choices(string.ascii_letters + string.digits, k=16))

    if custom_domain:
        # Custom domain — tidak bisa poll inbox, return saja
        return {
            "email": email,
            "password": password,
            "mail_token": None,
            "account_id": None,
            "can_poll": False,
        }

    # Buat akun di mail.gw
    data = json.dumps({"address": email, "password": password}).encode()
    req = Request(
        f"{MAIL_GW_API}/accounts",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urlopen(req, timeout=10)
    result = json.loads(resp.read())

    # Ambil mail token
    data = json.dumps({"address": email, "password": password}).encode()
    req = Request(
        f"{MAIL_GW_API}/token",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urlopen(req, timeout=10)
    token = json.loads(resp.read())["token"]

    return {
        "email": email,
        "password": password,
        "mail_token": token,
        "account_id": result["id"],
        "can_poll": True,
    }


def poll_inbox(email, mail_password, max_attempts=8, delay=3):
    """Cek inbox mail.gw, return isi email pertama atau None."""
    for i in range(max_attempts):
        time.sleep(delay)

        # Refresh token
        data = json.dumps({"address": email, "password": mail_password}).encode()
        req = Request(
            f"{MAIL_GW_API}/token",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urlopen(req, timeout=10)
            mail_token = json.loads(resp.read())["token"]
        except Exception:
            continue

        # Cek messages
        req = Request(
            f"{MAIL_GW_API}/messages",
            headers={"Authorization": f"Bearer {mail_token}"},
        )
        try:
            resp = urlopen(req, timeout=10)
            msgs = json.loads(resp.read())
        except Exception:
            continue

        if msgs.get("hydra:totalItems", 0) > 0:
            msg = msgs["hydra:member"][0]

            req2 = Request(
                f"{MAIL_GW_API}/messages/{msg['id']}",
                headers={"Authorization": f"Bearer {mail_token}"},
            )
            resp2 = urlopen(req2, timeout=10)
            full = json.loads(resp2.read())

            return {
                "from": full.get("from", {}).get("address", ""),
                "subject": full.get("subject", ""),
                "text": full.get("text", ""),
            }
    return None


def _resolve_browser() -> tuple[str, str]:
    """Locate a Chromium binary and its driver on THIS machine.

    Hardcoding Termux paths made this script a no-op everywhere else: the
    binary_location simply did not exist, and Selenium raised a
    WebDriverException that read like a Selenium bug rather than a wrong path.
    Order: explicit env override, then PATH, across the names each platform
    actually ships.
    """
    chrome = os.environ.get("CHROME_BIN", "")
    driver = os.environ.get("CHROMEDRIVER", "")
    if not chrome:
        for name in (
            "chromium", "chromium-browser", "chrome", "google-chrome",
            "google-chrome-stable", "chrome.exe",
        ):
            found = shutil.which(name)
            if found:
                chrome = found
                break
    if not driver:
        for name in ("chromedriver", "chromedriver.exe"):
            found = shutil.which(name)
            if found:
                driver = found
                break
    return chrome, driver


def make_driver():
    """Buat instance Chromium headless (lintas platform)."""
    chrome, driver_path = _resolve_browser()
    if not driver_path:
        raise SystemExit(
            "chromedriver tidak ditemukan di PATH.\n"
            "  Install: Termux `pkg install chromium`; Debian/Ubuntu "
            "`apt install chromium-driver`; macOS `brew install chromedriver`.\n"
            "  Atau set CHROMEDRIVER=/path/ke/chromedriver (dan CHROME_BIN bila perlu)."
        )
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    # Let Selenium find the browser itself when it is on PATH under a name it
    # already knows; only override when we resolved something concrete.
    if chrome:
        options.binary_location = chrome
    service = Service(executable_path=driver_path)
    return webdriver.Chrome(service=service, options=options)


def register_account(driver, signup_url, ref_code, args):
    """Daftar 1 akun. Return dict detail akun atau None."""

    # Generate data
    first = args.first_name or random.choice(FIRST_NAMES)
    last = args.last_name or random.choice(LAST_NAMES)
    city_data = random.choice(CITIES)
    city = args.city or city_data[0]
    state = args.state or city_data[1]
    zip_code = args.zip or city_data[2]
    street = args.street or city_data[3]
    phone_area = args.phone_area or "512"
    phone = f"{phone_area}{random.randint(200,999)}{random.randint(1000,9999)}"

    # Buat temp email
    print("  → Membuat temp email...")
    tm = create_tempmail(first, last, args.domain)
    email = tm["email"]
    password = args.password or tm["password"]

    print(f"  → Email : {email}")
    print(f"  → Nama  : {first} {last}")
    print(f"  → Phone : +1 {phone}")
    if ref_code:
        print(f"  → Ref   : {ref_code}")

    try:
        url = signup_url.format(ref=ref_code) if "{ref}" in signup_url else signup_url

        for attempt in range(3):
            try:
                print(f"  → Membuka halaman signup... (attempt {attempt+1})")
                driver.get(url)
                wait = WebDriverWait(driver, 15)

                # ── Step 1: Personal info ──
                print("  → Step 1: Isi data pribadi...")
                wait.until(EC.element_to_be_clickable((By.ID, "firstName"))).send_keys(first)
                driver.find_element(By.ID, "lastName").send_keys(last)
                driver.find_element(By.ID, "email").send_keys(email)
                driver.find_element(By.ID, "password").send_keys(password)
                driver.find_element(By.ID, "confirmPassword").send_keys(password)
                time.sleep(1)

                # Click Next or Submit
                next_btn = driver.find_elements(By.XPATH, "//button[contains(text(),'Next')]") or \
                           driver.find_elements(By.XPATH, "//button[contains(text(),'Continue')]") or \
                           driver.find_elements(By.XPATH, "//button[@type='submit']")
                if next_btn:
                    next_btn[0].click()
                time.sleep(6)

                # ── Step 2: Phone + ref code + address (jika ada) ──
                print("  → Step 2: Isi phone + ref code + alamat...")
                try:
                    wait.until(EC.element_to_be_clickable((By.NAME, "phoneNum")))
                    driver.find_element(By.NAME, "phoneNum").send_keys(phone)
                except Exception:
                    print("     (field phoneNum tidak ditemukan — skip)")

                # Field alamat (optional, tidak semua form punya)
                for field_name, value in [
                    ("city", city), ("state", state),
                    ("zipCode", zip_code), ("zip", zip_code),
                    ("street", street), ("address", street),
                ]:
                    try:
                        el = driver.find_element(By.NAME, field_name)
                        el.clear()
                        el.send_keys(value)
                    except Exception:
                        pass

                # ISI REF CODE jika ada field
                if ref_code:
                    for ref_field_name in ["referrerCode", "refCode", "ref", "referrer", "reference"]:
                        try:
                            ref_field = driver.find_element(By.NAME, ref_field_name)
                            ref_field.clear()
                            ref_field.send_keys(ref_code)
                            print(f"     Ref code ({ref_field_name}): {ref_field.get_attribute('value')}")
                            break
                        except Exception:
                            continue

                # Submit
                print("  → Submit...")
                submit_btn = driver.find_elements(By.XPATH, "//button[contains(text(),'Create')]") or \
                             driver.find_elements(By.XPATH, "//button[contains(text(),'Register')]") or \
                             driver.find_elements(By.XPATH, "//button[contains(text(),'Sign Up')]") or \
                             driver.find_elements(By.XPATH, "//button[contains(text(),'Submit')]") or \
                             driver.find_elements(By.XPATH, "//button[@type='submit']")
                if submit_btn:
                    submit_btn[0].click()
                time.sleep(8)

                # Cek hasil
                body = driver.find_element(By.TAG_NAME, "body").text
                current_url = driver.current_url.lower()

                # Deteksi sukses: redirect ke sign-in/login atau pesan sukses
                success = (
                    "Registration successful" in body or
                    "sign-in" in current_url or
                    "login" in current_url or
                    "successfully" in body.lower() or
                    "check your email" in body.lower() or
                    "verify" in body.lower()
                )

                if success:
                    print("  ✅ Registrasi berhasil!")

                    # Cek email jika bisa poll
                    if tm.get("can_poll"):
                        print("  → Cek email...")
                        email_data = poll_inbox(email, tm["password"])
                        if email_data:
                            print(f"     Email dari: {email_data['from']}")
                            print(f"     Subject   : {email_data['subject']}")

                    account = {
                        "email": email,
                        "password": password,
                        "name": f"{first} {last}",
                        "phone": f"+1 {phone}",
                        "ref_code": ref_code or "",
                        "city": city,
                        "state": state,
                        "zip": zip_code,
                        "street": street,
                        "mail_password": tm["password"] if tm.get("can_poll") else "",
                        "signup_url": signup_url,
                    }

                    # Simpan ke file
                    with open(OUTPUT_FILE, "a") as f:
                        f.write("=" * 60 + "\n")
                        f.write(f"Email    : {email}\n")
                        f.write(f"Password : {password}\n")
                        f.write(f"Name     : {first} {last}\n")
                        f.write(f"Phone    : +1 {phone}\n")
                        f.write(f"Ref Code : {ref_code or '(none)'}\n")
                        f.write(f"Address  : {street}, {city}, {state} {zip_code}\n")
                        f.write(f"Signup   : {signup_url}\n")
                        if tm.get("can_poll"):
                            f.write(f"Mail.gw  : {email} / {tm['password']}\n")
                        f.write(f"Created  : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write("\n")
                    os.chmod(OUTPUT_FILE, 0o600)

                    return account
                else:
                    errors = driver.find_elements(
                        By.CSS_SELECTOR,
                        "[class*='error'], [class*='Error'], [class*='alert'], [role='alert']",
                    )
                    error_msgs = [e.text.strip() for e in errors if e.text.strip()]
                    print(f"  ❌ Gagal: {error_msgs or body[:200]}")
                    if attempt < 2:
                        print("  → Retry dalam 3 detik...")
                        time.sleep(3)
                        continue
                    return None

            except Exception as attempt_err:
                print(f"  ⚠️  Attempt {attempt+1} error: {attempt_err}")
                if attempt < 2:
                    print("  → Retry dalam 3 detik...")
                    time.sleep(3)
                    continue
                raise

    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        driver.save_screenshot(
            os.path.expanduser(f"~/auto_reg_error_{email.split('@')[0]}.png")
        )
        return None


# ─────────── Main ───────────

def main():
    parser = argparse.ArgumentParser(
        description="Auto-register akun untuk signup form generic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", help="URL signup (pakai {ref} placeholder jika perlu)")
    parser.add_argument("--ref", default="", help="Kode referral")
    parser.add_argument("--count", type=int, default=1, help="Jumlah akun (default: 1)")
    parser.add_argument("--domain", help="Custom email domain (default: random mail.gw)")
    parser.add_argument("--first-name", help="Override first name (default: random)")
    parser.add_argument("--last-name", help="Override last name (default: random)")
    parser.add_argument("--phone-area", default="512", help="Phone area code (default: 512)")
    parser.add_argument("--city", help="Override city (default: random)")
    parser.add_argument("--state", help="Override state (default: random)")
    parser.add_argument("--zip", help="Override zip code (default: random)")
    parser.add_argument("--street", help="Override street (default: random)")
    parser.add_argument("--password", help="Override password (default: random 16 char)")

    args = parser.parse_args()

    signup_url = args.url
    ref_code = args.ref
    count = args.count

    print(f"\n{'='*60}")
    print(f"  AUTO REGISTER — Generic Signup")
    print(f"  URL    : {signup_url}")
    print(f"  Ref    : {ref_code or '(none)'}")
    print(f"  Jumlah : {count}")
    print(f"  Output : {OUTPUT_FILE}")
    print(f"{'='*60}\n")

    driver = make_driver()
    results = []

    try:
        for i in range(count):
            print(f"\n--- Akun {i+1}/{count} ---")
            account = register_account(driver, signup_url, ref_code, args)
            if account:
                results.append(account)
                print(f"\n  📧 Email : {account['email']}")
                print("  🔐 Credential saved privately in output file")
                print(f"  👤 Name  : {account['name']}")
                print(f"  📱 Phone : {account['phone']}")
            else:
                print("  ⚠️  Akun gagal")

            if i < count - 1:
                delay = random.randint(3, 7)
                print(f"\n  ⏳ Tunggu {delay} detik...")
                time.sleep(delay)

    finally:
        driver.quit()

    print(f"\n{'='*60}")
    print(f"  SELESAI — {len(results)}/{count} akun berhasil")
    print(f"  Detail disimpan di: {OUTPUT_FILE}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
