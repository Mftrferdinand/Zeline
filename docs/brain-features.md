# Zeline Brain Features

Dokumentasi fitur-fitur baru di Zeline.

## 1. Auto-fetch Background Sync

Sinkronisasi otomatis data eksternal ke memory setiap 20 menit (configurable).

**Config:** `~/.zeline/autofetch.json`
```json
{
  "interval_minutes": 20,
  "sources": [
    {"type": "rss", "url": "https://example.com/feed.xml", "name": "Tech news"},
    {"type": "url", "url": "https://example.com/status", "name": "Status page"},
    {"type": "file", "path": "~/notes.md", "name": "My notes"}
  ]
}
```

**Cara kerja:**
- Dijalankan otomatis setiap turn agent (cek `should_run()`)
- Hanya simpan jika konten berubah (hash check)
- Konten difilter injection sebelum masuk memory
- Masuk ke episodic memory sebagai episode

## 2. Split-brain (Reflex vs Reasoning)

Dua layer pemrosesan:
- **Reflex:** respons instan tanpa LLM untuk sapaan, terima kasih, jam, tanggal
- **Reasoning:** full agent loop untuk task kompleks

**Di CLI:** otomatis aktif. Ketik "hai" → jawab instan tanpa panggil model (gratis).

**Fungsi:**
- `splitbrain.classify(text)` → "reflex" atau "reasoning"
- `splitbrain.reflex_response(text, user_name)` → string atau None

## 3. Subconscious Loop

Background process yang review history dan inject steering directives.

**Cara kerja:**
- Dijalankan tiap 10 turns di agent loop
- `review()` cek goals, deteksi drift
- Directives disimpan di `~/.zeline/subconscious_directives.json`
- Di-pop otomatis dan di-inject sebagai `[SUBCONSCIOUS]` history

## 4. GEPA (Automatic Self-improvement)

Belajar otomatis dari pola tool yang sukses.

**Cara kerja:**
1. Setiap tool call dicatat (`record_tool_call`)
2. `auto_learn()` deteksi pola berulang yang sukses (min 3x, 80%+ success)
3. Buat draft skill otomatis
4. Draft dipromote jadi permanent setelah 3x pakai sukses
5. Draft buruk (< 80% success dari 5x) jadi deprecated

**Tools:**
- `gepa_drafts` — list draft skills
- `gepa_learn` — trigger learning manual

**Deduplikasi:** pola yang merupakan subsequence dari pola lebih panjang dihapus.

## 5. Visual Workflows

Workflow DAG dengan approval gate.

**Via API:**
- `POST /api/workflows` — save workflow
- `GET /api/workflows` — list
- `GET /api/workflows/<id>` — get
- `DELETE /api/workflows/<id>` — delete

**Node types:** `task`, `approval`, `note`

**Security:** workflow ID di-sanitize (path traversal fix).

## 6. Skill Hub

Install skill dari zip package dengan safety scanning.

**Tools:**
- `skill_pack` — pack skill jadi zip
- `skill_install` — install dari URL atau file lokal

**Security:**
- Scan konten untuk pola berbahaya sebelum install
- Quarantine jika terdeteksi
- Nama di-sanitize (path traversal fix)
- Download dibatasi 10MB, timeout 30s
- Hash verification jika tersedia

### ClawHub (Community Skill Registry)

ClawHub adalah registry skill komunitas publik (5000+ skills). Zeline bisa mencari dan menginstall skill langsung dari registry.

**Tools:**
- `clawhub_search(query, limit)` — cari skill di registry (limit 1–50)
- `clawhub_install(slug)` — install skill dari registry

**Security:**
- SSRF protection via `_safe_request` (IP internal ditolak, redirect maks 5 hop)
- Response body dibatasi 10MB (streaming guard, tidak bisa di-bypass via chunked encoding)
- Konten skill di-scan prompt injection sebelum install (quarantine jika terdeteksi)

## 7. Execution Backends

Jalankan command di environment berbeda.

**Config:** `execution.backend` di config (`local`/`docker`/`ssh`/`sandbox`)

**Backends:**
- **local** (default) — jalankan langsung
- **docker** — jalankan di container (mount workspace)
- **ssh** — jalankan di remote host via SSH
- **sandbox** — jalankan dengan bubblewrap (isolasi filesystem)

**Security:** fail-closed — jika backend non-local gagal, command TIDAK dijalankan locally.

## 8. ACP (Agent Client Protocol)

Integrasi dengan IDE (VS Code, Zed, dll) via JSON-RPC.

**Jalankan:** `zeline acp`

