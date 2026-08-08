# Aesora AI Agent

**Aesora** adalah framework AI agent ringan berbasis Python. Setiap orang dapat memasangnya sendiri, memasukkan provider LLM miliknya, lalu menghubungkan bot Telegram, akun WhatsApp, atau aplikasi HTTP webhook mereka sendiri.

> Status: **v0.1.0 — foundation release.** Core agent, memory per user, skills, CLI, Telegram, WhatsApp (Baileys), dan webhook sudah tersedia. Ini belum menargetkan kesetaraan fitur penuh dengan Hermes (mis. Discord, Slack, cron, MCP, dashboard, session DB, plugin marketplace), tetapi arsitektur gateway-nya sudah dibuat modular untuk menuju ke sana.

## Yang tersedia

- Agent loop OpenAI-compatible: LLM → tool call → hasil tool → LLM
- Provider-agnostic: OpenAI, OpenRouter, Nine Router, vLLM, Ollama, atau endpoint OpenAI-compatible lain
- Memory persisten **terisolasi per chat/user**
- Skills Markdown yang dimuat sesuai kebutuhan
- CLI interaktif dan single query
- Gateway Telegram via Bot API long-polling
- Gateway WhatsApp via Baileys + QR Linked Devices
- Gateway HTTP webhook dengan Bearer token
- Tool profile aman:
  - `safe`: memory + baca skill (default semua gateway publik)
  - `workspace`: `safe` + file hanya dalam workspace pemilik
  - `full`: `workspace` + shell (default hanya CLI lokal pemilik)

## Quick start — Termux / Linux / macOS

### 1. Prasyarat

```bash
python3 --version   # Python 3.10+
pip --version
```

Untuk WhatsApp, juga butuh Node.js 18+ dan npm.

### 2. Install dari source

```bash
git clone https://github.com/Mftrferdinand/aesora.git
cd aesora
bash install.sh
```

Atau secara langsung dari GitHub setelah repo dipublikasi:

```bash
curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/aesora/main/install.sh | bash
```

> Di Termux, jika `git` belum ada: `pkg install git python`.

Installer memasang command `aesora` lalu menyalin skill bawaan ke `~/.aesora/skills/`.

### 3. Setup provider dan platform

```bash
aesora setup
```

Wizard meminta:

1. nama agent;
2. `base_url`, API key, dan model provider LLM;
3. platform yang ingin diaktifkan.

Config user tersimpan privat di:

```text
~/.aesora/config.json
```

Cek instalasi:

```bash
aesora doctor
aesora gateway list
```

Chat lokal:

```bash
aesora
aesora chat -q "Halo, siapa kamu?"
```

## Telegram

1. Buka [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Salin token bot yang diberikan.
3. Jalankan:

```bash
aesora gateway setup telegram
# tempel token dari BotFather
aesora gateway start
# atau foreground untuk systemd/tmux: aesora gateway run
```

Perintah bot:

- `/start` atau `/help`
- `/new` untuk menghapus history chat saat ini
- `/status`

### Privasi Telegram

Allowlist kosong berarti bot **publik**. Untuk bot pribadi, isi chat ID milik owner saat setup. Meski bot publik, tool profile default tetap `safe`, sehingga user lain tidak memperoleh akses shell atau file host.

## WhatsApp

> Gateway ini memakai **Baileys / WhatsApp multi-device**, bukan API resmi Meta Business. Gunakan sesuai kebijakan WhatsApp dan risiko akun masing-masing.

```bash
aesora gateway setup whatsapp
aesora gateway start
# atau foreground untuk systemd/tmux: aesora gateway run
```

Pada start pertama Aesora memasang Baileys di `~/.aesora/wa-bridge/`. QR akan muncul di terminal. Buka WhatsApp → **Linked devices** → **Link a device**, lalu scan QR tersebut.

## Webhook HTTP

Aktifkan:

```bash
aesora gateway enable webhook
aesora gateway start
# atau foreground untuk systemd/tmux: aesora gateway run
```

Secara default ia bind di `127.0.0.1:8765`, bukan ke seluruh internet. Endpoint:

```bash
curl http://127.0.0.1:8765/health

curl -X POST http://127.0.0.1:8765/message \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer TOKEN_WEBHOOK_KAMU' \
  -d '{"chat_id":"demo-user","text":"Halo Aesora"}'
```

Lihat config secara aman (token dimask):

```bash
aesora config show
```

Untuk mengekspos webhook, gunakan reverse proxy HTTPS/tunnel milik sendiri dan **jangan** menghapus token autentikasi.

## Migrasi dari Hermes (opsional)

Aesora tidak pernah otomatis membaca atau menyalin secret Hermes. Pemilik Hermes yang memang mau memakai provider lokalnya dapat memilih migrasi eksplisit:

```bash
aesora setup --from-hermes
```

## Command reference

```text
aesora                         Chat CLI
aesora chat -q "..."           Satu query
aesora setup                   Setup semua
aesora setup --from-hermes     Impor Nine Router Hermes secara eksplisit
aesora doctor                  Diagnosis config/dependency
aesora config path             Lokasi config
aesora config show             Config dengan secret dimask
aesora gateway setup [name]    Setup telegram|whatsapp|webhook
aesora gateway enable <name>   Aktifkan gateway
aesora gateway disable <name>  Matikan gateway
aesora gateway list            Lihat konfigurasi platform
aesora gateway token webhook   Tampilkan token hanya saat diperlukan
aesora gateway start           Jalankan gateway aktif di background
aesora gateway stop            Hentikan gateway background
aesora gateway status          Status proses gateway background
aesora gateway log             Lihat log gateway
aesora gateway run             Jalankan semua gateway aktif foreground
aesora skills                  Daftar skill
aesora memory                  Lihat memory CLI lokal
```

## Keamanan

- Jangan commit `~/.aesora/`, `.env`, token Telegram, atau provider API key.
- Gateway publik default `safe`; pertahankan begitu kecuali Anda benar-benar paham risiko `workspace` atau `full`.
- WhatsApp bridge Python↔Node memakai token acak internal per runtime.
- Memory user terpisah antar identitas platform (`telegram:ID`, `whatsapp:JID`, `webhook:ID`).
- Webhook membutuhkan token dan secara default hanya mendengar localhost.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --wheel-dir dist .
```

## Roadmap menuju pengalaman setara Hermes

- [ ] packaging PyPI + signed release artifact
- [ ] system service integration (`systemd` / Termux:Boot)
- [ ] Discord, Slack, Signal, iMessage, Email adapters
- [ ] cron scheduler
- [ ] MCP client/server
- [ ] plugin API dan marketplace skills
- [ ] SQLite session store + search
- [ ] streaming UI / TUI / web dashboard
- [ ] tool approval policy per platform

## License

MIT © 2026 Mahesa F. Ferdinand
