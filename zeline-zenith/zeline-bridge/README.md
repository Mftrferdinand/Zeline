# Zeline Bridge — Zeline Zenith ↔ Zeline Runtime

## What is this?

The Zeline Bridge maps Zeline Zenith skills and tools to native Zeline runtime calls. 
It enables crypto operations (swap, bridge, mint, deploy) to run through Zeline's 
`skills/zeline/scripts/` Python templates with proper environment and governance.

## Quick Start

```bash
# Check what's configured
python3 adapter.py status

# Check environment readiness
python3 adapter.py env

# Verify integrity
python3 adapter.py verify-integrity

# Execute swap
python3 adapter.py swap --token-in ETH --token-out USDC --amount 100 --chain ethereum

# Execute bridge
python3 adapter.py bridge --token USDC --amount 500 --from-chain base --to-chain ethereum

# Check spend governor
python3 adapter.py governor check
```

## Required Environment

Set in `~/.zeline/.env` or export:

```bash
export RPC_URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY"
export PRIVATE_KEY="0x..."  # Or use encrypted wallet
export ETHERSCAN_API_KEY="YOUR_KEY"  # For contract verification
```

## Architecture

```
Zeline Zenith (sk10/sk13/H1-H10)
        ↓
zeline-bridge/adapter.py (this file)
        ↓
Zeline Runtime (skills/zeline/scripts/*.py)
        ↓
Governor (spend caps, kill-switch)
        ↓
Blockchain RPC
```

## Tool Mapping

| Zeline Zenith Tool | Zeline Script | Required Env |
|----------------|---------------|-------------|
| swap | swap_engine.py | RPC_URL, PRIVATE_KEY |
| bridge | bridge_engine.py | RPC_URL, PRIVATE_KEY |
| deploy-token | deploy_engine.py | RPC_URL, PRIVATE_KEY, ETHERSCAN_API_KEY |
| mint-nft | nft_engine.py | RPC_URL, PRIVATE_KEY |
| airdrop-farm | airdrop_runner.py | RPC_URL, PRIVATE_KEY |
| contract-read | contract_reader.py | RPC_URL |
| contract-write | contract_writer.py | RPC_URL, PRIVATE_KEY |
| governor | governor.py | None |
| verify-integrity | skill_integrity.py | None |
