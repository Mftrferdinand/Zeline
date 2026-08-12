# Hermes Crypto Agent — Skill

Multi-chain crypto + Web3 agent toolkit untuk Hermes AI Agent.

## Kapabilitas

### Crypto Core
- ✅ Buat wallet baru di EVM / Solana / Sui / Aptos / TON
- ✅ Import wallet dari seed phrase atau private key
- ✅ Swap & sell token via contract address (1inch / Jupiter)
- ✅ Beli/jual NFT (OpenSea, Blur, LooksRare, Magic Eden, Tensor — via Reservoir)
- ✅ Sniping token launch + NFT mint (dengan honeypot/GoPlus safety gate)
- ✅ Otomatisasi airdrop multi-wallet (randomized delay + amount jitter + resume)

### Web3 Lanjutan
- ✅ **Bridge cross-chain**: LI.FI aggregator + Stargate V2 (LayerZero farming) + Across + Wormhole/Mayan + native L1↔L2
- ✅ **DeFi**: Aave/Compound/Morpho lending, Lido/Marinade/Jito staking, EigenLayer restaking, Uniswap V3 concentrated LP, GMX V2 / Hyperliquid perp, Yearn / Pendle
- ✅ **Sign-in & connect**: SIWE (EIP-4361), WalletConnect v2, EIP-712 typed data, ERC-2612 Permit, EIP-1271 universal verify, ENS/SNS resolution

### On-Chain Monitoring (Expanded)
- ✅ **Basic**: wallet/whale tracker (WebSocket + Webhook), ERC-20 Transfer listener, multi-chain portfolio (Zerion/DeBank/Birdeye), DexScreener price alert, Aave health monitor, Telegram/Discord notifier, RPC failover
- ✅ **Mempool sniffer realtime**: filter framework + frontrun protection + approval drainer detection (anti-scam untuk user wallet)
- ✅ **Smart-money tracker**: Nansen + Arkham + Dune integration, copy-trade pattern, top-holder watch
- ✅ **NFT whale alert**: Reservoir WS stream, Magic Eden whale, floor-drop alert
- ✅ **Contract deployment listener**: detect new ERC-20/NFT contract, auto-classify via bytecode, new token launch detector (deploy → liquidity-add → alert)

## Struktur

```
skills/hermes/
├── SKILL.md
├── DISPATCH.md
├── README.md
├── references/  (15 files)
│   ├── wallets.md             swap.md            nft.md            sniping.md
│   ├── airdrop_automation.md  bridge.md          defi.md
│   ├── web3_connect.md        monitoring.md      security.md
│   ├── governor.md            browser.md         (v4.0)
│   ├── contract_read.md       contract_write.md  (v4.0)
│   ├── deploy.md              (v4.0)
└── scripts/  (14 files)
    ├── wallet_manager.py      swap_engine.py     nft_engine.py
    ├── bridge_engine.py       web3_connect.py
    ├── monitoring.py          monitoring_advanced.py
    ├── airdrop_runner.py
    ├── governor.py             mev.py             browser_engine.py  (v4.0)
    ├── contract_reader.py      contract_writer.py (v4.0)
    └── deploy_engine.py        (v4.0)
```

## Dependencies (Python)

```bash
pip install web3 eth-account mnemonic solders solana httpx \
            cryptography pysui aptos-sdk tonsdk base58 bip-utils \
            ens hyperliquid-python-sdk websockets
```

## Environment Variables

```bash
# Vault
export HERMES_MASTER_PW="..."

# Trading & NFT
export ONEINCH_API_KEY="..."              # https://portal.1inch.dev
export RESERVOIR_API_KEY="..."            # https://reservoir.tools

# Portfolio
export ZERION_API_KEY_B64="..."           # base64('apikey:')
export BIRDEYE_API_KEY="..."

# Smart Money (advanced monitoring)
export NANSEN_API_KEY="..."               # paid, paling akurat
export ARKHAM_API_KEY="..."               # free tier ada
export DUNE_API_KEY="..."                 # custom queries

# RPC per chain (recommended punya 2+ untuk failover, plus WS untuk monitoring)
export RPC_EVM_ETHEREUM="https://..."
export WS_RPC_EVM_ETHEREUM="wss://..."
export RPC_EVM_BASE="https://..."
export RPC_SOLANA="https://..."

# Notifier (optional)
export HERMES_TG_BOT_TOKEN="..."
export HERMES_TG_CHAT_ID="..."
export HERMES_DISCORD_WEBHOOK="..."
```

## Prinsip Operasi Hermes

1. **User-funds-only** — tolak credential mencurigakan
2. **No drainer / no scam sybil** — multi-wallet automation OK di wallet sendiri
3. **Confirm before signing** — selalu tampilkan plan decoded (bukan raw hex)
4. **Simulasi sebelum eksekusi**
5. **Secret hygiene** — encrypted vault, audit log auto-redact

## Reminder ke User (Wajib Satu Kali Per Sesi Multi-Wallet)

> "Banyak proyek airdrop (LayerZero, zkSync, Linea, dll) punya deteksi sybil yang bisa blacklist semua wallet terkait. Saya bisa randomize timing & amount + variasi bridge per wallet, tapi tidak menjamin lolos. ToS proyek tanggung jawab Anda."

## Pattern Use Cases

### LayerZero Points Farming
Bridge via Stargate V2 langsung ke variasi chain. Randomized timing 5–180 menit antar wallet, amount jitter ±15%.

### Anti-Drainer Mempool Watch
`MempoolSniffer` watch wallet user untuk pending `approve()`, `setApprovalForAll()`, `permit()`. Kalau detect, push critical alert ke Telegram sebelum tx confirmed.

### Smart-Money Copy-Trade
Pakai `copy_trade_arkham()` — poll Arkham transfer history wallet whale, mirror buy ukuran kecil di Hermes wallet (filter out stablecoin & low-value).

### New Token Launch Sniping
`detect_new_token_launches()` watch deployment ERC-20 → tunggu 5 menit → cek pair Uniswap V2 → kalau ada, alert dengan safety check sebagai gate.

### Sniping dengan Safety Gate
Listen `PairCreated` → honeypot.is + GoPlus check → CRITICAL flag = block, WARN = escalate ke user.
