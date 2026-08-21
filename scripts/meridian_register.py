#!/usr/bin/env python3
"""Daftar akun Meridian Funded otomatis via Chromium headless di Termux.

Usage:
    python3 ~/meridian_register.py              # 1 akun, ref code default
    python3 ~/meridian_register.py 3            # 3 akun
    python3 ~/meridian_register.py 1 REF8DFA425022E8B04  # custom ref code

Output: file ~/meridian_accounts.txt (append mode)
"""

import json, random, string, os, sys, time, re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ─────────── Config ───────────
DEFAULT_REF_CODE = "REF8DFA381425022E8B04"
SIGNUP_URL = "https://trading.meridian-funded.com/auth/sign-up?ref={ref}"
SIGNIN_URL = "https://trading.meridian-funded.com/auth/sign-in"
OUTPUT_FILE = os.path.expanduser("~/meridian_accounts.txt")

# Chromium di Termux
CHROME_BIN = "/data/data/com.termux/files/usr/lib/chromium/chrome"
CHROMEDRIVER = "/data/data/com.termux/files/usr/bin/chromedriver"

# ─────────── Helpers ───────────

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


def create_tempmail(first_name, last_name):
    """Buat akun temp email di mail.gw (5 domain).

    Email format: firstname.lastname##@<random-domain>
    Supaya gampang diingat = sama dengan nama profil akun.
    """
    import urllib.request

    # Ambil domain random dari mail.gw
    req = urllib.request.Request("https://api.mail.gw/domains")
    resp = urllib.request.urlopen(req, timeout=10)
    domains = json.loads(resp.read())
    domain = random.choice(domains["hydra:member"])["domain"]

    # Email = firstname.lastname + 2-3 digit random (anti-collision)
    suffix = random.randint(10, 999)
    username = f"{first_name.lower()}{last_name.lower()}{suffix}"
    email = f"{username}@{domain}"
    password = "".join(random.choices(string.ascii_letters + string.digits, k=16))

    # Buat akun
    data = json.dumps({"address": email, "password": password}).encode()
    req = urllib.request.Request(
        "https://api.mail.gw/accounts",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read())

    # Ambil mail token untuk polling inbox nanti
    data = json.dumps({"address": email, "password": password}).encode()
    req = urllib.request.Request(
        "https://api.mail.gw/token",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=10)
    token = json.loads(resp.read())["token"]

    return {
        "email": email,
        "password": password,
        "mail_token": token,
        "account_id": result["id"],
    }


