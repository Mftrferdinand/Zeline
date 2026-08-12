# Cross-Chain Bridging

Reference untuk bridge token antar-chain. Pilih bridge sesuai use case: speed, security, atau airdrop farming.

## Pilih Bridge Sesuai Kebutuhan

| Bridge | Best for | Speed | Notes |
|---|---|---|---|
| **Stargate (LayerZero)** | EVM ↔ EVM, deep liquidity USDC/USDT/ETH | 1–5 menit | Sering masuk eligibility airdrop LZ |
| **Across** | EVM ↔ EVM cepat, fee murah | 1–3 menit | Intent-based, capital-efficient |
| **Wormhole + Mayan** | EVM ↔ Solana ↔ Sui ↔ Aptos | 5–15 menit | Multi-VM coverage terluas |
| **deBridge / DLN** | EVM ↔ EVM ↔ Solana, slippage protection | 1–5 menit | Bagus untuk swap+bridge |
| **Hop** | Ethereum L1 ↔ L2 | 5–30 menit | Native asset preservation |
| **Native bridges** | L1 → official L2 (Arbitrum/Optimism/Base/Linea) | 1–15 menit deposit, 7 hari withdraw | Paling secure, paling lambat utk withdraw |
| **Synapse** | Stable asset bridge multi-chain | 1–5 menit | Bagus untuk stablecoins |
| **Jumper / LI.FI** | Aggregator routing terbaik | — | API gratis, agg semua bridge di atas |

**Default rekomendasi**: pakai **LI.FI aggregator** untuk routing otomatis, kecuali user spesifik minta bridge tertentu (misal untuk farming poin LayerZero).

## LI.FI Aggregator (Recommended)

LI.FI cek semua bridge + DEX di backend, return rute optimal. Gratis sampai ribuan request/hari.

```python
import httpx

LIFI_API = "https://li.quest/v1"

async def lifi_quote(from_chain: int, to_chain: int,
                      from_token: str, to_token: str,
                      from_amount: str, from_address: str,
                      to_address: str | None = None):
    """from_amount: dalam unit terkecil (wei untuk ETH, lamports untuk SOL)."""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{LIFI_API}/quote", params={
            "fromChain": from_chain, "toChain": to_chain,
            "fromToken": from_token, "toToken": to_token,
            "fromAmount": from_amount,
            "fromAddress": from_address,
            "toAddress": to_address or from_address,
            "slippage": 0.01,    # 1%
        })
    return r.json()

async def lifi_execute(w3, account, quote: dict):
    """quote dari lifi_quote() — punya field `transactionRequest` yang siap-sign."""
    tx_req = quote["transactionRequest"]
    
    # Approve kalau dari ERC-20 (bukan native)
    if quote["action"]["fromToken"]["address"].lower() not in ("0x0000000000000000000000000000000000000000",
                                                                  "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"):
        spender = tx_req["to"]
        amount = int(quote["action"]["fromAmount"])
        approve_if_needed(w3, account, quote["action"]["fromToken"]["address"], spender, amount)
    
    tx = {
        "from": account.address,
        "to": w3.to_checksum_address(tx_req["to"]),
        "data": tx_req["data"],
        "value": int(tx_req.get("value", "0x0"), 16) if isinstance(tx_req.get("value"), str) else int(tx_req.get("value", 0)),
        "gas": int(tx_req["gasLimit"], 16) if isinstance(tx_req["gasLimit"], str) else int(tx_req["gasLimit"]),
        "gasPrice": int(tx_req["gasPrice"], 16) if isinstance(tx_req["gasPrice"], str) else int(tx_req["gasPrice"]),
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "chainId": w3.eth.chain_id,
    }
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
    return tx_hash

async def lifi_status(tx_hash: str, from_chain: int, to_chain: int) -> dict:
    """Track status bridge — penting karena destination tx beda hash."""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{LIFI_API}/status", params={
            "txHash": tx_hash,
            "fromChain": from_chain, "toChain": to_chain,
        })
    return r.json()
    # status: PENDING | DONE | FAILED
    # receiving: {txHash: "..."} kalau sudah landing di dest chain
```

