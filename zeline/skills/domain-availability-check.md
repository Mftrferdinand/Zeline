# Domain Availability Check

> Cek status domain (available/taken) dengan benar via RDAP HTTP — tanpa whois CLI. Pakai saat user brainstorming nama brand/project, membandingkan kandidat domain, atau verifikasi klaim 'domain X masih kosong'. Cover .com/.io/.ai/.dev/.app dan pola fallback kalau server registry tidak kejangkau.

Cek status domain via **RDAP over HTTPS** — jangan andalkan `whois` CLI (sering tidak terinstall, dan string-matching outputnya rawan false positive).

## Metode kanonik

RDAP return **HTTP 200 = TAKEN**, **HTTP 404 = AVAILABLE**. Itu satu-satunya sinyal yang dipercaya. Jangan pernah menyimpulkan dari grep pola teks ("No match", "NOT FOUND") — output kosong/gagal koneksi akan ter-grep sebagai "available" dan menghasilkan false positive (kasus nyata: loop whois melaporkan semua domain TAKEN padahal sebagian AVAILABLE, karena `whois` binary tidak ada dan grep tidak match apa-apa).

Server RDAP per TLD (wajib routing ke server yang benar):

| TLD | RDAP server |
|---|---|
| .com / .net | `https://rdap.verisign.com/com/v1/domain/` |
| .io / .ai | `https://rdap.identitydigital.services/rdap/domain/` |
| .dev / .app | `https://rdap.nic.google/domain/` |

Script siap pakai: `scripts/check_domains.py <domain> [<domain> ...]` — routing TLD otomatis, output `AVAILABLE` / `TAKEN` / `UNKNOWN` per domain. Jalankan ini daripada ngetik curl manual.

## Brand naming dengan syarat `.com` tersedia

Jika user meminta nama brand yang domain `.com`-nya masih tersedia, availability adalah **hard constraint**, bukan catatan tambahan.

1. Buat pool kandidat internal, lalu cek **domain persis** tiap kandidat melalui RDAP sebelum memberi shortlist.
2. Tampilkan sebagai rekomendasi hanya kandidat yang statusnya `AVAILABLE`; `TAKEN` dan `UNKNOWN` tidak boleh menjadi top pick.
3. Bila user hanya membandingkan beberapa nama (“bagus mana?”), cek exact finalist tersebut dulu, lalu pisahkan jelas: **nilai brand** vs **status domain**.
4. Jangan memberi daftar nama kreatif yang belum diverifikasi lalu baru melakukan pengecekan setelah user menegur bahwa `.com` harus tersedia.
5. Anggap hasil RDAP sebagai snapshot; recheck 1–3 finalis segera sebelum user checkout di registrar.

### Hard constraints for short-name requests

- Jangan melonggarkan syarat diam-diam. Permintaan **4–6 huruf** berarti **satu kata** dengan panjang tepat 4, 5, atau 6 karakter—bukan dua kata, hyphen, typo-like respelling, atau nama lebih panjang hanya karena `.com`-nya tersedia.
- Tampilkan kandidat per panjang (`4 huruf`, `5 huruf`, `6 huruf`), sertakan cara baca singkat, lalu tolak kandidat yang sulit diucapkan atau tidak bisa dieja setelah didengar sekali.
- Mulai dari batch kecil dan terkurasi. Jangan membuat user menunggu karena loop panjang untuk ratusan kandidat lemah; kirim shortlist yang sudah diverifikasi lebih dulu, lalu lanjut hanya jika user meminta eksplorasi tambahan.
- Hasil batch hanya membuktikan domain yang benar-benar diperiksa. Jangan menyimpulkan seluruh kategori 4–6 huruf “habis” dari satu batch terbatas.

### Hasil RDAP yang tidak bersih

- `UNKNOWN` berarti belum ada bukti availability — jangan ubah jadi `AVAILABLE`.
- Jika koneksi gagal, timeout, rate-limited, atau hasil terlihat konflik, retry finalist **secara sequential** dengan request baru/cache-busting.
- Gunakan HTTP status yang benar-benar diterima dari respons. Jangan menyimpulkan dari exit code `curl` saja, output file dari request sebelumnya, atau hasil kosong.
- Jika setelah retry masih `UNKNOWN`, arahkan user cek registrar; jangan membuat klaim availability.

### Nama dari karakter, aktor, atau IP populer

Domain tersedia **bukan** clearance merek dagang/IP. Untuk nama karakter fiksi yang sangat spesifik atau nama figur terkenal, jangan menjanjikan aman hanya karena user tidak memakai logo, artwork, atau lore asalnya. Beri catatan singkat: lakukan pengecekan merek dagang di negara dan kelas usaha yang relevan sebelum penggunaan komersial; jika perlu konsultasikan ke profesional hukum. Setelah itu, tetap bantu evaluasi brand, pengucapan, dan domain secara praktis.

