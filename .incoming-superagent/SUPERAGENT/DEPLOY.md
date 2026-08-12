# DEPLOY.md — Pasang SUPERAGENT V7.0 di OpenClaw (dari nol)

Panduan deploy *agent*-nya ke runtime OpenClaw di VPS lo. (Beda dari `skills/hermes/references/deploy.md` yang itu soal deploy *smart contract*.)

> Ini **menggantikan** instruksi quick-start lama di README. Jalur workspace V7: `~/.openclaw/workspace/superagent-v7/`.

---

## 0. Prasyarat

```bash
# OpenClaw udah keinstall & jalan (gateway)
openclaw --version

# Python 3.10+ dan pip
python3 --version
```

---

## 1. Taruh workspace

OpenClaw nge-inject bootstrap file (AGENTS.md, SOUL.md, IDENTITY.md, USER.md, TOOLS.md) dari **root workspace**. Jadi jangan nest di subfolder — arahin workspace langsung ke folder `openclaw/` ini.

```bash
# salin folder openclaw ke lokasi tetap
cp -r openclaw ~/.openclaw/workspace/superagent-v7

# init config OpenClaw kalau belum
openclaw setup           # bikin ~/.openclaw/openclaw.json
```

Edit `~/.openclaw/openclaw.json`, arahin workspace ke folder tadi:

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.openclaw/workspace/superagent-v7"
    }
  }
}
```

Sekarang AGENTS.md / SOUL.md / IDENTITY.md / USER.md / TOOLS.md / HEARTBEAT.md / MEMORY.md ada di root workspace → ke-inject otomatis tiap sesi.

---

## 2. Install dependency Python

```bash
cd ~/.openclaw/workspace/superagent-v7

# inti Hermes (wallet, swap, web3, dll)
pip install web3 eth-account mnemonic solders solana httpx \
            cryptography pysui aptos-sdk tonsdk base58 bip-utils \
            ens hyperliquid-python-sdk websockets

# governor/mev/integrity/memory/reflection/alerts/briefing/triage/vault/
# watchdog/contract_reader/writer/deploy → cukup web3+httpx+cryptography+stdlib (udah ke-cover)
```

Opsional (per fitur):

```bash
# browser automation (sk15/browser.md)
pip install playwright && playwright install chromium

# voice input — STT lokal gratis (sk15/multimodal)
pip install faster-whisper

# crypto dev: compile/test/deploy kontrak (v7.0)
curl -L https://foundry.paradigm.xyz | bash && foundryup

# ── v7.0 modul opsional (install hanya yang dipakai) ──
# sk18 creative & media
pip install manim                       # animasi math (+ butuh ffmpeg & LaTeX)
brew install chafa ffmpeg               # ASCII/retro video
# ComfyUI = self-host terpisah (GPU); drive via API (sk18). Excalidraw = web/VS Code.
# sk19 desktop & robotics
#   macOS control: osascript bawaan (gak perlu install) — kasih izin Accessibility & Automation
#   Isaac Sim/Omniverse: install via NVIDIA Omniverse (butuh GPU NVIDIA); USD: pip install usd-core
# sk55/sk56/sk57/sk58, sk20 humanizer, sk21 KQL/HIDS → stdlib (eval/humanizer/hids/desktop_control murni stdlib)
#   sk21 KQL akses langsung (opsional): az CLI — `az extension add -n log-analytics`
# sk0 marketplace: skill_market.py butuh httpx (udah ke-cover di deps inti)
```

---

## 3. Isi config

```bash
cp .env.example .env
nano .env                 # isi HERMES_MASTER_PW [WAJIB] + RPC + key yang lo pakai
```

Edit **USER.md** (masih template) — ini WAJIB sebelum first run:
- **Identity**: nama, honorific, timezone, bahasa, level
- **Team Members table**: isi operator (Level 3) + anggota tim (Level 0-3) dengan domain & wallet
- **Billing section**: payment method, currency, auto-bill threshold, wallet pembayaran
- **Domain Focus**: centang domain yang relevan (Crypto/Web3, Content, SaaS, dll)
- **Trigger phrases**: shortcut "go"/"gas"/"profit" dll

Set spend cap governor di `.env` (sangat disarankan):

```bash
HERMES_MAX_TX_USD=500
HERMES_DAILY_CAP_USD=2000
HERMES_SESSION_CAP_USD=1000
```

---

## 4. Lock integritas skill (langkah operator)

Generate manifest di sumber tepercaya (mesin lo), supaya tampering kedeteksi:

```bash
python tools/skill_integrity.py generate            # tulis SKILLS.lock
# opsional, tandatangani:
# export HERMES_SIGNING_KEY=~/.hermes/sign.pem
# python tools/skill_integrity.py generate --sign
```

> **SKILLS.lock** mencakup 58 skill (sk1–sk48, sk52–sk58, H1–H10) + semua tool Python + file bootstrap. SHA-256 hash per-file + opsional Ed25519 signature.

Verifikasi kapan pun:

```bash
python tools/skill_integrity.py verify              # exit 0 = bersih
```

Boot sequence V7 otomatis jalanin `verify` — kalau exit != 0, operasi on-chain ditahan sampai diaudit.

---

## 5. Konfigurasi Hermes

Pastikan konfigurasi runtime Hermes udah bener:

```bash
# Cek config Hermes
hermes config list

