# Deep Research

> Playbook riset mendalam: pecah topik, fan-out search, cek memory dulu, baca sumber primer, cross-check, lalu sintesis dengan sitasi.

Gunakan skill ini setiap kali jawaban butuh **lebih dari satu fakta cepat** — keputusan yang dipertaruhkan, klaim faktual yang sensitif, atau topik yang jawabannya tersebar di banyak sumber. Untuk satu fakta sederhana ("kurs USD hari ini", "ibu kota Peru"), `web_search` biasa sudah cukup.

## Kapan Pakai Skill Ini vs `web_search` Biasa

| Situasi | Pakai |
|---------|-------|
| Satu fakta cepat, satu angka, satu definisi | `web_search` biasa (1x) |
| Jawaban butuh ≥2 sumber untuk dipercaya | Skill ini |
| Klaim bisa merugikan kalau salah (aturan, biaya, keamanan, legal) | Skill ini |
| Topik luas: "apakah X legit?", "bagaimana cara kerja Y?", "bandingkan A vs B" | Skill ini |
| User bilang "riset dulu", "pastikan", "teliti" | Skill ini |

Aturan praktis: kalau kamu merasa perlu membuka **lebih dari satu halaman** sebelum menjawab — itu deep research, bukan web_search biasa.

> **Catatan tool vs skill:** ada juga native tool `deep_research` (sekali jalan: cari + buka 3 halaman teratas). Pakai *tool* itu untuk pertanyaan sedang (butuh beberapa sumber tapi tidak kontroversial). Pakai *skill/playbook ini* bila kasusnya butuh ketelitian penuh: klaim dipertaruhkan, sumber bisa bertentangan, atau perlu cek memory lokal dulu. Skill ini yang menentukan kapan tool `deep_research` boleh dipakai sebagai salah satu langkah fan-out.

## Quick Reference

| Action | Command |
|--------|---------|
| Search per sub-query | `web_search(query="...")` — panggil beberapa sekaligus (paralel) |
| Cek memory lokal (WAJIB sebelum riset) | `list_memory()` — plus baca blok `<user_memory>` di konteks |
| Baca sumber primer | `web_fetch(url="https://...")` |
| Halaman butuh JS / login / klik | `browser(action="open", url="https://...")` lalu `browser(action="text")` |
| Simpan temuan penting | `add_memory(fact="...")` |
| Muat ulang playbook ini | `load_skill(name="deep-research")` |

## Alur Riset (ikuti berurutan)

### 1. Pecah topik jadi 3–5 sub-query yang saling melengkapi

Jangan search topik mentah-mentah. Pecah jadi sudut pandang yang masing-masing menjawab satu potongan pertanyaan. Sub-query yang bagus: **spesifik, beda sudut, dan mengarah ke sumber primer**.

Contoh — topik: *"Apakah prop firm XYZ legit untuk ikut evaluasi?"*

1. `situs resmi XYZ syarat evaluasi target profit drawdown` → mengarah ke terms resmi
2. `XYZ prop firm review payout proof` → bukti pembayaran dari trader
3. `XYZ prop firm scam complaint trustpilot reddit` → sisi negatif / red flag
4. `XYZ company registration address who owns` → legalitas perusahaan

Contoh — topik: *"Aturan payout program 2-Step Atlas Funded"*

1. `atlasfunded.com payout policy profit split` → docs resmi
2. `Atlas Funded minimum trading days funded account` → syarat per program
3. `Atlas Funded one-sided exposure rule terms` → aturan risiko resmi
4. `Atlas Funded payout processing time review` → pengalaman user

Patokan: kalau dua sub-query bakal mengembalikan hasil yang sama, gabung. Kalau satu sudut penting belum terwakili (biasanya: **sisi negatif / kritik**), tambah.

### 2. Fan-out: `web_search` per sub-query (boleh paralel)

Jalankan semua sub-query sekaligus dalam satu batch — jangan berurutan satu-satu, itu buang waktu. Dari tiap hasil, catat 2–3 URL paling menjanjikan, prioritaskan:

1. **Sumber primer**: docs resmi, repo resmi, paper, pengumuman resmi perusahaan.
2. **Sumber sekunder kredibel**: media besar, dokumentasi komunitas yang terawat.
3. **Agregator / forum**: dipakai untuk petunjuk dan pengalaman user, BUKAN sebagai bukti final.

### 3. WAJIB: cek memory lokal DULU sebelum mengklaim "tidak tahu"

Sebelum satu pun klaim "saya tidak tahu" atau sebelum menyimpulkan sesuatu yang mungkin user sudah tahu:

1. Baca blok `<user_memory>` di konteks kamu — Zeline sudah menyuntikkan hasil *scored retrieval* memory yang relevan dengan pesan user ke setiap giliran.
2. Kalau butuh sapuan penuh, panggil `list_memory()`.

**Kenapa ini wajib:** memory berisi pengetahuan dan keputusan user dari interaksi sebelumnya. Riset yang mengabaikan memory bisa menghasilkan jawaban yang **bertentangan dengan apa yang user sudah tetapkan** — misalnya menyarankan sesuatu yang user sudah tolak, atau mengklaim tidak tahu padahal jawabannya sudah tersimpan. Itu lebih buruk daripada jawaban yang kurang lengkap: merusak kepercayaan.