## LayerZero / Stargate Langsung (Untuk Farming LZ Points)

Stargate adalah app utama yang qualified untuk airdrop LayerZero. Kalau farming poin LZ, **panggil Stargate langsung** (bukan via aggregator) supaya jejaknya jelas di on-chain analytics.

### Stargate v2 — Token Bridge

```python
STARGATE_V2_POOLS = {
    # chain_id: {symbol: pool_address}
    1: {
        "USDC": "0xc026395860Db2d07ee33e05fE50ed7bD583189C7",
        "USDT": "0x933597a323Eb81cAe705C5bC29985172fd5A3973",
        "ETH":  "0x77b2043768d28E9C9aB44E1aBfC95944bcE57931",
    },
    8453: {  # Base
        "USDC": "0x27a16dc786820B16E5c9028b75B99F6f604b5d26",
        "ETH":  "0xdc181Bd607330aeeBEF6ea62e03e5e1Fb4B6F7C7",
    },
    42161: {  # Arbitrum
        "USDC": "0xe8CDF27AcD73a434D661C84887215F7598e7d0d3",
        "USDT": "0xcE8CcA271Ebc0533920C83d39F417ED6A0abB7D0",
        "ETH":  "0xA45B5130f36CDcA45667738e2a258AB09f4A5f7F",
    },
    # Tambahan: Optimism, Polygon, BSC, Avalanche, Linea, Scroll dst.
}

LZ_ENDPOINT_IDS = {  # endpoint ID v2 (bukan chain ID!)
    1: 30101, 8453: 30184, 42161: 30110, 10: 30111,
    137: 30109, 56: 30102, 43114: 30106, 59144: 30183,
    534352: 30214, 5000: 30181,  # Linea, Scroll, Mantle
}

STARGATE_V2_ABI = [
    {"name": "send",
     "inputs": [
        {"name": "_sendParam", "type": "tuple", "components": [
            {"name": "dstEid", "type": "uint32"},
            {"name": "to", "type": "bytes32"},
            {"name": "amountLD", "type": "uint256"},
            {"name": "minAmountLD", "type": "uint256"},
            {"name": "extraOptions", "type": "bytes"},
            {"name": "composeMsg", "type": "bytes"},
            {"name": "oftCmd", "type": "bytes"},
        ]},
        {"name": "_fee", "type": "tuple", "components": [
            {"name": "nativeFee", "type": "uint256"},
            {"name": "lzTokenFee", "type": "uint256"},
        ]},
        {"name": "_refundAddress", "type": "address"},
     ],
     "outputs": [], "stateMutability": "payable", "type": "function"},
    {"name": "quoteSend",
     "inputs": [
        {"name": "_sendParam", "type": "tuple", "components": [...]},  # same
        {"name": "_payInLzToken", "type": "bool"},
     ],
     "outputs": [{"name": "fee", "type": "tuple", "components": [
        {"name": "nativeFee", "type": "uint256"},
        {"name": "lzTokenFee", "type": "uint256"},
     ]}],
     "stateMutability": "view", "type": "function"},
]

def address_to_bytes32(addr: str) -> bytes:
    return bytes(12) + bytes.fromhex(addr.removeprefix("0x"))

async def stargate_v2_bridge(w3, account, from_chain_id: int, to_chain_id: int,
                              token_symbol: str, amount: int,
                              slippage_bps: int = 100):
    pool_addr = STARGATE_V2_POOLS[from_chain_id][token_symbol]
    dst_eid = LZ_ENDPOINT_IDS[to_chain_id]
    
    pool = w3.eth.contract(address=pool_addr, abi=STARGATE_V2_ABI)
    
    send_param = {
        "dstEid": dst_eid,
        "to": address_to_bytes32(account.address),
        "amountLD": amount,
        "minAmountLD": amount * (10000 - slippage_bps) // 10000,
        "extraOptions": b"",
        "composeMsg": b"",
        "oftCmd": b"",   # OFT v2 cmd
    }
    
    # 1. Quote native fee
    fee = pool.functions.quoteSend(send_param, False).call()
    native_fee = fee[0]
    
    # 2. Approve kalau token bukan ETH
    if token_symbol != "ETH":
        token_addr = get_underlying_token(from_chain_id, token_symbol)  # lookup
        approve_if_needed(w3, account, token_addr, pool_addr, amount)
        value = native_fee
    else:
        value = native_fee + amount
    
    # 3. Send
    tx = pool.functions.send(send_param, (native_fee, 0), account.address).build_transaction({
        "from": account.address,
        "value": value,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "gas": 500000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    })
    signed = account.sign_transaction(tx)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()
```

