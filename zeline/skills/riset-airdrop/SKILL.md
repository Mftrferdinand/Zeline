---
name: riset-airdrop
description: "Cari airdrop atau testnet crypto terbaru. Format plain text English, 1 project per pesan, pakai ✧ buat step dan ✷ buat kode. Tanpa emoji, warning, atau gambar."
---

# Riset Airdrop Crypto

Skill untuk mencari dan menyajikan airdrop / testnet crypto terbaru dengan format konsisten yang sudah dikonfirmasi user. Output selalu bahasa Inggris, dikirim satu project per pesan.

## Kapan digunakan

- User minta dicarikan airdrop atau testnet crypto terbaru
- User minta "garapan lain" atau "project lain"
- User minta rekomendasi testnet terbaik yang masih aktif
- User minta analisis kelayakan suatu project

## Cara mencari — PRIORITAS

### Metode #1 (TERCEPAT): WordPress REST API — airdrops.io

Ada script reusable di `scripts/fetch-latest-airdrops.py` — tinggal jalanin:

```bash
python3 scripts/fetch-latest-airdrops.py 7   # 7 hari terakhir
python3 scripts/fetch-latest-airdrops.py 3   # 3 hari terakhir
```

Script return markdown list yang tinggal diformat ulang ke template.**

Atau manual:

AirDrops.io pakai WordPress dengan REST API publik. Ini metode paling cepat dan paling bersih — return JSON, bukan HTML.

```bash
# List semua airdrop terbaru (50 per query)
curl -sL 'https://airdrops.io/wp-json/wp/v2/airdrop?per_page=50&_fields=title,link,date,id' \
  -H 'User-Agent: Mozilla/5.0'

# Ambil detail per airdrop dari slug (dari field `link` di atas)
# Parsing HTML halaman detail pakai grep/sed untuk cari:
# - chain (network)
# - reward
# - deadline
# - requirements/steps
```

**Workflow:**
1. `curl` ke REST API → JSON list airdrop terbaru (filter by date)
2. Pilih 5-10 airdrop paling fresh
3. `curl` ke masing-masing halaman detail → parse HTML untuk deskripsi, chain, reward, deadline, steps
4. Format sesuai template

### Metode #2: Terminal + curl langsung (ketika REST API tidak ada)

Alternatif untuk situs non-WordPress:

- DuckDuckGo: `curl -sL 'https://html.duckduckgo.com/html/?q=latest+crypto+airdrop+June+2026'`
- Bing: `curl -sL 'https://www.bing.com/search?q=...'`
- airdrops.io blog: `https://airdrops.io/wp-json/wp/v2/posts?per_page=20`

### Metode #3: delegate_task (JANGAN andalkan — web_search tool tidak tersedia)

Untuk melakukan pencarian dari berbagai sumber berikut:

- airdrops.io (paling mudah diakses via REST API)
- coinmarketcap.com/airdrops
- defillama.com/airdrops
- coingecko.com/airdrops
- Twitter/X trending crypto airdrop

Namun web_search tool tidak tersedia di environment ini — delegate_task tidak bisa akses web secara native. Gunakan Metode #1 atau #2.

## Aturan bahasa

- **English** untuk konten airdrop, web3, testnet, opportunity, dan step farming
- **Bahasa Indonesia** untuk obrolan biasa, penjelasan konsep, dan percakapan normal

## FORMAT OUTPUT — Final (dikonfirmasi user sesi 2026-06-28, iterasi ~15x)

Ini SATU-SATUNYA format. Kirim SATU PROJECT per pesan. Tidak ada kata pengantar.

**NamaProject**
Deskripsi singkat (1 kalimat maks 50 char)

