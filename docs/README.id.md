<p align="center">
  <img src="../assets/zerolinear-logo.png" alt="Zerolinear" width="760">
</p>

<p align="center">
  <a href="https://zeline.zerolinear.com"><img src="https://img.shields.io/badge/DOCS-ZELINE.ZEROLINEAR.COM-38BDF8?style=for-the-badge&labelColor=334155"></a>
  <a href="https://t.me/zerolinear"><img src="https://img.shields.io/badge/TELEGRAM-0A84FF?style=for-the-badge&labelColor=334155&logo=telegram&logoColor=white"></a>
  <a href="https://zerolinear.com"><img src="https://img.shields.io/badge/BUILT%20BY-ZEROLINEAR.COM-1D4ED8?style=for-the-badge&labelColor=334155"></a>
  <br>
  <a href="../LICENSE"><img src="https://img.shields.io/badge/LICENSE-MIT-38BDF8?style=for-the-badge&labelColor=334155"></a>
  <a href="../README.md"><img src="https://img.shields.io/badge/LANG-EN-0A84FF?style=for-the-badge&labelColor=334155"></a>
  <a href="README.id.md"><img src="https://img.shields.io/badge/LANG-ID-1D4ED8?style=for-the-badge&labelColor=334155"></a>
  <a href="README.zh.md"><img src="https://img.shields.io/badge/LANG-中文-1E3A8A?style=for-the-badge&labelColor=334155"></a>
</p>

<p align="center">
  <strong>Zeline Agentic AI</strong> — oleh Zerolinear, sebuah lab riset AI.
</p>

---

# Zeline

