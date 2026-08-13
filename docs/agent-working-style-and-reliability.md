# Agent Working Style & Reliability (Zeline)

> Kapan dipakai: baca di awal sesi kerja pengembangan Zeline, terutama saat menyentuh gateway Telegram, UI progres, web_fetch/search, atau rilis kode. Ini kumpulan pelajaran keandalan & gaya UX yang terbukti — patuhi biar tidak mengulang kesalahan yang sama.

## Bagian A — Gaya UX chat (bikin bersih & responsif)

1. **Kirim pesan SATU-SATU, jangan live-edit.** Hindari streaming/edit-bubble-berulang. Narasi rencana, aktivitas tool, dan jawaban akhir masing-masing jadi bubble utuh sendiri, dikirim sekali. Alur ideal: [penjelasan] → [aktivitas tool] → [penjelasan] → [jawaban]. Edit-in-place bubble makin panjang makin berat di perangkat mobile dan bisa menampilkan markdown setengah jadi.
2. **Progres minimalis. TANPA header & TANPA emoji hiasan.**
   - Tidak ada header `Processing` — tampilkan feed aktivitas apa adanya.
   - Tidak ada banner `Successful` — bubble progres dikunci jadi feed final polos.
   - Command tampil sebagai blok `<pre>` polos, tanpa judul/emoji.
   - Kalau tidak ada aktivitas tool sama sekali → jangan bikin bubble progres.
3. **Jangan pernah menyalahkan model** ("model is slow to respond"). Delay biasanya jaringan/tool/router. Ganti model TIDAK memperbaiki kecepatan — kalau lambat, cari bug jaringan/threading, bukan tukar model.
4. **Presisi mobile.** Asumsikan layar sempit: layout rapi, hindari tabel lebar.
5. **STOP = STOP total.** Kalau user minta berhenti, hentikan segera: no tools, no edits, no push.
6. **Anti over-engineering.** Kerjakan yang diminta saja; hindari fitur/abstraksi berlebih.
7. **Koreksi kecil**: baca file dulu (read_file) lalu edit bagian spesifik — jangan regenerate dari nol atau menimpa file dengan versi lama.

## Bagian B — Keandalan Zeline (pelajaran teknis)

### Gateway Telegram tidak boleh nyangkut / lambat
- **Akar "lambat/macet/bubble ilang-ilangan"**: panggilan Bot API untuk UI-progres (bubble, editMessageText, sendChatAction) memakai timeout panjang + retry. Di jaringan mobile yang sering drop, tiap update MENAHAN loop agent. Ini bukan masalah model.
- **Fix**: beri parameter `attempts` pada helper API; panggilan UI-progres pakai timeout pendek (mis. 6s) + `attempts=1` (fail-fast, dilewati diam-diam kalau gagal). Hanya jawaban akhir yang tetap diretry supaya tidak hilang.
- **Loop polling wajib self-heal**: bungkus tiap update dengan try/except sendiri, pakai backoff adaptif yang reset saat pulih, tangani 409 Conflict (instance dobel), dan heartbeat log berkala. Loop tidak boleh keluar sendiri — kalau exception nembus keluar, thread gateway mati diam dan butuh kill paksa.
- Restart gateway tiap ubah kode (`stop && start`, bukan hot-load). Kalau stop butuh kill paksa, itu tanda proses lama (kode lama) nyangkut — mulai bersih.

### Web fetch: bypass Cloudflare tanpa headless browser
- Banyak situs pakai Cloudflare managed challenge → fetch (langsung / via reader proxy) balik halaman `Just a moment...`, bukan isi asli.
- Stealth browser berbasis Firefox (Camoufox dsb) butuh glibc; tidak jalan native di lingkungan Android/bionic. Jangan janji bisa install sebelum cek arch/libc.
- **Solusi ringan zero-cost**: deteksi halaman challenge (marker `__cf_chl` / `_cf_chl_opt` / "Just a moment"), lalu ambil snapshot terbaru dari archive.org (CDX API cari timestamp status-200 terbaru → versi mentah `<ts>id_/<url>`; dekompres gzip manual bila perlu). Cloudflare tidak melindungi arsip web.

### Disiplin rilis (branch `main` protected)
1. **Test dulu lokal** dengan test runner proyek. Lanjut hanya kalau HIJAU.
2. **Branch + PR** (jangan commit langsung ke main): buat branch, push, buka PR ke main.
3. **Tunggu semua status check CI hijau** sebelum merge.
4. **Merge squash**, lalu verifikasi state == MERGED.
5. **Sync lokal = publik**: `git fetch origin && git reset --hard origin/main`.
6. **Reinstall + restart** supaya runtime = kode yang di-merge; re-run test sebagai sanity check.
7. **Scrub sebelum push** (main = publik): jangan ada ID/kredensial/PII atau endpoint internal di kode. Pakai nilai dummy di test.

### Pitfall CI
- Pemindai keamanan (mis. CodeQL) bisa nge-flag pengecekan substring hostname mentah walau itu hanya memindai konten, bukan sanitasi URL. Pakai marker yang lebih spesifik agar lolos tanpa mengurangi ketepatan.

## Prinsip inti
Target: agent yang responsif, bersih, tanpa drama UI, dan andal. Kalau ada yang lambat/aneh, diagnosa akar masalah (jaringan/threading/blocking I/O) — jangan tambal gejala atau menyalahkan model.