def poll_inbox(email, mail_password, max_attempts=12, delay=3):
    """Cek inbox mail.gw, return isi email pertama atau None."""
    import urllib.request

    for i in range(max_attempts):
        time.sleep(delay)

        # Refresh token
        data = json.dumps({"address": email, "password": mail_password}).encode()
        req = urllib.request.Request(
            "https://api.mail.gw/token",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            mail_token = json.loads(resp.read())["token"]
        except Exception:
            continue

        # Cek messages
        req = urllib.request.Request(
            "https://api.mail.gw/messages",
            headers={"Authorization": f"Bearer {mail_token}"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            msgs = json.loads(resp.read())
        except Exception:
            continue

        if msgs.get("hydra:totalItems", 0) > 0:
            msg = msgs["hydra:member"][0]

            # Ambil isi lengkap
            req2 = urllib.request.Request(
                f"https://api.mail.gw/messages/{msg['id']}",
                headers={"Authorization": f"Bearer {mail_token}"},
            )
            resp2 = urllib.request.urlopen(req2, timeout=10)
            full = json.loads(resp2.read())

            return {
                "from": full.get("from", {}).get("address", ""),
                "subject": full.get("subject", ""),
                "text": full.get("text", ""),
            }

    return None


def make_driver():
    """Buat instance Chromium headless untuk Termux."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,800")
    options.binary_location = CHROME_BIN
    service = Service(executable_path=CHROMEDRIVER)
    return webdriver.Chrome(service=service, options=options)


def register_account(driver, ref_code):
    """Daftar 1 akun Meridian Funded. Return dict dengan detail akun atau None."""

    # Generate random personal data
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    city_data = random.choice(CITIES)

    # Buat temp email — domain random, username = nama profil
    print("  → Membuat temp email...")
    tm = create_tempmail(first, last)
    email = tm["email"]
    password = tm["password"]
    phone = f"512{random.randint(200,999)}{random.randint(1000,9999)}"

    print(f"  → Email: {email}")
    print(f"  → Nama : {first} {last}")
    print(f"  → Phone: +1 {phone}")
    print(f"  → Ref  : {ref_code}")

    try:
        url = SIGNUP_URL.format(ref=ref_code)

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
                driver.find_element(By.XPATH, "//button[contains(text(),'Next')]").click()
                time.sleep(6)

                # ── Step 2: Phone + ref code + address ──
                print("  → Step 2: Isi phone + ref code + alamat...")
                wait.until(EC.element_to_be_clickable((By.NAME, "phoneNum")))
                driver.find_element(By.NAME, "phoneNum").send_keys(phone)
                driver.find_element(By.NAME, "city").send_keys(city_data[0])
                driver.find_element(By.NAME, "state").send_keys(city_data[1])
                driver.find_element(By.NAME, "zipCode").send_keys(city_data[2])
                driver.find_element(By.NAME, "street").send_keys(city_data[3])

                # ISI REF CODE — WAJIB
                ref_field = driver.find_element(By.NAME, "referrerCode")
                ref_field.clear()
                ref_field.send_keys(ref_code)
                ref_value = ref_field.get_attribute("value")
                print(f"     Ref code: {ref_value}")

                # Submit
                print("  → Submit Create Account...")
                driver.find_element(
                    By.XPATH, "//button[contains(text(),'Create Account')]"
                ).click()
                time.sleep(8)

                # Cek hasil
                body = driver.find_element(By.TAG_NAME, "body").text

                if "Registration successful" in body or "sign-in" in driver.current_url.lower():
                    print("  ✅ Registrasi berhasil!")

                    # Cek email welcome
                    print("  → Cek email...")
                    email_data = poll_inbox(email, password, max_attempts=8, delay=3)
                    if email_data:
                        print(f"     Email dari: {email_data['from']}")
                        print(f"     Subject   : {email_data['subject']}")
                    else:
                        print("     (tidak ada email dalam 24 detik — mungkin tidak perlu verifikasi)")

                    account = {
                        "email": email,
                        "password": password,
                        "name": f"{first} {last}",
                        "phone": f"+1 {phone}",
                        "ref_code": ref_code,
                        "city": city_data[0],
                        "state": city_data[1],
                        "zip": city_data[2],
                        "street": city_data[3],
                        "mailtm_password": password,
                        "login_url": SIGNIN_URL,
                        "status": "registered",
                    }

                    # Simpan ke file (append)
                    with open(OUTPUT_FILE, "a") as f:
                        f.write("=" * 60 + "\n")
                        f.write(f"Email    : {email}\n")
                        f.write(f"Password : {password}\n")
                        f.write(f"Name     : {first} {last}\n")
                        f.write(f"Phone    : +1 {phone}\n")
                        f.write(f"Ref Code : {ref_code}\n")
                        f.write(f"Address  : {city_data[3]}, {city_data[0]}, {city_data[1]} {city_data[2]}\n")
                        f.write(f"Login URL: {SIGNIN_URL}\n")
                        f.write(f"Mail.gw  : {email} / {password} (untuk reset password jika perlu)\n")
                        f.write(f"Status   : Registration successful\n")
                        f.write(f"Created  : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write("\n")
                    os.chmod(OUTPUT_FILE, 0o600)

                    return account

                else:
                    # Cek error message
                    errors = driver.find_elements(
                        By.CSS_SELECTOR,
                        "[class*='error'], [class*='Error'], [class*='alert'], [role='alert']",
                    )
                    error_msgs = [e.text.strip() for e in errors if e.text.strip()]
                    print(f"  ❌ Registrasi gagal: {error_msgs or body[:200]}")
                    # Jangan return None langsung — retry mungkin bisa
                    if attempt < 2:
                        print(f"  → Retry dalam 3 detik...")
                        time.sleep(3)
                        continue
                    return None

            except Exception as attempt_err:
                print(f"  ⚠️  Attempt {attempt+1} error: {attempt_err}")
                if attempt < 2:
                    print(f"  → Retry dalam 3 detik...")
                    time.sleep(3)
                    continue
                raise

    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        driver.save_screenshot(
            os.path.expanduser(f"~/meridian_error_{email.split('@')[0]}.png")
        )
        return None


# ─────────── Main ───────────

def main():
    # Parse args
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    ref_code = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_REF_CODE

    print(f"\n{'='*60}")
    print(f"  MERIDIAN FUNDED — Auto Register")
    print(f"  Jumlah akun : {count}")
    print(f"  Ref code    : {ref_code}")
    print(f"  Output file : {OUTPUT_FILE}")
    print(f"{'='*60}\n")

    driver = make_driver()
    results = []

    try:
        for i in range(count):
            print(f"\n--- Akun {i+1}/{count} ---")
            account = register_account(driver, ref_code)
            if account:
                results.append(account)
                print(f"\n  📧 Email    : {account['email']}")
                print("  🔐 Credential saved privately in output file")
                print(f"  👤 Name     : {account['name']}")
                print(f"  📱 Phone    : {account['phone']}")
                print(f"  🏠 Address  : {account['street']}, {account['city']}, {account['state']} {account['zip']}")
            else:
                print("  ⚠️  Akun gagal — lihat error di atas")

            # Delay random antar akun
            if i < count - 1:
                delay = random.randint(3, 7)
                print(f"\n  ⏳ Tunggu {delay} detik...")
                time.sleep(delay)

    finally:
        driver.quit()

    # Summary
    print(f"\n{'='*60}")
    print(f"  SELESAI — {len(results)}/{count} akun berhasil")
    print(f"  Detail disimpan di: {OUTPUT_FILE}")
    print(f"{'='*60}\n")

    if results:
        print("Detail akun:\n")
        for acc in results:
            print(f"  Email : {acc['email']}")
            print("  Credential: saved privately in output file")
            print(f"  Name  : {acc['name']}")
            print(f"  Phone : {acc['phone']}")
            print(f"  Login : {acc['login_url']}")
            print()


if __name__ == "__main__":
    main()
