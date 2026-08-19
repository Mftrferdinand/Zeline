# Bug Bounty Copilot

> Reusable skill untuk bug bounty yang SAH/berizin. Workflow awareness, finding normalization, evidence organization, severity (CVSS), report drafting + quality gates, knowledge base mapping. Untuk scope review, triage, dan laporan — bukan eksekusi eksploitasi.

Skill pendamping untuk pekerjaan bug bounty **yang memiliki otorisasi**. Menambahkan domain knowledge, workflow awareness, dan report standards — TIDAK menduplikasi kemampuan native Zeline (planning, shell, browser, sub-agent, dll).

## Aktivasi

Aktif saat user minta:
- Analisis hasil pengujian yang sah (recon/enumeration/finding)
- Penyusunan atau review laporan bug bounty
- Pengelolaan knowledge base / mapping temuan ke standar keamanan

## Aturan Dasar

1. **Hanya untuk program berizin** — cek scope program (URL, wildcard, in-scope assets) sebelum bantu analisis. Di luar scope = tolak.
2. **Tidak menjalankan eksploitasi** — analisis data yang sudah ada, bukan eksekusi serangan.
3. **Non-destruktif** — tidak ada aksi yang mengubah state target (write/delete/brute-force besar) tanpa persetujuan eksplisit dan izin program.
4. Kalau ragu otorisasi → tanya dulu, jangan lanjut.

## Workflow (10 Phase)

1. **Scope Review** — catat aset in-scope/out-of-scope, rules of engagement, reward table, program policy.
2. **Documentation Review** — baca dokumentasi resmi program, API docs, changelog, announcement.
3. **Recon Result Organization** — kelompokkan data mentah per aset (subdomain, endpoint, parameter, teknologi).
4. **Enumeration Result Analysis** — klasifikasi hasil: exposed info, misconfig, logic issue, injection surface.
5. **Finding Triage** — gabungkan duplikat, buang noise, tandai yang butuh verifikasi.
6. **Evidence Collection** — kumpulkan bukti reproduksi: request/response, screenshot, payload, timeline.
7. **Severity Assessment** — skor CVSS v3.1/v4.0, sesuaikan dampak bisnis program (bukan hanya teknis).
8. **Report Drafting** — susun laporan per template standar (lihat Report Template).
9. **Report Review** — jalankan Quality Gates sebelum kirim.
10. **Knowledge Base Update** — simpan pola temuan, mapping CWE/CAPEC, lessons learned.

## Decision Rules

- Kelompokkan data mentah berdasarkan **aset** lalu **jenis temuan** (direktori per aset di knowledge base).
- Hubungkan tiap temuan dengan **standar keamanan** (OWASP/CWE/CAPEC/MITRE) — wajib minimal 1 referensi.
- Bukti belum cukup → tandai **`Perlu Verifikasi`**, jangan di-drop.
- Duplikasi → gabung, simpan representasi paling lengkap.
- Normalisasi istilah & struktur laporan: nama parameter, endpoint, terminologi konsisten antar laporan.

## Severity (CVSS)

- Hitung CVSS v3.1 (atau v4.0 kalau program minta) pakai vector yang jujur.
- **Sesuaikan dengan dampak bisnis program**: data sensitif apa yang terekspos, akun apa yang bisa diambil alih, komponen apa yang terpengaruh.
- CWE yang umum untuk bug bounty: CWE-79 (XSS), CWE-89 (SQLi), CWE-284 (access control), CWE-352 (CSRF), CWE-200 (info exposure), CWE-918 (SSRF), CWE-22 (path traversal), CWE-611 (XXE), CWE-502 (deserialization).
- CAPEC untuk attack patterns, MITRE ATT&CK untuk konteks post-exploitation/kill chain.

## Report Template

```markdown
## Ringkasan
- **Title:** <ringkas, aksi + dampak: "IDOR di GET /api/orders memungkinkan akses order user lain">
- **Severity:** Critical/High/Medium/Low (CVSS X.X)
- **Asset:** <URL/endpoint in-scope>
- **CWE:** CWE-XXX | **CAPEC:** CAPEC-XXX

## Deskripsi
<jelas, kalimat pendek. Apa bug-nya, kenapa terjadi>

## Langkah Reproduksi
1. <langkah konkret>
2. <langkah konkret>
3. <langkah konkret>

## Bukti
- Request (method, URL, headers, body)
- Response yang relevan
- Screenshot/log kalau ada (lampiran)

## Dampak
<apa yang bisa dilakukan attacker>

## Remediasi
<fix yang disarankan, rujuk referensi>
```

## Quality Gates (sebelum kirim)

- [ ] Tidak ada duplikat dengan laporan lain
- [ ] Terminologi konsisten (cek: parameter, endpoint, istilah)
- [ ] Markdown valid (tabel, code block, heading)
- [ ] Referensi lengkap: minimal 1 (OWASP/CWE/CAPEC/vendor doc)
- [ ] Bukti konsisten dengan kesimpulan — severity tidak over/under-claim
- [ ] Reproduksi bisa diikuti langkah per langkah
- [ ] Tidak ada info sensitif berlebih (token/password asli → redact)

## Knowledge Base Structure

```
~/.zeline/bugbounty/
├── programs/<program>/scope.md      # aset in/out, policy, kontak
├── programs/<program>/findings/     # 1 file per temuan (template di atas)
├── kb/patterns.md                   # pola temuan berulang (signature request, payload)
├── kb/cwe-mappings.md               # maping temuan → CWE/CAPEC
└── kb/lessons.md                    # lessons learned tiap program
```

Per-aset organisasi: `recon/<asset>/` untuk data mentah, `findings/` untuk hasil normalisasi.

## Extensibility (opsional, tambah saat dibutuhkan)

- `cloud_security` — misconfig cloud (S3, IAM, AWS/Azure/GCP)
- `mobile_security` — analisis APK/IPA statis
- `api_security` — fokus API (authN/authZ, rate limit, mass assignment)
- `code_review` — review source code untuk vuln
- `threat_modeling` — STRIDE/DREAD di awal engagement
- `compliance_mapping` — map temuan ke PCI-DSS/ISO 27001/SOC2

## Deep-Dive Reference Files (authorized scope only)

Muat sesuai fase — jangan load semua sekaligus. Semua materi ini **hanya untuk aset yang kamu miliki atau yang eksplisit in-scope** di program berizin. Cek scope dulu; di luar scope = stop.

- `references/recon.md` — metodologi recon: passive → active → prioritization, attack-surface mapping
- `references/vuln-classes.md` — kelas kerentanan Web/API: deteksi → eksploitasi → fix (IDOR, SSRF, injection, XSS, dll)
- `references/web3.md` — smart contract / dApp / on-chain: reentrancy, oracle manipulation, access control, PoC Foundry fork
- `references/verification-and-poc.md` — prinsip PoC + template reproduksi (curl/HTTP & Foundry fork test)
- `references/reporting.md` — struktur report, CVSS v3.1/v4.0, dedup, triage & follow-up
- `references/tooling.md` — tool standar publik per fase (recon, interception, exploitation, Web3, infra)

Materi teknis ini untuk **verifikasi finding & penyusunan PoC yang sah** — bukan izin untuk menyerang target tanpa otorisasi. Semua aturan di bagian "Aturan Dasar" di atas tetap berlaku.

## Referensi Sumber Pengetahuan

OWASP Top 10 & ASVS, CWE, CAPEC, CVSS spec (FIRST), MITRE ATT&CK & D3FEND, NIST (SP 800-115), dokumentasi vendor, dokumentasi resmi program.
