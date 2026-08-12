# Hermes Bridge — SUPERAGENT V7 ↔ Hermes Runtime

## What is this?

The Hermes Bridge maps SUPERAGENT V7 skills and tools to native Hermes runtime calls. 
It enables crypto operations (swap, bridge, mint, deploy) to run through Hermes's 
`skills/hermes/scripts/` Python templates with proper environment and governance.

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

Set in `~/.hermes/.env` or export:

```bash
export RPC_URL="https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY"
export PRIVATE_KEY="0x..."  # Or use encrypted wallet
export ETHERSCAN_API_KEY="YOUR_KEY"  # For contract verification
```

## Architecture

```
SUPERAGENT V7 (sk10/sk13/H1-H10)
        ↓
hermes-bridge/adapter.py (this file)
        ↓
Hermes Runtime (skills/hermes/scripts/*.py)
        ↓
Governor (spend caps, kill-switch)
        ↓
Blockchain RPC
```

## Tool Mapping

| SUPERAGENT Tool | Hermes Script | Required Env |
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
