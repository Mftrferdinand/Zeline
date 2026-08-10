# Telegram Mini App Web3

> Build Telegram Mini Apps with Web3 wallet functionality — wallet creation, balance checks, transfers, transaction history. Covers bot setup, Mini App frontend, backend API, and deployment.

Telegram Mini App (dulu Telegram Web App) adalah halaman web yang dibuka **di dalam layar Telegram** — bukan browser eksternal. Bisa berisi game, dashboard, atau wallet Web3.

## Arsitektur Standar

```
Telegram App
  └── Bot @community_bot
        └── Tombol / Keyboard → "Buka Wallet"
              └── Telegram Mini App (iframe/webview Telegram)
                    ├── Create Wallet (generate keypair)
                    ├── Lihat Saldo (query RPC)
                    ├── Transfer/TF (sign + broadcast)
                    ├── Receive (tampilkan address)
                    └── History Tx
```

## Komponen

| Layer | Teknologi yang Umum | Catatan |
|-------|---------------------|---------|
| Bot Telegram | Python (python-telegram-bot), Node.js (telegraf) | Handle commands + tombol untuk buka Mini App |
| Mini App Frontend | HTML/CSS/JS + React/Vue atau vanilla | Web app yang muncul di dalam Telegram |
| Wallet Web3 | ethers.js / web3.js / viem | Koneksi ke blockchain lewat RPC |
| Backend/API | Node.js/Express, Python/FastAPI | Simpan data user, handle tx di server |
| Database | SQLite, PostgreSQL, MongoDB | Simpan wallet terenkripsi, riwayat |
| APK wrapper (opsional) | WebView ke URL Mini App | Versi standalone tanpa Telegram |
| Hosting | Vercel, Cloudflare Workers, VPS | HTTPS wajib |

## Setup Dasar Bot + Mini App

### 1. Bot Telegram

Buat bot lewat @BotFather:
- `/newbot` → dapet token
- `/setmenubutton` → set link Mini App
- `/setdomain` → domain web app

### 2. Inline Keyboard ke Mini App

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

keyboard = [[InlineKeyboardButton(
    text="🚀 Buka Wallet",
    web_app=WebAppInfo(url="https://domain.com/app")
)]]
reply_markup = InlineKeyboardMarkup(keyboard)
```

### 3. Mini App Frontend

Minimal HTML yang komunikasi dengan Telegram:
```html
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
const tg = window.Telegram.WebApp;
tg.expand(); // full screen
tg.MainButton.setText("Konfirmasi");
tg.MainButton.show();
</script>
```

## Web3 Wallet Integration (ethers.js)

### Generate Wallet (Non-Custodial)
```js
import { Wallet } from 'ethers';

const wallet = Wallet.createRandom();
const address = wallet.address;
const privateKey = wallet.privateKey;
const mnemonic = wallet.mnemonic.phrase; // WAJIB backup oleh user
```

### Cek Saldo
```js
import { JsonRpcProvider } from 'ethers';

const provider = new JsonRpcProvider(RPC_URL); // Infura / Alchemy / public RPC
const balance = await provider.getBalance(address);
const formatted = ethers.formatEther(balance);
```

### Transfer
```js
const tx = await wallet.sendTransaction({
    to: recipientAddress,
    value: ethers.parseEther(amount)
});
await tx.wait();
```

### Sign Message (Verifikasi kepemilikan)
```js
const signature = await wallet.signMessage(message);
```

## Custodial vs Non-Custodial

| Mode | Cara | Risiko |
|------|------|--------|
| **Non-Custodial** | Key pair digenerate di frontend, seed phrase user pegang sendiri | User tanggung jawab backup |
| **Custodial** | Key pair digenerate & disimpan di backend (terenkripsi) | Lo yang tanggung jawab keamanan server |

Rekomendasi: **Non-Custodial** untuk wallet — user simpan seed phrase.

## Pitfalls & Catatan

- **HTTPS wajib** — Telegram Mini App hanya jalan di HTTPS. Localhost gak bisa (kecuali tunneling).
- **Telegram Web App API** — hanya jalan di dalam Telegram app, bukan browser biasa.
- **Seed phrase backup** — user harus backup 12/24 words. Kasih opsi download PDF/copy.
- **Gas fee** — user butuh native token buat gas (ETH, BNB, MATIC, dll).
- **RPC rate limit** — public RPC punya limit, pakai Infura/Alchemy/QuickNode untuk production.
- **Transaction signing** — jangan kirim private key ke server untuk non-custodial. Sign di frontend.
- **Termux dev preview** — di HP gak bisa test Mini App langsung karena Telegram Mini App butuh URL publik HTTPS. Solusi: deploy ke Vercel/CF Workers tiap preview.

## Deploy

| Hosting | Cocok untuk | HTTPS |
|---------|-------------|-------|
| Vercel | Frontend + API serverless | ✅ Auto |
| Cloudflare Workers | Edge API | ✅ Auto |
| Railway / Render | Backend penuh | ✅ Auto |
| VPS (DigitalOcean, dll) | Full control | Butuh Nginx + certbot |
| localhost.run (testing) | Preview dari Termux | ✅ Tunnel |

## User Preferences (the community)

- Branding: "the community"
- Prefer clean, minimalis, non-cheesy design
- Pink/soft tone background, CSS-only animations
- No emoji in production UI (use icons/SVGs instead)