**Methods:**
- `initialize` — handshake
- `session/new` — buat session
- `session/prompt` — kirim prompt, jalankan agent loop
- `session/cancel` — cancel session

**Catatan:** butuh model terkonfigurasi (`zeline model`).

## 9. Dashboard Web UI

Dashboard web penuh di WebChat (`zeline gateway setup` lalu `zeline gateway start`), bukan sekadar halaman chat.

**Akses:** buka `GET /` dengan header `Authorization: Bearer <token>` (token yang sama dengan API).

**8 tab:**
- **Ringkasan** — kartu statistik: model aktif, worker berjalan, tools, connectors, sesi terindeks, skill dipelajari, token hari ini
- **Chat** — UI chat yang sudah ada (dengan rate limit)
- **Workers** — tabel worker dengan auto-refresh tiap 5 detik
- **Goals** — kartu goal dengan progress bar
- **Workflows** — daftar workflow, lihat detail, hapus
- **Skills** — tabel skill dengan pencarian
- **Cron** — jadwal cron dan waktu berikutnya
- **Connectors** — grid kartu connector

**Fitur:** dark theme, Bahasa Indonesia, responsif (mobile-friendly), tanpa dependensi eksternal (offline penuh). Semua render memakai `textContent` — tidak ada `innerHTML` (anti-XSS).

**Security:** halaman tanpa token hanya menampilkan login; semua API butuh token valid (401 jika salah/tidak ada).

## 10. Workflow Execution Engine

Jalankan workflow DAG secara nyata — bukan sekadar data.

**Node types:** `task` (jalankan via agent), `approval` (pause, tunggu persetujuan), `note` (dokumentasi, di-skip).