### Brand-fit & decision discipline

Pisahkan tiga hal saat memberi rekomendasi: **kualitas nama**, **status domain persis**, dan **risiko kebingungan/merek**. RDAP `404` hanya membuktikan domain belum terdaftar pada saat cek; itu bukan bukti nama bagus atau legal-clear.

- Jangan merekomendasikan double-letter, huruf sisipan, atau near-homophone dari brand lain hanya karena `.com` tersedia. Jelaskan singkat risiko terlihat typo, salah eja, dan traffic/email bocor.
- Jangan membalik rekomendasi hanya untuk menutup diskusi. Bila penilaian berubah karena bukti baru (mis. brand aktif dengan nama sama), sebutkan bukti yang mengubahnya.
- Situs aktif dengan nama sama menunjukkan risiko kebingungan praktis, bukan vonis trademark. Untuk penggunaan komersial, arahkan pengecekan trademark terpisah tanpa memberi kepastian hukum.

### Responsiveness & constraint discipline for brand brainstorming

Prioritaskan keputusan cepat yang didukung bukti. Sebelum mencari, ekstrak batas pengguna secara literal: jumlah kata, rentang huruf, bahasa/sound family, TLD, dan apakah user hanya minta opini atau juga cek availability.

#### Pilih mode request dulu

- **Ide saja / “gausah cari .com”.** Jangan jalankan RDAP, jangan membahas availability, dan jangan mengubah jawaban menjadi riset domain. Beri nama kuat + pelafalan + trade-off singkat.
- **Bandingkan finalis.** Cek hanya nama persis yang disebut pengguna; jawab verdict dulu, lalu status domain dan risiko kebingungan jika relevan.
- **Cari `.com` tersedia.** Availability adalah hard constraint, tetapi kualitas nama tetap disaring sebelum diuji.

- **Constraint literal, bukan inspirasi.** Bila user meminta **4–6 huruf**, hasil harus satu kata dengan panjang tepat 4, 5, atau 6 karakter. Jangan menggantinya dengan dua kata, 7+ huruf, hyphen, angka, atau typo-like respelling hanya karena domainnya tersedia.
- **Hard cap batch awal: 10 kandidat.** Untuk eksplorasi domain, mulai 5–10 kandidat kuat yang benar-benar mudah dibaca; kirim shortlist itu dulu. Jangan menjalankan ratusan kandidat, loop serial panjang, atau background scan besar yang membuat user menunggu. Bila hasilnya terlalu sedikit, akui apa adanya dan minta arah sound/meaning berikutnya—jangan padding dengan suku kata acak.
- **Jika salah memahami user, reset segera.** Hentikan pencarian lama yang tidak relevan, akui singkat, ulangi constraint yang benar dalam satu kalimat, lalu lakukan batch kecil yang sesuai. Jangan terus memproses kandidat dari interpretasi lama.
- **Availability bukan alasan menurunkan standar.** Jangan menampilkan rangkaian suku kata acak atau nama sulit dieja hanya karena RDAP memberi 404. Kandidat harus punya pelafalan alami, ejaan yang dapat ditebak setelah didengar sekali, dan tidak terlihat typo dari brand lain.
- **Perbandingan sederhana = jawaban sederhana.** Pisahkan secara ringkas: kualitas nama, status domain, dan risiko kebingungan. Jangan membuka side quest atau mendorong kandidat lama setelah user berganti arah.
- **Opinion harus stabil dan transparan.** Jangan menyuruh user “ambil saja” atau membalik ranking hanya karena domain tersedia. Bila ada trade-off nyata—misalnya dekat dengan brand aktif, ejaan ambigu, atau nama terlalu generik—sebutkan dengan jelas dan biarkan user memilih.
- **Jangan klaim kategori habis.** Hasil batch hanya membuktikan domain yang diperiksa; jangan menyimpulkan semua nama 4–6 huruf sudah taken dari sampel terbatas.

Jalankan recheck sequential dengan cache-busting hanya untuk 1–3 finalis tepat sebelum checkout.

## Pitfall

- **False positive dari string matching** — lihat di atas. Selalu HTTP status code.
- **`rdap.nic.google` kadang tidak resolve dari jaringan tertentu** (Termux/mobile pernah gagal DNS). Kalau `UNKNOWN` untuk .dev/.app, coba ulang dari jaringan lain atau tanyakan registrar langsung (Porkbun/Cloudflare search) — JANGAN klaim available/taken tanpa data.
- **whois CLI tidak ada di Termux** — jangan buang waktu `pkg install whois` kalau hanya butuh status; RDAP lebih cepat dan lebih akurat.
- Domain "TAKEN" belum tentu dipakai — bisa parked/dijual. Kalau user ngotot mau nama itu, opsi: beli dari pemilik, atau pakai pola brand-preserving (`getX.com`, `Xhq.com`, `Xlabs.com`, `X.io`).