# Set model default (pakai yang ada di API keys lo)
hermes config set model "b.ai/deepseek-v4-pro"    # atau "anthropic/claude-sonnet-4-20250514"

# Enable tools yang dibutuhkan
hermes tools list
```

H1–H10 crypto dispatch **auto-route** dari AGENTS.md keyword table — gak perlu config tambahan. Hermes references (15 file) load on-demand, zero always-on cost.

---

## 6. Restart & cek

```bash
pm2 restart openclaw      # atau: systemctl restart openclaw / screen -r
```

Cek boot sequence jalan (lihat AGENTS.md): inject identity → TIME → sk0 registry → USER → MEMORY → integrity verify → reflection cycle → autonomous scan → profit ledger init → briefing if due.

Tes cepat dari channel (Telegram/chat):

```
"siapa lo?"            → harusnya jawab sebagai SUPERAGENT V7 IRONCLAW SUPREME
"cek gas ethereum"     → trigger web3 (sk10/H-skill)
"baca kontrak 0x...."  → trigger H9 contract reader
"profit"               → trigger revenue P&L ledger
```

---

## 7. Jadwalkan service (opsional)

```bash
# alert engine (poll terus)        → jalanin sebagai service/background task
# daily briefing (sekali/hari)     → cron, contoh jam 7 WIB:
0 7 * * *  cd ~/.openclaw/workspace/superagent-v7 && python -c "..."   # wire push_briefing ke notifier lo
# watchdog (mantau proses)         → service interval 30s
```

---

## Checklist ringkas

```
☐ openclaw setup → workspace = ~/.openclaw/workspace/superagent-v7
☐ pip install deps inti (+ playwright/whisper/foundry sesuai fitur)
☐ cp .env.example .env → isi HERMES_MASTER_PW + RPC + caps governor
☐ edit USER.md (identity + team + billing)
☐ python tools/skill_integrity.py generate  →  SKILLS.lock
☐ hermes config set model [model-choice]
☐ restart openclaw → tes "siapa lo?"
☐ (opsional) jadwalkan briefing/alert/watchdog
```

---

## Troubleshooting

- **Bootstrap file gak ke-inject** → workspace nunjuk ke subfolder, bukan root. AGENTS.md/SOUL.md harus di root workspace.
- **`integrity verify` exit 1 di VPS** → wajar kalau lo edit file pasca-generate; audit (sk11) lalu `generate` ulang. Kalau lo gak ngedit apa-apa → investigasi, JANGAN jalanin operasi on-chain.
- **RPC publik timeout** → wajar (rate-limited). Isi `RPC_EVM_*` dengan endpoint lo / pakai RPCRouter failover.
- **`forge`/`playwright`/`whisper` not found** → fitur terkait kasih pesan install, sisanya tetap jalan.
- **Update config gak kebaca** → restart gateway OpenClaw.
- **Team member gak kedeteksi** → cek USER.md — pastikan team table sudah diisi, restart agent.
- **Profit ledger kosong** → pastikan MEMORY.md punya REVENUE LOG section, hermes config tools include profit_ledger.