### Track LayerZero Status

```python
async def layerzero_scan(src_tx_hash: str) -> dict:
    """Track tx via LayerZeroScan API."""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://scan.layerzero-api.com/v1/messages/tx/{src_tx_hash}")
    return r.json()
    # status: INFLIGHT | DELIVERED | FAILED | BLOCKED
```

## Across Protocol (Intent-Based, Cepat)

Across pakai relayer competition — relayer kasih dana di chain tujuan, lalu klaim back via UMA optimistic oracle. Hasilnya: bridge dalam menit, tidak perlu menunggu finalisasi multi-block.

```python
ACROSS_API = "https://app.across.to/api"

async def across_quote(from_chain: int, to_chain: int,
                        input_token: str, output_token: str,
                        amount: int, recipient: str):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{ACROSS_API}/suggested-fees", params={
            "originChainId": from_chain,
            "destinationChainId": to_chain,
            "inputToken": input_token,
            "outputToken": output_token,
            "amount": str(amount),
            "recipient": recipient,
        })
    return r.json()

async def across_deposit(w3, account, quote: dict, amount: int,
                          input_token: str, output_token: str,
                          to_chain_id: int, recipient: str):
    """Call SpokePool.depositV3()."""
    spoke_pool = quote["spokePoolAddress"]
    spoke = w3.eth.contract(address=spoke_pool, abi=SPOKE_POOL_ABI)
    
    output_amount = int(amount) - int(quote["totalRelayFee"]["total"])
    
    if input_token.lower() != "0x0000000000000000000000000000000000000000":
        approve_if_needed(w3, account, input_token, spoke_pool, amount)
        value = 0
    else:
        value = amount
    
    tx = spoke.functions.depositV3(
        account.address,         # depositor
        recipient,               # recipient
        input_token,
        output_token,
        amount,
        output_amount,
        to_chain_id,
        "0x0000000000000000000000000000000000000000",   # exclusiveRelayer (none)
        int(quote["timestamp"]),
        int(quote["timestamp"]) + 18000,                 # fillDeadline
        0,                                                # exclusivityDeadline
        b"",                                              # message
    ).build_transaction({
        "from": account.address, "value": value,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "gas": 350000, "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    })
    signed = account.sign_transaction(tx)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()
```

## Wormhole + Mayan (EVM ↔ Solana/Sui/Aptos)

Untuk bridge ETH/USDC dari EVM ke Solana, Mayan (built on Wormhole) jauh lebih cepat (1–3 menit) daripada Wormhole portal native.

```python
MAYAN_API = "https://price-api.mayan.finance/v3"
MAYAN_SDK_API = "https://explorer-api.mayan.finance/v3"

async def mayan_quote(from_chain: str, to_chain: str,
                       from_token: str, to_token: str,
                       amount: float, from_address: str, to_address: str):
    """from_chain/to_chain: 'ethereum', 'solana', 'base', 'arbitrum', 'sui', 'aptos'"""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{MAYAN_API}/quote", params={
            "amountIn": amount,
            "fromToken": from_token, "toToken": to_token,
            "fromChain": from_chain, "toChain": to_chain,
            "slippage": 1,   # 1%
        })
    return r.json()

# Eksekusi tergantung chain asal:
# - EVM origin: call Mayan contract.swap()
# - Solana origin: build versioned tx dari instruction yang return Mayan API
# Lihat https://docs.mayan.finance untuk full spec
```