Register Here : [//Nama](url)
✧ Step pendek (maks 30-35 char, 1 baris HP)
✧ Step pendek (maks 30-35 char, 1 baris HP)
✧ Step pendek (maks 30-35 char, 1 baris HP)

✷ [Info bonus], Code : `KodeReferral`

Source : [//Nama-Sumber](url)

## Aturan format ketat

1. Judul: **bold**, langsung enter, tanpa spasi setelahnya
2. Deskripsi: 1 kalimat pendek, maks 50 karakter, enter
3. "Register Here :" — spasi SEBELUM titik dua, baru spasi lalu link
4. Setiap langkah: `✧` (bukan ➖, bukan •, bukan -), teks maks 30-35 karakter
5. Kode/info bonus: `✷` di awal, kode dalam `monospace` (backtick)
6. "Source :" — spasi sebelum titik dua, link `[//Nama](url)`
7. Antar project: 1 baris kosong. Antar judul-deskripsi-register-langkah: NO SPASI
8. ❌ ⚠️ ❌ — tidak ada peringatan/warning sama sekali
9. ❌ ➖ ❌ • ❌ — cuma ✧ yang dipakai untuk step
10. ❌ blockquote ❌ tabel ❌ kurung ❌ garis pemisah ❌ gambar
11. Link: `[//Teks](url)` — bukan URL mentah, bukan markdown image
12. ❌ kata pengantar — langsung format, kirim 1 project 1 pesan
13. ❌ MEDIA: — jangan kirim gambar/file
14. ❌ emoji — cuma ✧ dan ✷ yang diizinkan, tidak ada emoji lain
15. ❌ descriptor di link — jangan "Twitter" atau "Discord" di Source, cukup `[//Nama](url)` langsung
16. Source cukup **SATU link** — yang paling penting (contoh Twitter DOANG tanpa Discord/Form). Kecuali user minta spesifik "source Twitter, Discord, Form" baru kirim 3.
17. Format giveaway/promo post: **bold title**, 1 kalimat deskripsi langsung (tanpa "Register Here :"), lalu Reward pakai block ``` atau `✧` per tier. Source : [//Twitter](url) — **SATU SOURCE aja** (Twitter biasanya). Bahasa Indonesia untuk konten non-Web3 giveaway.
18. Testnet/Mainnet dibedakan lewat judul — tambah "Testnet" atau "Mainnet" di judul sesuai project

## Informasi yang perlu dicari per project

- Nama project dan link resmi
- Deskripsi chain (L1/L2, fitur utama, pendanaan terkenal jika ada)
- Langkah farming ringkas (maks 5-6 step)
- Kode referral jika ada
- Sumber informasi (Twitter/Discord/website)

## Sumber pencarian (dari yang paling mudah)

- airdrops.io — mudah di-scrape via curl + sed
- Bing search sebagai alternatif Google
- DuckDuckGo HTML mode
- X/Twitter official accounts
- Discord: user share username `@user` untuk add friend/DM. Tidak ada link profil publik. Gunakan `discordapp.com/users/<18-digit-user-id>` untuk direct link (perlu Developer Mode ON di Discord).
  - Cara dapat User ID di Discord HP (UI baru 2025-2026): Settings > Advanced > Developer Mode ON. Lalu ketik `\@namauser` di chat mana aja — Discord akan kirim `<@ID_ANGKA>` sebagai balasan. Copy angka di situ.
  - Alternatif: tap foto profil > tap titik tiga > Copy ID (tidak tersedia di semua versi UI).

## Pitfall

- Jangan panggil user "bos" — panggil **aes**. Ini penting, user sudah protes soal ini.
- Jangan kirim dump mentah delegate_task — format ulang dulu
- Jangan kirim semua project sekaligus — satu per satu
- Jangan pakai ➖ atau • untuk step — pakai ✧
- Jangan pakai ⚠️ — user sudah hapus total
- Jangan kirim gambar via MEDIA: — user skip
- Pastikan tiap baris cukup pendek untuk HP (maks 30-35 karakter per baris)
- Kode referral selalu dalam `monospace`
- Jangan kirim gambar sama sekali — skip MEDIA:
- Jangan ada kata pengantar ("berikut", "saya kirim", dll) — langsung format
- Satu pesan = satu project. Jangan kumpulin 2-3 dalam 1 pesan
- Untuk testnet: sama persis formatnya, bedanya "Testnet" di judul
- **`delegate_task` dengan toolsets `["web"]` TIDAK berfungsi** — web_search tool tidak tersedia. Jangan andalkan delegation. Gunakan terminal + curl langsung.
- **REST API airdrops.io adalah metode tercepat** — jangan buang waktu parsing HTML jika JSON tersedia. Selalu cek `/wp-json/wp/v2/` endpoint dulu.
- **User-Agent penting** — selalu set `-H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)'` pada curl. Tanpa UA, banyak situs return 403/block.
- **CoinMarketCal kena Cloudflare** — jangan buang waktu.
- **Penolakan konten ilegal** — tolak permintaan cookie-to-token conversion, account takeover, carding, atau akses ilegal. Jawab langsung tolak, jangan diproses. Ini sudah dikomunikasikan dan user paham.
