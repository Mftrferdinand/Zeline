<p align="center">
  <img src="../assets/zerolinear-logo.png" alt="Zerolinear" width="760">
</p>

<p align="center">
  <a href="https://zeline.zerolinear.com"><img src="https://img.shields.io/badge/Docs-zeline.zerolinear.com-7DD3FC?style=flat&labelColor=334155"></a>
  <a href="https://t.me/zerolinear"><img src="https://img.shields.io/badge/Community-0A84FF?style=flat&labelColor=334155&logo=telegram&logoColor=white"></a>
  <a href="../LICENSE"><img src="https://img.shields.io/badge/License-MIT-7DD3FC?style=flat&labelColor=334155"></a>
  <a href="../README.md"><img src="https://img.shields.io/badge/Lang-EN-0A84FF?style=flat&labelColor=334155"></a>
  <a href="README.id.md"><img src="https://img.shields.io/badge/Lang-ID-1D4ED8?style=flat&labelColor=334155"></a>
  <a href="README.zh.md"><img src="https://img.shields.io/badge/Lang-中文-1E3A8A?style=flat&labelColor=334155"></a>
  <br>
  <strong>Zeline Agentic AI</strong> — oleh Zerolinear, sebuah lab riset AI.
</p>

---

# Zeline

Zeline adalah framework AI agentik open-source yang dikembangkan oleh [Zerolinear](https://zerolinear.com). Zeline merupakan fondasi fleksibel untuk membangun agen AI yang dapat bernalar, menggunakan tools, berinteraksi dengan sistem eksternal, dan menjalankan alur kerja kompleks berskala banyak langkah.

Alih-alih terikat pada satu model, penyedia, atau infrastruktur tertentu, Zeline dibangun dengan mengutamakan fleksibilitas. Hubungkan model AI pilihan Anda dan endpoint yang kompatibel dengan OpenAI, konfigurasikan penyedia, integrasikan tools, dan kembangkan framework agar sesuai dengan cara kerja agen yang Anda inginkan — model dan penyedia dapat ditukar tanpa perlu membangun ulang sistem, sehingga arsitektur agen tetap portabel dan mudah beradaptasi.

Jalankan secara lokal untuk pengembangan atau deploy ke server maupun cloud Anda sendiri, lalu hubungkan ke antarmuka yang Anda gunakan. Tujuannya adalah menjaga kendali tetap di tangan developer: model Anda, tools Anda, infrastruktur Anda, data Anda. Open-source, model-agnostic, dapat diperluas, dan mengutamakan developer.

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

**Persyaratan:** Python 3.10+. WhatsApp juga membutuhkan Node.js 18+ dan npm.
Di platform POSIX, Zeline memakai environment Python privat; di Windows paket
dipasang hanya untuk akun pengguna. Tidak perlu root atau Administrator.

### Termux, Linux, dan macOS

```bash
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.1
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(line.split()[0] for line in lines if line.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
export PATH="$HOME/.local/bin:$PATH"
zeline setup
```

### iOS / iPadOS melalui iSH

```sh
apk add bash curl python3 py3-pip
BASE=https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.1
curl -fSLO "$BASE/install.sh" -O "$BASE/SHA256SUMS"
python3 - <<'PY'
from pathlib import Path
import hashlib
lines = Path("SHA256SUMS").read_text().splitlines()
expected = next(line.split()[0] for line in lines if line.split()[-1].lstrip("*") == "install.sh")
actual = hashlib.sha256(Path("install.sh").read_bytes()).hexdigest()
if len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
    raise SystemExit("invalid install.sh checksum entry")
if actual != expected.lower():
    raise SystemExit("install.sh checksum mismatch")
print("install.sh SHA-256 verified")
PY
bash install.sh
zeline setup
```

CLI dan integrasi HTTP bisa dipakai di iSH, tetapi iOS dapat menghentikan gateway
saat iSH tidak berada di foreground.

### Windows PowerShell

```powershell
$base = 'https://github.com/Mftrferdinand/Zerolinear/releases/download/v0.2.1'
Invoke-WebRequest "$base/install.ps1" -OutFile install.ps1
Invoke-WebRequest "$base/SHA256SUMS" -OutFile SHA256SUMS
$expected = ((Get-Content SHA256SUMS | Where-Object { $_ -match ' install.ps1$' }) -split '\s+')[0]
if (-not $expected -or $expected -notmatch '^[0-9a-f]{64}$') { throw 'invalid install.ps1 checksum entry' }
if ((Get-FileHash install.ps1 -Algorithm SHA256).Hash.ToLower() -ne $expected.ToLower()) { throw 'checksum mismatch' }
.\install.ps1
zeline setup
```

Lihat [panduan instalasi lengkap](installation.md) untuk paket prasyarat setiap
OS, instalasi dari checkout, update, perbaikan PATH, keterbatasan iOS, dan
uninstall.

Sesudah itu, periksa tools, integrasi, dan kesehatan instalasi:

```bash
zeline tools list
zeline mcp list
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
zeline                         First run: gateway onboarding; later: local chat
zeline chat -q "..."           Send one query after gateway + model setup
zeline setup                   First run: gateway picker; later: setup center
zeline setup <section>         Configure gateway|model|tools|integrations|agent
zeline model                   Detect protocol, fetch models, and choose one
zeline tools list              List native tools, profiles, and enabled state
zeline tools profile <name>    Set safe|workspace|full for the local CLI
zeline tools enable|disable T  Toggle one native tool for new sessions
zeline tools workspace <path>  Set the owner workspace root
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

Lihat [SECURITY.md](../SECURITY.md) untuk panduan pelaporan.

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