## Native L1 → L2 Bridges (Paling Aman)

Untuk deposit besar ke Arbitrum/Optimism/Base/Linea, native bridge punya security paling tinggi (trust-minimized, langsung lewat protocol).

```python
NATIVE_BRIDGES = {
    42161: {  # Arbitrum
        "inbox": "0x4Dbd4fc535Ac27206064B6804cC1cB3F8d29F6f1",
        "method": "depositEth",
    },
    10: {     # Optimism
        "portal": "0xbEb5Fc579115071764c7423A4f12eDde41f106Ed",
        "method": "depositTransaction",
    },
    8453: {   # Base
        "portal": "0x49048044D57e1C92A77f79988d21Fa8fAF74E97e",
        "method": "depositTransaction",
    },
    59144: {  # Linea
        "messageService": "0xd19d4B5d358258f05D7B411E21A1460D11B0876F",
        "method": "sendMessage",
    },
}

async def native_deposit_to_arbitrum(w3_l1, account, amount_eth: float):
    inbox = w3_l1.eth.contract(
        address=NATIVE_BRIDGES[42161]["inbox"],
        abi=[{"name": "depositEth", "inputs": [], "outputs": [{"type": "uint256"}],
              "stateMutability": "payable", "type": "function"}]
    )
    value = w3_l1.to_wei(amount_eth, "ether")
    tx = inbox.functions.depositEth().build_transaction({
        "from": account.address, "value": value,
        "nonce": w3_l1.eth.get_transaction_count(account.address),
        "gas": 200000,
    })
    signed = account.sign_transaction(tx)
    return w3_l1.eth.send_raw_transaction(signed.raw_transaction).hex()
```

**Catatan withdraw**: L2 → L1 native bridge butuh waktu **7 hari** (challenge period). Untuk withdraw cepat, pakai third-party (Across, Stargate, Hop) yang front the funds.

## Multi-Bridge Strategy for Airdrop Farming

Untuk maximize eligibility multiple airdrop sekaligus, mix bridge:

```python
# Contoh task: bridge ETH dari L1 → semua L2 via bridge yg beda per leg
async def diversified_bridge_run(wallet, total_eth: float):
    legs = [
        # (to_chain_id, bridge_name, amount_pct)
        (8453, "stargate", 0.25),       # Base via LZ
        (42161, "across", 0.25),         # Arbitrum via Across (fast)
        (10, "native", 0.20),            # Optimism via native (slow tapi aman)
        (59144, "hop", 0.15),            # Linea via Hop
        (534352, "lifi", 0.15),          # Scroll via LiFi (best route)
    ]
    for to_chain, bridge, pct in legs:
        amount = total_eth * pct
        if bridge == "stargate":
            await stargate_v2_bridge(...)
        elif bridge == "across":
            await across_deposit(...)
        # dst.
        await asyncio.sleep(random.uniform(300, 1800))   # 5–30 menit antar leg
```

## Pre-Bridge Safety Checklist

- [ ] **Cek address tujuan**: kalau bridge ke chain non-EVM (Solana/Sui), address format beda. Validasi format dulu.
- [ ] **Cek liquidity di destination**: bridge fail kalau tidak ada likuiditas di sisi tujuan (umum di Stargate untuk pair non-mainstream).
- [ ] **Estimate fee total**: gas asal + bridge fee + gas tujuan (kalau auto-rebalance gas).
- [ ] **Slippage realistis**: 0.5% untuk stablecoin, 1–2% untuk ETH, 3–5% untuk volatile.
- [ ] **Set timeout**: kalau bridge belum landing dalam 30 menit (Stargate) atau 5 menit (Across), check status manual.

## Konfirmasi User Template

```
🌉 BRIDGE PLAN
─────────────
From:       Ethereum (1) — 0.5 ETH
To:         Arbitrum (42161) — ≈ 0.4985 ETH
Route:      Across (relay-based, ~2 menit)
Bridge fee: ≈ 0.0015 ETH ($3.75)
Gas est.:   ~$5
Recipient:  same wallet
─────────────
Lanjut? (yes/no)
```
