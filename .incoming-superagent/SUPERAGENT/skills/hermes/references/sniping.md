# Sniping: Token Launch & NFT Mint

Sniping = buy di detik-detik pertama launch sebelum harga naik. Berisiko tinggi (honeypot, rug, gas wars). Hermes harus selalu jalankan **pre-trade safety** sebelum eksekusi.

## Token Launch Sniping (EVM)

### Cara Kerja

1. **Listening phase**: subscribe ke `PairCreated` event di factory DEX (Uniswap V2/V3 factory, PancakeSwap factory, dll).
2. **Filter**: filter pair yang relevan (mengandung WETH/USDC/USDT di salah satu sisi).
3. **Safety scan**: cek contract token baru sebelum buy.
4. **Buy**: kirim tx swap dengan gas tinggi.

### Listening: Uniswap V2 PairCreated

```python
from web3 import Web3
import asyncio

UNI_V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"  # Ethereum
PAIR_CREATED_TOPIC = Web3.keccak(text="PairCreated(address,address,address,uint256)").hex()

WETH_BY_CHAIN = {
    1: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    56: "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",   # WBNB
    8453: "0x4200000000000000000000000000000000000006", # WETH on Base
}

async def listen_new_pairs(w3_async, chain_id: int, callback):
    weth = WETH_BY_CHAIN[chain_id].lower()
    sub_id = await w3_async.eth.subscribe("logs", {
        "address": UNI_V2_FACTORY,
        "topics": [PAIR_CREATED_TOPIC],
    })
    async for log in w3_async.socket.process_subscriptions():
        # Decode: topics[1]=token0, topics[2]=token1
        t0 = "0x" + log["params"]["result"]["topics"][1][-40:]
        t1 = "0x" + log["params"]["result"]["topics"][2][-40:]
        if weth not in (t0.lower(), t1.lower()):
            continue   # skip non-WETH pairs
        target_token = t1 if t0.lower() == weth else t0
        await callback(target_token, log)
```

Untuk Uniswap V3, event-nya `PoolCreated` di factory `0x1F98431c8aD98523631AE4a59f267346ea31F984`.

### Pre-trade Safety (CRITICAL)

```python
async def is_safe_to_snipe(w3, token_address: str, chain_id: int) -> tuple[bool, list]:
    issues = []

    # 1. Honeypot API check
    import httpx
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get("https://api.honeypot.is/v2/IsHoneypot",
                        params={"address": token_address, "chainID": chain_id})
        data = r.json()
    if data.get("honeypotResult", {}).get("isHoneypot"):
        issues.append("HONEYPOT terdeteksi via honeypot.is")
        return False, issues
    
    buy_tax = data.get("simulationResult", {}).get("buyTax", 0)
    sell_tax = data.get("simulationResult", {}).get("sellTax", 0)
    if sell_tax > 15:
        issues.append(f"Sell tax tinggi: {sell_tax}%")
    if buy_tax > 15:
        issues.append(f"Buy tax tinggi: {buy_tax}%")

    # 2. GoPlus deeper check
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
                        params={"contract_addresses": token_address})
        gp = r.json()["result"].get(token_address.lower(), {})
    
    if gp.get("is_mintable") == "1":
        issues.append("Token mintable — supply bisa di-inflate owner")
    if gp.get("can_take_back_ownership") == "1":
        issues.append("Owner bisa take-back ownership")
    if gp.get("hidden_owner") == "1":
        issues.append("Hidden owner detected")
    if gp.get("transfer_pausable") == "1":
        issues.append("Transfer bisa di-pause owner")
    if gp.get("is_blacklisted") == "1":
        issues.append("Token punya blacklist function")

    # 3. Liquidity locked? (cek dari GoPlus "lp_holders")
    lp = gp.get("lp_holders", [])
    locked = sum(float(h.get("percent", 0)) for h in lp if h.get("is_locked") == 1)
    if locked < 0.5:
        issues.append(f"Liquidity ter-lock cuma {locked*100:.0f}% — rug risk")

    return len([i for i in issues if "HONEYPOT" in i or "Hidden" in i]) == 0, issues
```

### Eksekusi Snipe

```python
async def snipe(w3, account, token_address: str, eth_amount: float,
                slippage_pct: float, max_priority_gwei: float):
    safe, issues = await is_safe_to_snipe(w3, token_address, w3.eth.chain_id)
    if not safe:
        return {"status": "blocked", "issues": issues}
    
    # Build swap WETH → token via Uniswap V2 router
    router = w3.eth.contract(address=UNI_V2_ROUTER, abi=V2_ROUTER_ABI)
    amount_in_wei = w3.to_wei(eth_amount, "ether")
    
    weth = WETH_BY_CHAIN[w3.eth.chain_id]
    path = [weth, Web3.to_checksum_address(token_address)]
    
    # Get expected out, hitung min_out
    amounts = router.functions.getAmountsOut(amount_in_wei, path).call()
    min_out = int(amounts[1] * (1 - slippage_pct / 100))
    
    tx = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
        min_out, path, account.address, int(time.time()) + 300
    ).build_transaction({
        "from": account.address,
        "value": amount_in_wei,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "gas": 500000,
        "maxFeePerGas": w3.eth.gas_price * 3,
        "maxPriorityFeePerGas": w3.to_wei(max_priority_gwei, "gwei"),
    })
    signed = account.sign_transaction(tx)
    return {"status": "sent", "tx": w3.eth.send_raw_transaction(signed.rawTransaction).hex(),
            "warnings": issues}
```

Pakai `swapExactETHForTokensSupportingFeeOnTransferTokens` (bukan `swapExactETHForTokens`) untuk handle token dengan fee-on-transfer.

