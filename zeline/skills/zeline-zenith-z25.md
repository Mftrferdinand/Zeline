# Compliance, CI/CD & Code Migration [Zeline Zenith]

> Compliance, CI/CD & Code Migration — modul Zeline Zenith (sumber: zeline-zenith-z25).

# Load when: compliance, regulasi, GDPR, CI/CD, pipeline, code migration, SOC2, audit regulasi
# Category: Infrastructure & DevOps
Lapisan enterprise-grade buat tim: review kepatuhan/regulasi, enforce brand guideline korporat, pipeline CI/CD, dan porting kode antar bahasa/framework. **Opsional.** Komplemen zeline-zenith-z16 (coding), zeline-zenith-z2 (deploy), zeline-zenith-z21 (enterprise/defensif), zeline-zenith-z20 (brand voice).

> Disclaimer: compliance review di sini = **bantuan teknis & checklist**, bukan nasihat hukum. Untuk keputusan legal final → counsel manusia. Surface risiko jujur, jangan klaim "pasti compliant".

| Capability | Sifat | Output |
|---|---|---|
| Compliance & regulatory review | advisory checklist | gap + rekomendasi |
| Brand-guidelines enforcement | lint corporate identity | pelanggaran + fix |
| Deploy pipeline / CI/CD | otomasi build→test→deploy | workflow file |
| Code-migrator | porting bahasa/framework | kode + catatan risiko |

---

## 1. Compliance & regulatory review

Cek sistematis vs framework relevan (GDPR, SOC2, PCI-DSS, HIPAA, dst) — sesuai konteks operator, bukan semua sekaligus.

```
1. SCOPE     → data apa yang disentuh? (PII, kartu, kesehatan) → framework mana yang berlaku
2. CHECKLIST → mapping kontrol: data retention, consent, enkripsi at-rest/in-transit, akses, audit log
3. GAP       → mana yang belum ada / setengah → ranking by risiko
4. REMEDIATE → rekomendasi konkret + contoh implementasi (komplemen z16)
```

Output: gap list + fix, BUKAN stempel "compliant". Tandai mana yang butuh review hukum manusia.

## 2. Brand-guidelines enforcement (corporate identity)

Lint output/aset vs brand guide (warna, font, logo usage, tone, terminologi). Beda dari zeline-zenith-z20 (humanizer/voice) — ini enforce *aturan korporat eksplisit*:

```python
GUIDE = {"colors":["#0A2540","#635BFF"], "forbidden_terms":["cheap","guys"],
         "required_terms":{"login":"sign in"}, "tone":"professional", "logo_min_px":24}
# scan teks/asset → flag pelanggaran + auto-fix yang aman (terminologi), sisanya report
```

Konsisten lintas semua materi = inti brand. Combo z20 untuk tone, z18 untuk visual token.

## 3. Deploy pipeline & CI/CD

Otomasi build → test → (gate) → deploy. Generate workflow sesuai platform:

```yaml
# .github/workflows/ci.yml — pola umum (sesuaikan)
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: <install deps>
      - run: <lint>
      - run: <test>            # WAJIB hijau sebelum deploy
  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps: [ ... ]             # deploy ke VPS (z2) / registry
```

Prinsip: test gate sebelum deploy (jangan deploy merah), secret via CI secrets bukan hardcode, rollback plan. Deploy ke prod = aksi outward → kalau lewat agent, R9 gate. Combo zeline-zenith-z2 (target VPS), zeline-zenith-z16 (build).

## 4. Code-migrator (porting antar bahasa/framework)

Port kode antar bahasa (Python↔TS↔Go) atau framework (Express→FastAPI, Flask→Django):

```
1. INVENTORY → petakan modul, dependency, fitur bahasa yang gak ada padanan 1:1
2. IDIOM      → port ke IDIOM target, bukan transliterasi (Pythonic ≠ Go-ish)
3. TEST-FIRST → port test dulu / bikin karakterisasi test → jadi jaring regресi (combo z56/z26 TDD)
4. RISK        → tandai bagian yang semantik beda (async model, error handling, tipe, concurrency)
5. INCREMENTAL → migrasi bertahap + jalan paralel, jangan big-bang
```

Yang fatal: porting yang *terlihat* benar tapi semantik beda (mis. integer overflow, timezone, floating point, null vs undefined). Tandai eksplisit, jangan diam.

---

## Catatan
- Compliance = advisory, bukan legal advice. CI/CD prod deploy = R9 gate.
- Migrasi: test dulu (jaring regresi) sebelum sentuh logic.
- Combo: z25 + zeline-zenith-z16 (impl) + zeline-zenith-z2 (deploy) + zeline-zenith-z56 (regression) + zeline-zenith-z20 (brand tone).
