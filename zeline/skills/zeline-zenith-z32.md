# CTF / Whitehat Toolkit [Zeline Zenith]

> CTF / Whitehat Toolkit — modul Zeline Zenith (sumber: zeline-zenith-z32).

# Load when: CTF, capture the flag, whitehat, bug bounty, decode flag, crypto challenge, caesar, xor cipher
# Category: Security

## SCOPE & ETIKA (baca dulu — non-negotiable)
z32 buat **CTF legal & bug-bounty in-scope**: platform latihan (HackTheBox/TryHackMe/CTFd), kompetisi resmi, atau target yang operator punya **izin tertulis**. Tujuannya dapet reward/skill secara whitehat.
- ✅ Boleh: analisis pasif, decode, klasik-crypto, identifikasi, recon target sendiri/in-scope.
- ❌ TIDAK: nyerang sistem tanpa izin, akses data orang lain, jual exploit. Kalau target gak jelas in-scope → tanya operator dulu (R9 gate), jangan asumsi.
Tool inti di sini **stdlib-only & pasif** (encoding/analisis). Eksploitasi aktif (web/pwn) = manual operator + izin.

## TOOL (v4.2, net-new)
`tools/ctf.py` — toolkit serbaguna:
- `find_flags(text)` — ekstrak pola `NAME{...}`.
- `triage(desc)` — tebak kategori (web/pwn/crypto/reverse/forensics/osint) dari deskripsi soal → arahin tooling.
- `try_decode(data)` — kupas multi-layer: base64 / hex / rot13 / url / binary / ascii-decimal, diurut by printability.
- `caesar_bruteforce(text)` — 26 shift sekaligus.
- `xor_single_byte(bytes)` — brute 1-byte XOR, rank by englishness.
- `xor_repeating(data, key)` — repeating-key/Vigenere XOR (enc==dec).
- `identify_hash(h)` — tebak tipe (MD5/SHA-1/256/512, bcrypt, argon2) dari panjang/charset.

## ALUR STANDAR (per kategori)
```python
from ctf import triage, try_decode, find_flags, caesar_bruteforce, xor_single_byte, identify_hash

# 0. Triage: soal ini kategori apa?
triage("RSA modulus nonce encrypt")        # → [('crypto', 4)]

# 1. CRYPTO/MISC — kupas encoding berlapis
for a in try_decode(blob)[:3]:
    print(a.method, a.value)                # cari yang printable & ada flag{...}
find_flags(decoded)

# 2. Classic cipher
caesar_bruteforce(ciphertext)               # baca shift yang masuk akal
xor_single_byte(ct_bytes)                   # top-5 kandidat key

# 3. Hash
identify_hash("5f4dcc3b5aa765d61d8327deb882cf99")   # → ['MD5','NTLM']
```
Recon checklist per kategori:
- **web**: cek header/cookie/JWT, params (IDOR/LFI/SQLi), source map, robots.txt — pakai z11 + httpx, izin in-scope only.
- **pwn/reverse**: `strings`, decompile (manual, host), cari fungsi cek-flag.
- **forensics**: metadata/exif, `binwalk`/carve, pcap (wireshark), stego.
- **crypto**: encoding chain → classic → modern (RSA small-e/common-modulus, AES-ECB cut-paste) — math manual.

## ORCHESTRATOR + SWARM (v4.2, `tools/ctf/`)
z32 = **router CTF**. Buat soal nyata (bukan cuma decode), z32 triage → cek scope → dispatch ke sub-skill kategori → validasi flag → writeup. Sub-skill kategori:

| Sinyal soal | Sub-skill |
|---|---|
| URL / web app / HTTP service | **z43** (ctf-web) |
| ELF/PE + remote `nc host port` | **z44** (ctf-pwn) |
| ELF/PE, "what does it do", no overflow | **z45** (ctf-rev) |
| ciphertext / key / `.pem` / math / encoding | **z46** (ctf-crypto) |
| `.pcap` / memory dump / image / zip / "hidden" | **z47** (ctf-forensics) |
| LLM/AI target, jailbreak, prompt injection, Gandalf/Lakera | **z48** (ctf-prompt-injection) |
| gak jelas | z47 file-id dulu → re-triage |

**Full-auto runtime** (`tools/ctf/`) — poll CTFd, race N model per soal di sandbox Docker, first valid flag wins, HITL/consensus gate sebelum submit:
```bash
cd tools/ctf
cp .env.example .env && cp scope.example.json scope.json   # set host authorized
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
python3 run.py            # poll/solve loop
python3 run.py status     # state per soal
python3 run.py approve    # submit flag yang antri HITL (aksi operator)
```
Helper kunci: `tools/ctf/scope_guard.py` (allowlist — whitehat line, deny menang), `tools/ctf/flag_validator.py` (full-match + anti-placeholder, anti-halusinasi flag), `tools/ctf/rsa_attacks.py` (RSA offline). Detail: `tools/ctf/README.md`.

## SCOPE & DELEGATION
| Butuh | z32 | Delegasi |
|---|---|---|
| Kategori & arah serang | `triage()` | — |
| Decode/crypto klasik/hash-id | `ctf.py` | — |
| Web / pwn / rev / crypto / forensics dalam | dispatch | z43 / z44 / z45 / z46 / z47 |
| LLM red-team / prompt injection (Gandalf) | dispatch | zeline-zenith-z48 (`tools/ctf/gandalf_solver.py`) |
| Full-auto solve banyak soal | — | `tools/ctf/run.py` (coordinator + swarm) |
| Konfirmasi target authorized | — | `tools/ctf/scope_guard.py` |
| Validasi flag (anti-halusinasi) | — | `tools/ctf/flag_validator.py` |
| Audit kerentanan target | recon checklist | zeline-zenith-z11 (audit/exploit review) |
| HTTP recon in-scope | — | zeline-zenith-z6 (httpx) + z11 |
| Otomasi banyak soal/round | — | z12/z30 (batch, rate-limit) |
| Writeup/report reward | — | zeline-zenith-z8 (PDF) / zeline-zenith-z26 (spec) |

## SAFETY RAILS
- Izin = syarat. Out-of-scope/target asing → R9 gate ("⚠️ target ini in-scope & ada izin? Lanjut? y/n"), default tahan.
- Jangan simpan/lecehkan data sensitif yang kebuka saat challenge.
- `secret_tripwire` (v4.2) tetap aktif di output — flag boleh tampil, tapi key/secret nyata yang gak relevan tetap di-redact.

## QUICKSTART
```bash
python3 tools/ctf.py                # demo decode + triage + hash-id
python3 tools/tests/run_tests.sh    # test_ctf hijau (offline)
```
🔧 Upgrade: combo z8 buat auto-generate writeup PDF tiap flag ketemu.