**Eksekusi:** topological sort (Kahn's algorithm), cycle ditolak fail-closed, timeout per node, error node → workflow `failed` (tidak hang). Approval yang tidak di-resume dalam batas waktu → `failed`.

**Tools:**
- `workflow_execute` (risk install) — jalankan workflow
- `workflow_pause` / `workflow_resume` (risk write) — jeda/lanjut
- `workflow_status` (risk read) — lihat status eksekusi

**API (WebChat):**
- `POST /api/workflows/<id>/execute` → `{ok, exec_id}`
- `GET /api/workflows/<id>/executions`, `GET /api/executions/<id>`
- `POST /api/executions/<id>/pause`, `POST /api/executions/<id>/resume`

**Security:** state disimpan atomic (tmp + rename, 0o600) di `~/.zeline/workflows/executions/`, direktori 0o700, exec ID di-sanitize, symlink ditolak. Status basi setelah restart tampil sebagai `interrupted`.

## 11. Hooks Engine

Automasi event-driven — jalankan hook saat event tertentu terjadi.

**Event types:** `on_tool_call`, `on_tool_result`, `on_turn_start`, `on_turn_end`, `on_error`, `on_skill_learned`.

**CLI:**
- `zeline hooks list` — daftar hooks
- `zeline hooks add --command "./notify.sh" <nama> <event>` — tambah hook shell
- `zeline hooks remove <nama>` — hapus hook
- `zeline hooks errors` — lihat error hooks

Hooks tersimpan di `~/.zeline/hooks.json` (0600, bisa di-edit manual). Tipe: `builtin` (command-logger, session-memory) atau `command` (shell, menerima JSON event via stdin).

**Keamanan & ketahanan:** tiap hook berjalan isolated dengan timeout 5 detik (daemon thread, tidak blokir shutdown); hook yang gagal tidak crash agent loop; error dicatat ke `hook-errors.jsonl`. Log di-chmod 0600 dan secret (api_key/token/password) di-redact sebelum ditulis. Hooks hanya bisa dipasang via CLI operator — model tidak punya akses.

## 12. SuperContext

Pre-message research sweep — sebelum model membaca pesan, Zeline menyapu memory/skills/goals untuk konteks relevan. Tidak ada cold start.

**Cara kerja:** ekstrak keyword dari pesan → cari di episodic memory (FTS5), learned skills, goals aktif, dan user model → gabung jadi blok konteks (maks ~2000 karakter) → prepend ke system prompt sebagai `[SUPERCONTEXT]`.

**Config** (`agent` di config):
- `supercontext: true` — aktif/nonaktif (default True)
- `supercontext_max_chars: 2000` — batas panjang (200–8000)

**Performa:** <500ms (FTS5 terindeks); sapaan seperti "hai" return kosong dalam ~0ms (fast-path).

**Security:** konten memory yang terdeteksi sebagai prompt injection di-drop sebelum masuk system prompt; pencarian session bersifat identity-scoped (satu chat tidak bocor ke chat lain). Skills dan user model sengaja global.

## 13. Peer Communication

Komunikasi bot-to-bot — dua instance Zeline saling kirim pesan dan delegasi task.

**CLI:**
- `zeline peer keygen` — buat shared secret
- `zeline peer serve --port 8788` — jalankan peer server
- `zeline peer send <url> "<pesan>"` — kirim pesan ke peer

**Config** (`peer` di config): `{"secret": "<shared>", "peers": {"nama": {"url": "...", "secret": "..."}}}`.

**Tool:** `peer_send` — kirim ke peer yang dikonfigurasi (by name, bukan raw URL).

**Security:** auth wajib via shared secret (`hmac.compare_digest`); rate limit 30 req/60s (berlaku juga untuk request tanpa auth); SSRF protection — IP internal/metadata cloud (169.254.169.254) ditolak, loopback diizinkan; body cap 32KB; maksimal 4 agent turn peer berjalan bersamaan — request ke-5+ dapat HTTP 503 "server busy".

## 14. Memory Tree (Obsidian Vault)

Export memory sebagai vault Markdown yang bisa dibuka di Obsidian — view yang readable, bukan storage baru.

**Struktur vault:**
```
vault/
  README.md            # index dengan wikilinks
  daily/2026-10-08.md  # episodic memory per hari
  topics/<topik>.md    # agregasi per keyword
  skills/<skill>.md    # ringkasan learned skills
  goals.md             # goals aktif & selesai
  user.md              # user model traits
```

**CLI:**
- `zeline vault export --path ~/vault` — export penuh
- `zeline vault sync --path ~/vault` — incremental (hanya file yang berubah)

**Config** (`memory.vault_path` di config): default `~/zeline-vault`.

**Security:** path traversal ditolak (resolve guard), identity di-hash SHA-256.

## 15. Email Gateway

Gateway email native (IMAP IDLE + SMTP) — Zeline bisa menerima dan membalas email.

**Config** (`gateways.email` di config, file `~/.zeline/gateways/email.json` 0600):
`imap_host`, `imap_user`, `imap_pass`, `smtp_host`, `smtp_user`, `smtp_pass`, `allowed_senders` (allowlist), `tool_profile`.

**Tool:** `email_send(to, subject, body)` — kirim email (risk network).

**Fitur:** IMAP IDLE real (fallback ke polling jika server tidak support); email masuk dari `allowed_senders` diteruskan ke agent loop; auto-reply dengan header threading (`In-Reply-To`).

**Security & anti-spam:** password tidak pernah di-log; email auto-generated (Auto-Submitted, Precedence: bulk, dll) di-skip; alamat sendiri di-skip; Message-ID yang sudah dibalas tidak diproses ulang; **semua email keluar membawa header `Auto-Submitted: auto-replied`** (cegah loop antar dua instance Zeline); fetch dibatasi `max_fetch_bytes` (default 25MB, dicek via `RFC822.SIZE` sebelum download body).

**Behavior yang perlu diketahui:**
- `allowed_senders` **WAJIB** diisi — config dengan allowlist kosong ditolak saat validasi (tidak boleh terima email dari siapa pun).
- Maksimal 20 email diproses per siklus poll — sisanya di-skip dengan warning di log.
- Reconnect IMAP memakai exponential backoff (10 detik → double tiap gagal → maks 300 detik), reset saat koneksi sukses.
- Email dengan `Authentication-Results: dkim=fail` atau `spf=fail` ditolak (anti-spoofing); email body difilter injection sebelum diteruskan ke agent.

## 16. Voice (STT/TTS)

Speech-to-text dan text-to-speech offline — semua backend opsional, dideteksi saat runtime.

**CLI:**
- `zeline voice transcribe <file> [--model tiny] [--language id]` — audio → teks (faster-whisper)
- `zeline voice speak "<teks>" -o out.wav` — teks → WAV (piper → espeak-ng → espeak)
- `zeline voice status` — lihat backend yang tersedia
- `zeline voice download-model [model]` — download model STT (opt-in eksplisit)

**Tools:** `voice_transcribe` (risk read), `voice_speak` (risk write) — path di-contain ke workspace.

**Catatan:** tidak ada download model otomatis (berat); `transcribe` pakai `local_files_only=True` — gagal jujur jika model belum ada. Teks ke TTS lewat stdin (bukan argv) — aman dari injeksi; nilai voice/rate/pitch yang diawali `-` ditolak.

**Keterbatasan:** butuh backend terinstall (`pip install faster-whisper` / piper / espeak). Tanpa backend, perintah gagal dengan pesan yang jelas.