Kalau memory berisi fakta yang relevan, sitasi juga: tulis "menurut catatan sebelumnya..." agar user bisa koreksi kalau sudah kedaluwarsa.

### 4. Baca sumber PRIMER dengan `web_fetch` / `browser`

Snippet hasil search **bukan sumber** — itu teaser. Untuk tiap klaim penting, buka halamannya:

- Halaman statis / docs / artikel: `web_fetch(url="...")`.
- Halaman butuh JavaScript, login, atau klik (SPA, dashboard, paywall ringan): `browser(action="open", url="...")` sekali, lalu `browser(action="text")` untuk membaca teks render, `browser(action="links")` untuk daftar tautan. Page tetap terbuka antar panggilan — open sekali saja.
- API publik / endpoint JSON: `web_fetch` cukup kalau responsnya teks.

Prioritas baca: **sumber primer dulu**. Agregator (blog ringkasan, thread forum, AI overview) hanya untuk menemukan petunjuk menuju sumber primer — jangan jadikan satu-satunya dasar jawaban.

### 5. Cross-check: minimal 2 sumber INDEPENDEN untuk klaim faktual penting

"Independen" artinya: domain/penulis berbeda **dan** bukan copy-paste dari sumber yang sama. Dua blog yang sama-sama mengutip satu tweet = satu sumber, bukan dua.

| Jenis klaim | Standar bukti |
|-------------|---------------|
| Angka, tanggal, syarat resmi (target profit, biaya, limit) | ≥2 sumber independen, salah satunya primer |
| Klaim "X terjadi / X melanggar" | ≥2 sumber independen |
| Opini, review, pengalaman user | Boleh 1 sumber, tapi labeli sebagai pengalaman/opini |
| Inferensi kamu sendiri | Labeli eksplisit sebagai inferensi, bukan fakta |

Kalau dua sumber **bertentangan**, jangan pilih yang enak didengar: laporkan keduanya, sebutkan mana yang primer/lebih baru, dan tandai klaimnya BELUM PASTI (lihat langkah 6).

### 6. Sintesis: struktur jawaban + sitasi + pisahkan PASTI vs BELUM PASTI

Struktur jawaban, berurutan:

1. **Ringkasan** — 2–4 kalimat: jawaban langsung atas pertanyaan user.
2. **Detail** — per poin, tiap klaim penting diberi sitasi `[1]`, `[2]` yang merujuk ke daftar sumber.
3. **Daftar sumber** — URL + judul + tanggal akses/diakses kapan (penting untuk info yang bisa berubah).
4. **Yang belum pasti** — bagian eksplisit berisi klaim satu-sumber, sumber yang bertentangan, atau inferensi. Jangan campur dengan fakta terverifikasi.

Contoh label yang jujur:

- ✅ **PASTI** (terverifikasi ≥2 sumber independen): "Split payout 90% untuk semua program — tercantum di halaman payout resmi [1] dan dikonfirmasi di FAQ [2]."
- ⚠️ **BELUM PASTI** (satu sumber): "Satu thread forum menyebut payout diproses 1–3 hari [3] — belum ada konfirmasi dari sumber resmi."
- 🔍 **Inferensi**: "Karena syarat X dan Y, kemungkinan Z — ini kesimpulan saya, bukan pernyataan resmi."

### 7. Simpan temuan yang durable ke memory

Kalau riset menghasilkan fakta yang akan berguna di masa depan (keputusan user, aturan resmi yang sering dirujuk, pelajaran dari kesalahan sumber), simpan dengan `add_memory(fact="...")` — ringkas dan deklaratif, satu fakta per entri. Jangan simpan seluruh laporan; simpan kesimpulannya.

## Anti-Pattern (JANGAN lakukan ini)

1. **Mengutip tanpa baca sumber.** Menulis sitasi `[1]` padahal cuma baca judul/snippet hasil search = sitasi palsu. Buka halamannya, baca isinya, baru sitasi.
2. **Satu sumber untuk klaim besar.** Apalagi kalau sumbernya agregator atau forum anonim. Klaim penting butuh 2 sumber independen (langkah 5).
3. **Mengklaim "tidak tahu" tanpa cek memory.** Selalu langkah 3 dulu. "Tidak ada di memory saya" hanya boleh dikatakan setelah `list_memory()` / blok `<user_memory>` dicek.
4. **Agregator sebagai satu-satunya dasar.** Ringkasan blog/SEO farm boleh jadi peta, bukan fondasi. Fondasi = sumber primer.
5. **Mencampur inferensi dengan fakta.** Setiap kalimat yang bukan fakta terverifikasi harus dilabeli (opini / pengalaman user / inferensi). Pembaca harus bisa membedakan tanpa menebak.
6. **Mengabaikan sumber yang bertentangan.** Kalau sumber A bilang ya dan sumber B bilang tidak, laporkan konfliknya — jangan diam-diam pilih satu.
7. **Riset tanpa batas.** Tetapkan dulu: 3–5 sub-query, baca maksimal ~5 halaman primer. Kalau belum konklusif juga, laporkan apa adanya di bagian BELUM PASTI — jangan loop search tanpa henti.
