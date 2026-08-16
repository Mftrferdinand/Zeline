---
name: auto-register
description: |
  Daftar akun otomatis untuk signup form generic (prop firm, exchange,
  exchange, dll) via Chromium headless di Termux. Form 2-step
  (nama/email/password → phone/alamat/ref code) atau 1-step.
  Temp email mail.gw (5 domain random) atau custom domain.
  Email = namadepan + namabelakang + digit (mudah diingat = nama profil).
  Load saat user minta daftar akun massal di situs dengan form signup
  serupa Meridian Funded, prop firm lain, atau exchange crypto.
metadata:
  zeline:
    tags: [automation, signup, register, tempmail, prop-firm, chromium]
    category: automation
---

# Auto-register — daftar akun otomatis untuk signup form generic

Script ini mendaftarkan akun secara otomatis untuk situs dengan form signup
yang punya field: firstName, lastName, email, password, confirmPassword,
phoneNum, city, state, zipCode, street, referrerCode (atau varian nama
field seperti refCode, ref, reference).

## Prasyarat

- Chromium + chromedriver terinstall di Termux
- Python 3.10+ dengan selenium
- Path: `chromedriver` di `/data/data/com.termux/files/usr/bin/chromedriver`

## Script

### 1. Generic (reusable untuk any signup form)

**Lokasi:** `scripts/auto_register.py`

```bash
# 1 akun, tanpa ref
python3 scripts/auto_register.py https://situs.com/auth/sign-up

# 5 akun + ref code
python3 scripts/auto_register.py https://situs.com/auth/sign-up --ref REF123 --count 5

# URL dengan {ref} placeholder
python3 scripts/auto_register.py "https://situs.com/signup?ref={ref}" --ref REF123 --count 3

# Custom domain email
python3 scripts/auto_register.py https://situs.com/signup --ref REF123 --domain gmail.com

# Override field
python3 scripts/auto_register.py https://situs.com/signup --ref REF123 \
  --first-name John --last-name Doe --phone-area 512
```

### 2. Meridian Funded (hardcoded URL + ref)

**Lokasi:** `scripts/meridian_register.py`

```bash
python3 scripts/meridian_register.py 5              # 5 akun, ref default
python3 scripts/meridian_register.py 1 REF8DFA3814  # 1 akun, custom ref
```

## Field yang didukung

| Step | Field name dicari | Override flag |
|------|-------------------|---------------|
| 1 | firstName | --first-name |
| 1 | lastName | --last-name |
| 1 | email | (auto dari mail.gw) |
| 1 | password | --password |
| 1 | confirmPassword | (sama dengan password) |
| 2 | phoneNum | --phone-area |
| 2 | city | --city |
| 2 | state | --state |
| 2 | zipCode, zip | --zip |
| 2 | street, address | --street |
| 2 | referrerCode, refCode, ref, referrer, reference | --ref |

## Tombol yang dicari

- Step 1: `Next` → `Continue` → `submit`
- Step 2: `Create` → `Register` → `Sign Up` → `Submit` → `submit`

## Deteksi sukses

- Redirect ke URL mengandung `sign-in` atau `login`
- Body mengandung "Registration successful" / "successfully" / "verify" / "check your email"

## Output

Semua akun disimpan ke `~/auto_register_accounts.txt` (generic) atau
`~/meridian_accounts.txt` (meridian), format append:

```
============================================================
Email    : hannahbailey843@pastryofistanbul.com
Password : UEYcbrXbmay2mDNv
Name     : Hannah Bailey
Phone    : +1 2014596904
Ref Code : REF8DFA381425022E8B04
Address  : 222 Pine St, Seattle, WA 98101
Mail.gw  : hannahbailey843@pastryofistanbul.com / UEYcbrXbmay2mDNv
Created  : 2026-08-16 19:20:00
```

## Alur kerja

1. Pastikan Chromium ready: `chromium --version && chromedriver --version`
2. Jalankan script dengan URL signup + ref code
3. Tunggu sampai selesai (±30 detik per akun)
4. Akun tersimpan di file txt — kasih detail ke user
5. Temp email (mail.gw) bisa di-poll ulang kalau perlu reset password

## Catatan

- **Retry 3x** per akun jika halaman belum ke-load (`element not interactable`)
- **WebDriverWait** 15 detik — tunggu form siap, bukan sleep kaku
- **mail.gw** punya 5 domain: oakon.com, teihu.com, raleigh-construction.com,
  pastryofistanbul.com, questtechsystems.com
- **Phone area code** default 512 (Texas) — lebih sering diterima validasi
- Jangan daftar terlalu cepat beruntun — delay 3-7 detik random antar akun
- Script ini hanya untuk situs yang **tidak ada CAPTCHA** — kalau ada Cloudflare/
  reCAPTCHA/Turnstile, perlu skill `captcha-solving-2captcha` atau browser interaktif

## Risiko & batasan

- Prop firm/exchange banyak yang deteksi multi-akun via IP, device fingerprint,
  atau phone number prefix. Gunakan dengan kesadaran risiko banned.
- Temp email bisa expire — simpan password dengan aman.
- Beberapa situs butuh SMS OTP — mail.gw tidak melayani SMS. Perlu layanan
  SMS virtual (sms-activate, 5sim) jika verifikasi SMS diwajibkan.