## Pola rekomendasi brand (dari sesi nyata)

Urutan yang biasanya masuk akal saat .com utama sudah diambil:
1. **Pertahankan brand, ganti TLD** (`brand.io`) — .io standar devtools/open-source.
2. **Pertahankan brand, tambah kata fungsional** (`getbrand.com`, `brandhq.com`) — brand tidak berubah, domain cuma pintu.
3. **Varian kata sifat** (`brandlabs.com`) kalau positioning-nya studio/ecosystem, bukan single product.
4. **Hindari respelling** (double letter, huruf ganti) — brand yang harus dieja = brand yang bocor; orang akan googling ejaan yang benar dan menemukan orang lain.


---

## Lampiran: `references/brand-name-screening.md`

# Brand-name screening for domain brainstorming

Use this alongside RDAP availability checks when the user is deciding a product/company name.

## Capture hard constraints first

Before generating candidates, explicitly extract:

- required roots or sound families (for example `the user` + `Era`)
- forbidden letters/syllables (for example: no `h`)
- pronunciation goal (short, easy to say, one obvious reading)
- requested count and TLD (usually 20–30 `.com` candidates)

Treat each as a hard filter. Do not reintroduce excluded roots or letters merely because an earlier candidate sounded good.

## Candidate quality filter

Prefer 2–4 clear syllables, predictable spelling, and a natural spoken form. Reject or demote names that:

- look like a typo or a doubled-letter workaround;
- have ambiguous vowel stacks;
- require the owner to spell the name aloud;
- are only distinguishable from an established candidate by one hard-to-hear letter.

## Availability workflow

1. Generate a larger internal pool, then return only the requested number of curated candidates.
2. Batch-check exact domains with the RDAP script (`HTTP 404 = available`, `HTTP 200 = taken`).
3. Present only candidates verified available in the latest check.
4. Treat availability as a time-stamped snapshot. Recheck the user's 1–3 finalists immediately before telling them to register.
5. If the same domain has conflicting results in the conversation, never state it as definitively available; say it needs a fresh check.

## Recommended response shape

Keep it decision-oriented:

1. **Top pick** — one sentence on pronunciation and brand fit.
2. **Runner-ups** — 2–4 choices with distinct positioning.
3. **Curated list** — requested number of available options, grouped by naming family if useful.
4. **Final-check note** — availability can change; recheck before checkout.

Avoid turning unresponsive HTTPS/DNS into an availability claim; only authoritative RDAP status determines registration availability.



---

## Lampiran: `scripts/check_domains.py`

```py
#!/usr/bin/env python3
"""Check domain availability via RDAP HTTP status (200=taken, 404=available).

Usage: python3 check_domains.py domain1.com domain2.io ...

Routes each TLD to the correct RDAP server. Prints one line per domain:
  AVAILABLE  <domain>
  TAKEN      <domain>
  UNKNOWN    <domain>  (<reason>)   <- do NOT claim either way
"""
import sys
import urllib.request
import urllib.error

RDAP_SERVERS = {
    "com": "https://rdap.verisign.com/com/v1/domain/",
    "net": "https://rdap.verisign.com/net/v1/domain/",
    "io": "https://rdap.identitydigital.services/rdap/domain/",
    "ai": "https://rdap.identitydigital.services/rdap/domain/",
    "dev": "https://rdap.nic.google/domain/",
    "app": "https://rdap.nic.google/domain/",
    "xyz": "https://rdap.centralnic.com/xyz/domain/",
    "co": "https://rdap.nic.co/domain/",
    "id": "https://rdap.pandi.id/rdap/domain/",
}
FALLBACK = "https://rdap.org/domain/"  # redirects to authoritative server


def check(domain: str) -> str:
    tld = domain.rsplit(".", 1)[-1].lower()
    servers = [RDAP_SERVERS.get(tld, FALLBACK) + domain]
    if RDAP_SERVERS.get(tld):
        servers.append(FALLBACK + domain)
    last_err = None
    for url in servers:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/rdap+json"},
            )
            urllib.request.urlopen(req, timeout=15)
            return "TAKEN"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "AVAILABLE"
            if e.code in (301, 302, 307, 308):
                continue  # rdap.org redirect target failed; try next
            last_err = f"HTTP {e.code}"
        except Exception as e:  # DNS failure, timeout, TLS, etc.
            last_err = type(e).__name__
            continue
    return f"UNKNOWN ({last_err})"


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    width = max(len(d) for d in sys.argv[1:])
    for d in sys.argv[1:]:
        status = check(d.lower().strip())
        print(f"{status.split(' ')[0]:<10} {d}" + (f"  {' '.join(status.split(' ')[1:])}" if " " in status else ""))


if __name__ == "__main__":
    main()

```