### MEV Protection

Sniping di Ethereum mainnet sering kena front-run/sandwich. Mitigasi:

- **Flashbots Protect RPC**: `https://rpc.flashbots.net` — kirim tx ke private mempool
- **MEV Blocker**: `https://rpc.mevblocker.io`
- **Pakai bundle**: kirim approve + buy dalam satu bundle via `eth_sendBundle`

Untuk chain selain mainnet (Base, Arbitrum, BSC), MEV risk lebih rendah tapi tetap ada.

## Token Launch Sniping (Solana — pump.fun / Raydium)

### Listen to new pools (Raydium AMM)

```python
from solana.rpc.websocket_api import connect
from solders.pubkey import Pubkey

RAYDIUM_AMM_PROGRAM = Pubkey.from_string("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8")

async def listen_raydium():
    async with connect("wss://api.mainnet-beta.solana.com") as ws:
        await ws.logs_subscribe(filter_={"mentions": [str(RAYDIUM_AMM_PROGRAM)]})
        async for msg in ws:
            # Parse log → find "initialize2" instruction → extract mint addresses
            ...
```

Untuk pump.fun: listen ke program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` (pump program), filter event create token.

### Buy via Jupiter atau langsung program

Untuk speed (pump.fun snipe), bypass Jupiter dan call program langsung. Tapi untuk kebanyakan case Jupiter cukup cepat dan jauh lebih simple — lihat `references/swap.md` bagian Jupiter.

### Pre-trade safety Solana

- **rugcheck.xyz** API: `https://api.rugcheck.xyz/v1/tokens/{mint}/report`
- Check: mint authority null? freeze authority null? top holder concentration < 20%? LP burned?

## NFT Mint Sniping

### EVM

```python
# Pattern: panggil function mint(uint256 quantity) atau publicMint() di contract NFT
# pas blok mint terbuka.

async def mint_nft(w3, account, nft_contract: str, mint_function: str,
                   quantity: int, value_per: float, max_gas_gwei: float):
    abi = [{"name": mint_function, "type": "function",
            "inputs": [{"name": "quantity", "type": "uint256"}],
            "stateMutability": "payable"}]
    c = w3.eth.contract(address=nft_contract, abi=abi)
    
    total_value = w3.to_wei(value_per * quantity, "ether")
    tx = getattr(c.functions, mint_function)(quantity).build_transaction({
        "from": account.address,
        "value": total_value,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "gas": 300000,
        "maxFeePerGas": w3.to_wei(max_gas_gwei, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(2, "gwei"),
    })
    signed = account.sign_transaction(tx)
    return w3.eth.send_raw_transaction(signed.rawTransaction).hex()
```

Untuk **Magic Eden Launchpad / Crossmint / thirdweb** drop: fungsi mint biasanya `mint`, `publicMint`, `claim`, atau `mintTo`. Selalu **decode contract via Etherscan ABI dulu** untuk dapat nama fungsi & signature yang benar.

### Solana — Candy Machine v3 / Metaplex

```python
# Pakai `metaplex-foundation` SDK (sayangnya lebih lengkap di JS)
# Python alternative: panggil instruction Candy Guard secara manual
# Atau pakai third-party API seperti Magic Eden launchpad endpoint
```

## Pre-Snipe Checklist (Hermes harus jalankan)

- [ ] Konfirmasi user soal jumlah ETH/SOL yang dipakai (ini high-risk)
- [ ] Run safety check; tampilkan semua warning ke user
- [ ] Jika ada CRITICAL warning (honeypot, hidden owner): **block by default**, minta konfirmasi eksplisit kalau user mau lanjut
- [ ] Pastikan slippage realistis (≥10% untuk pair baru karena volatilitas)
- [ ] Set deadline tx pendek (≤5 menit)
- [ ] Untuk Ethereum mainnet: tawarkan Flashbots Protect
- [ ] Setelah sukses: tawarkan auto-set stop-loss / take-profit (lihat bagian terpisah di `airdrop_automation.md` untuk task scheduling)

## Auto-sell Logic (Take-Profit / Stop-Loss)

```python
async def monitor_and_sell(w3, account, token_address: str, entry_price: float,
                            tp_multiplier: float = 2.0, sl_multiplier: float = 0.5):
    while True:
        current = await get_token_price_usd(token_address, w3.eth.chain_id)
        if current >= entry_price * tp_multiplier:
            await sell_token_full(w3, account, token_address)
            return {"exit": "take_profit", "price": current}
        if current <= entry_price * sl_multiplier:
            await sell_token_full(w3, account, token_address)
            return {"exit": "stop_loss", "price": current}
        await asyncio.sleep(10)
```

Untuk price feed: pakai DexScreener API (`https://api.dexscreener.com/latest/dex/tokens/{address}`) — gratis, multi-chain, cukup cepat.

---

## MEV Protection untuk Snipe (v4.0)

Snipe buy adalah target frontrun paling juicy — bot lihat buy kamu di mempool lalu masuk duluan. **Wajib** kirim lewat private relay (`scripts/mev.py`), bukan broadcast publik.

```python
from mev import send_private_tx
# ...setelah is_safe_to_snipe() lolos DAN governor.authorize() allow...
res = send_private_tx(signed.raw_transaction, chain_id, prefer="flashbots", public_w3=w3)
```

Urutan gate snipe v4.0: `PairCreated → is_safe_to_snipe (honeypot/GoPlus) → governor.authorize → send_private_tx → governor.record`. Honeypot CRITICAL = block; governor cap kelewat = block; gas war spike = HALT. Tiga lapis, semua harus lolos sebelum dana keluar.