Zeline adalah framework AI agentik open-source yang dikembangkan oleh [Zerolinear](https://zerolinear.com).  
Zeline merupakan fondasi fleksibel untuk membangun agen AI yang dapat bernalar, menggunakan tools, berinteraksi dengan sistem eksternal, dan menjalankan alur kerja kompleks berskala banyak langkah.

Alih-alih terikat pada satu model, penyedia, atau infrastruktur tertentu, Zeline dibangun dengan mengutamakan fleksibilitas. Hubungkan model AI pilihan Anda dan endpoint yang kompatibel dengan OpenAI, konfigurasikan penyedia, integrasikan tools, dan kembangkan framework agar sesuai dengan cara kerja agen yang Anda inginkan — model dan penyedia dapat ditukar tanpa perlu membangun ulang sistem, sehingga arsitektur agen tetap portabel dan mudah beradaptasi.

Jalankan secara lokal untuk pengembangan atau deploy ke server maupun cloud Anda sendiri, lalu hubungkan ke antarmuka yang Anda gunakan. Tujuannya adalah menjaga kendali tetap di tangan developer: model Anda, tools Anda, infrastruktur Anda, data Anda. Open-source, model-agnostic, dapat diperluas, dan mengutamakan developer.

Zeline — sebuah proyek [Zerolinear](https://zerolinear.com), dipimpin oleh [Mftrferdinand](https://mftrferdinand.com).

## Fitur

- **Inti agen** — loop agen yang kompatibel dengan OpenAI beserta pemanggilan tool, ditambah CLI interaktif dan kueri sekali jalan
- **Model-agnostic** — bekerja dengan OpenAI, OpenRouter, vLLM, Ollama, dan API apa pun yang kompatibel dengan OpenAI atau Anthropic; tukar model atau penyedia tanpa membangun ulang
- **Memori persisten** — memori jangka panjang yang terisolasi per identitas platform
- **Persistensi sesi** — riwayat percakapan disimpan di SQLite (`~/.zeline/sessions.db`), sehingga tetap ada setelah gateway di-restart
- **Skills** — prosedur Markdown yang dapat digunakan ulang dan dimuat sesuai kebutuhan
- **Gateway perpesanan** — Telegram (long polling, perintah, lampiran), WhatsApp (pemasangan QR Baileys), dan webhook HTTP lokal yang terautentikasi
- **Tools bawaan** — pencarian web, pengambilan web, riset mendalam, permintaan HTTP, baca/tulis/edit/cari berkas, eksekusi kode, dan shell
- **Klien MCP** — hubungkan server MCP eksternal (stdio atau HTTP) dan ekspos tools-nya secara otomatis
- **Profil tool bertingkat** — batasi akses per permukaan:
  - `safe` — hanya memori dan skills publik; default untuk gateway perpesanan
  - `workspace` — `safe` ditambah berkas di dalam workspace pemilik
  - `full` — `workspace` ditambah akses shell; ditujukan untuk CLI pemilik lokal

## Instalasi

**Persyaratan:** Python 3.10 atau lebih baru. WhatsApp juga memerlukan Node.js 18+ dan npm.

### Termux

```bash
pkg install git python -y
curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/Zerolinear/main/install.sh | bash
zeline setup
```

### Linux dan macOS

```bash
curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/Zerolinear/main/install.sh | bash
zeline setup
```

Untuk menginstal dari checkout sebagai gantinya:

```bash
git clone https://github.com/Mftrferdinand/Zerolinear.git
cd Zerolinear
bash install.sh
```

Konfigurasi Anda disimpan secara lokal di `~/.zeline/config.json`. Jalankan pemeriksaan cepat setelah setup:

```bash
zeline doctor
zeline gateway list
```

## Menggunakan CLI

```bash
zeline
# atau
zeline chat -q "What can you do?"
```

## Menghubungkan platform

### Telegram

Buat bot dengan [@BotFather](https://t.me/BotFather), lalu jalankan:

```bash
zeline gateway setup telegram
zeline gateway start
```

Allowlist yang kosong membuat bot bersifat publik. Gateway publik selalu menggunakan profil tool `safe` secara default, sehingga pengguna tidak dapat mengakses berkas host atau shell.

Perintah Telegram:

```text
/start, /help             Show command help
/status                   Show provider and session status
/models                   Show the active model
/model <provider/model>   Persistently switch this installation's model
/new                      Clear the current chat history
/restart                  Restart the current Telegram chat session
/stop                     Stop this Zeline gateway process
/logs                     Show how to inspect gateway logs from the installation terminal
```

Lampiran hingga 256 KB diterima untuk teks, JSON, CSV, berkas kode/konfigurasi umum, dan arsip ZIP yang berisi berkas teks yang aman. PDF berbasis teks diekstrak dengan `pypdf`. Gambar diterima sebagai metadata lampiran; analisis piksel memerlukan penyedia yang mendukung vision.

### WhatsApp

```bash
zeline gateway setup whatsapp
zeline gateway start
```

Pada saat pertama kali dijalankan, Zeline memasang bridge Baileys-nya di `~/.zeline/wa-bridge/` dan menampilkan kode QR. Di WhatsApp, buka **Perangkat tertaut**, pilih **Tautkan perangkat**, lalu pindai kode tersebut.

> Gateway ini menggunakan WhatsApp multi-device melalui Baileys, bukan Meta Business API. Pastikan penggunaan Anda mematuhi kebijakan WhatsApp.

### Webhook HTTP

```bash
zeline gateway enable webhook
zeline gateway start
```

Listener default adalah `127.0.0.1:8765`. Listener ini tidak mendengarkan di internet publik.

```bash
curl http://127.0.0.1:8765/health

curl -X POST http://127.0.0.1:8765/message \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_W...OKEN' \
  -d '{"chat_id":"demo-user","text":"Hello Zeline"}'
```

Tampilkan konfigurasi yang tersamar dengan:

```bash
zeline config show
```

Jika Anda mengekspos webhook melalui tunnel atau reverse proxy, gunakan HTTPS dan tetap aktifkan autentikasi token.

## Referensi perintah

```text
zeline                         Set up a gateway first, then open local chat
zeline chat -q "..."           Send one query after gateway + model setup
zeline setup                   Open the gateway picker (Telegram/WhatsApp/Webhook)
zeline model                   Detect protocol, fetch models, and choose one
zeline doctor                  Check dependencies and configuration
zeline config path             Print the configuration location
zeline config show             Print configuration with masked secrets
zeline gateway setup [name]    Configure telegram, whatsapp, or webhook
zeline gateway enable <name>   Enable a gateway
zeline gateway disable <name>  Disable a gateway
zeline gateway list            Show configured gateways
zeline gateway token webhook   Explicitly reveal a webhook token
zeline gateway start           Run enabled gateways in the background
zeline gateway stop            Stop the background gateway process
zeline gateway status          Show background gateway status
zeline gateway log             Print gateway logs
zeline gateway run             Run enabled gateways in the foreground
zeline skills                  List installed skills
zeline memory                  Print local CLI memory
```

Pada peluncuran pertama, Zeline memerlukan satu gateway yang dipilih dari picker berbasis tombol panah:
Telegram, WhatsApp, Webhook, atau Batal. Zeline hanya mengonfigurasi gateway yang dipilih,
kembali ke terminal, dan mengarahkan pengguna ke `zeline model`; chat lokal tetap
terkunci hingga setup gateway dan model selesai.

Selama setup model, Zeline mendeteksi API yang kompatibel dengan OpenAI atau Anthropic,
mengambil endpoint model penyedia, dan menampilkan picker bernomor. Masukan rahasia
ditampilkan sebagai satu `*` per karakter sementara API key sebenarnya tetap tersembunyi. Jika penyedia
tidak dapat mendaftar model, Zeline memerlukan ID model eksplisit alih-alih menerima
placeholder.

Zeline dapat dengan aman menjelaskan model aktifnya, URL penyedia, protokol, profil tool,
dan tools yang tersedia melalui `runtime_info` dan skill `self-analysis` bawaan.
API key dan token gateway tidak pernah disertakan.

## Keamanan

- Jaga agar `~/.zeline/`, `.env`, kunci penyedia, dan token bot tidak masuk ke Git.
- Pengguna gateway menerima profil `safe` secara default.
- Webhook memerlukan token rahasia dan terikat ke loopback secara default.
- Memori diberi namespace berdasarkan identitas platform, misalnya `telegram:123` atau `webhook:alice`.
- Bridge WhatsApp menggunakan token runtime acak antara Python dan Node.
- Repositori ini mengaktifkan secret scanning, push protection, Dependabot, CodeQL, dan dependency review.

Lihat [SECURITY.md](SECURITY.md) untuk panduan pelaporan.

## Pengembangan

```bash
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --wheel-dir dist .
```

## Peta jalan

- Publikasi PyPI dan artefak rilis yang ditandatangani
- Integrasi layanan untuk systemd dan Termux:Boot
- Lebih banyak adapter perpesanan
- Tugas terjadwal
- API plugin dan ekstensi
- Pencarian sesi dan antarmuka yang lebih kaya

## Lisensi

MIT © 2026 Mftrferdinand
