# Swap & Sell Token via Contract Address

Reference untuk swap/sell token di semua chain. Pendekatannya: pakai DEX aggregator dulu (1inch, 0x, Jupiter) — fallback ke DEX router langsung kalau aggregator tidak support.

## Pre-flight Safety (WAJIB sebelum swap apapun)

Sebelum execute swap ke contract address yang user kasih, jalankan check ini:

```python
def safety_check_evm(w3, token_address: str) -> dict:
    """Return dict berisi warning. Kosong = aman lanjut."""
    warnings = []
    addr = Web3.to_checksum_address(token_address)

    # 1. Apakah contract benar-benar ada?
    code = w3.eth.get_code(addr)
    if code == b'' or code == b'0x':
        warnings.append("CRITICAL: tidak ada contract di address ini")
        return {"warnings": warnings, "safe": False}

    # 2. Honeypot check via API (gunakan honeypot.is atau goplus)
    # GET https://api.honeypot.is/v2/IsHoneypot?address={addr}&chainID={chain_id}
    # Parse → kalau isHoneypot True → block

    # 3. Token tax check via goplus
    # GET https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={addr}
    # Warn kalau buy_tax > 10% atau sell_tax > 10% atau cannot_sell_all == "1"

    # 4. Liquidity check — simulasi swap kecil dulu (lihat bagian Simulate)
    return {"warnings": warnings, "safe": len(warnings) == 0}
```

## Swap di EVM Chain

### Opsi 1: 1inch Aggregator (recommended, support 10+ chain)

```python
import httpx

ONEINCH_BASE = "https://api.1inch.dev/swap/v6.0"  # butuh API key sekarang
# Alternatif gratis: https://api.0x.org (per chain endpoint)

async def quote_1inch(chain_id: int, src: str, dst: str, amount_wei: int, from_addr: str):
    url = f"{ONEINCH_BASE}/{chain_id}/quote"
    params = {"src": src, "dst": dst, "amount": str(amount_wei)}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params, headers={"Authorization": f"Bearer {API_KEY}"})
    return r.json()

async def build_swap_tx(chain_id: int, src: str, dst: str, amount_wei: int,
                        from_addr: str, slippage_pct: float = 1.0):
    url = f"{ONEINCH_BASE}/{chain_id}/swap"
    params = {
        "src": src, "dst": dst, "amount": str(amount_wei),
        "from": from_addr, "slippage": slippage_pct,
        "disableEstimate": "false",
    }
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params, headers={"Authorization": f"Bearer {API_KEY}"})
    data = r.json()
    return data["tx"]   # {to, data, value, gas, gasPrice}
```

`WETH/native sentinel` di 1inch: gunakan `0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE` untuk native ETH/BNB/MATIC, address WETH/WBNB/WMATIC untuk wrapped.

### Opsi 2: Uniswap V3 Router (langsung, tidak butuh API key)

```python
UNISWAP_V3_ROUTER = {
    1: "0xE592427A0AEce92De3Edee1F18E0157C05861564",   # Ethereum
    8453: "0x2626664c2603336E57B271c5C0b26F421741e481", # Base
    42161: "0xE592427A0AEce92De3Edee1F18E0157C05861564",# Arbitrum
    10: "0xE592427A0AEce92De3Edee1F18E0157C05861564",   # Optimism
    # dst.
}

ROUTER_ABI = [...]  # ISwapRouter ABI, exactInputSingle

def swap_uniswap_v3(w3, account, token_in, token_out, amount_in, fee_tier=3000, slippage=0.01):
    router = w3.eth.contract(address=UNISWAP_V3_ROUTER[w3.eth.chain_id], abi=ROUTER_ABI)

    # 1. Approve dulu kalau token_in bukan native
    if token_in.lower() != WETH_ADDR.lower():
        approve_if_needed(w3, account, token_in, UNISWAP_V3_ROUTER[w3.eth.chain_id], amount_in)

    # 2. Get expected output via quoter, hitung min_out
    expected_out = quote_v3(w3, token_in, token_out, fee_tier, amount_in)
    min_out = int(expected_out * (1 - slippage))

    # 3. Build & send
    params = {
        "tokenIn": token_in, "tokenOut": token_out, "fee": fee_tier,
        "recipient": account.address,
        "deadline": int(time.time()) + 600,
        "amountIn": amount_in, "amountOutMinimum": min_out,
        "sqrtPriceLimitX96": 0,
    }
    tx = router.functions.exactInputSingle(params).build_transaction({
        "from": account.address,
        "value": amount_in if token_in.lower() == WETH_ADDR.lower() else 0,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 250000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    })
    signed = account.sign_transaction(tx)
    return w3.eth.send_raw_transaction(signed.rawTransaction).hex()
```

### Per-chain DEX recommendation

| Chain | DEX Utama | Aggregator |
|---|---|---|
| Ethereum | Uniswap V3 | 1inch, 0x, Paraswap |
| BSC | PancakeSwap V3 | 1inch, 0x |
| Base | Aerodrome, Uniswap | 1inch, 0x |
| Arbitrum | Uniswap V3, Camelot | 1inch, 0x |
| Polygon | Uniswap V3, QuickSwap | 1inch, 0x |
| Avalanche | Trader Joe | 1inch, 0x |

## Swap di Solana

Pakai **Jupiter** — aggregator de-facto di Solana.

```python
import httpx
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient

JUP = "https://quote-api.jup.ag/v6"

async def jupiter_quote(input_mint, output_mint, amount_lamports, slippage_bps=100):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{JUP}/quote", params={
            "inputMint": input_mint, "outputMint": output_mint,
            "amount": amount_lamports, "slippageBps": slippage_bps,
        })
    return r.json()

async def jupiter_swap(quote, user_pubkey: str):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{JUP}/swap", json={
            "quoteResponse": quote,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "prioritizationFeeLamports": "auto",
        })
    return r.json()["swapTransaction"]  # base64 versioned tx

# Sign & send:
# tx_bytes = base64.b64decode(swap_tx_b64)
# vtx = VersionedTransaction.from_bytes(tx_bytes)
# signed = VersionedTransaction(vtx.message, [keypair])
# await client.send_raw_transaction(bytes(signed))
```

Native SOL mint: `So11111111111111111111111111111111111111112` (wrapped SOL).

## Swap di Sui / Aptos / TON

| Chain | DEX |
|---|---|
| Sui | Cetus, Aftermath, Turbos. Aggregator: 7K |
| Aptos | LiquidSwap, Thala, PancakeSwap-Aptos. Aggregator: Kana Labs, Hippo |
| TON | STON.fi, DeDust |

Pattern-nya sama: panggil aggregator → dapat unsigned tx / instructions → sign dengan keypair user → broadcast. Lihat doc resmi tiap DEX karena SDK-nya beda-beda.

## Sell All / Sell Percentage (Dump)

```python
def sell_token_full(w3, account, token_address: str, sell_via_native=True):
    # 1. get balance
    balance = get_erc20_balance(w3, token_address, account.address)
    if balance == 0:
        return {"status": "no_balance"}

    # 2. quote via aggregator (1inch/0x), token_address → WETH atau native sentinel
    out_token = NATIVE_SENTINEL if sell_via_native else WETH_ADDR
    tx = await build_swap_tx(w3.eth.chain_id, token_address, out_token,
                              balance, account.address, slippage_pct=2.0)

    # 3. approve kalau perlu (aggregator router-nya beda per protocol)
    spender = tx["to"]
    approve_if_needed(w3, account, token_address, spender, balance)

    # 4. execute swap
    return send_tx(w3, account, tx)

def sell_token_percent(w3, account, token_address, pct: float):
    balance = get_erc20_balance(w3, token_address, account.address)
    amount = int(balance * pct / 100)
    # ... lanjutkan seperti di atas dengan amount, bukan balance
```

## Approval Management (Keamanan)

**JANGAN approve `MAX_UINT256` ke router tak dikenal.** Pola aman:

```python
MAX_UINT = 2**256 - 1

def approve_if_needed(w3, account, token, spender, amount, use_exact=True):
    """use_exact=True: approve persis sebanyak amount (lebih aman, butuh gas tiap swap).
       use_exact=False: approve MAX (lebih hemat gas, tapi spender harus trusted)."""
    token_c = w3.eth.contract(address=token, abi=ERC20_ABI)
    current = token_c.functions.allowance(account.address, spender).call()
    if current >= amount:
        return None
    approve_amount = amount if use_exact else MAX_UINT
    tx = token_c.functions.approve(spender, approve_amount).build_transaction({...})
    # sign & send
```

Untuk router resmi Uniswap/1inch/Jupiter: `use_exact=False` boleh demi gas efficiency. Untuk router asing/baru: selalu `use_exact=True`.

## Konfirmasi User Pre-broadcast (template)

```
📋 SWAP PLAN
─────────────
Chain:      Base
From:       0.5 ETH
To:         ≈ 1,234,567 PEPE (slippage 1%, min 1,222,222)
Route:      Uniswap V3 (0.3% fee tier)
Gas est.:   ~$0.42
─────────────
Lanjut? (yes/no)
```

---

## MEV Protection (v4.0)

Swap yang di-broadcast ke **public mempool** empuk buat sandwich/frontrun — searcher lihat tx kamu in-flight lalu ngapit. Untuk swap bernilai signifikan, kirim lewat **private relay** (`scripts/mev.py`).

```python
from mev import send_private_tx
from governor import SpendGovernor, TxIntent

gov = SpendGovernor()
signed = account.sign_transaction(tx)

# governor dulu (pre-flight gate)
intent = TxIntent(account.address, chain_id, "swap", usd_value=usd_est,
                  slippage_pct=slippage, gas_price_wei=tx["gasPrice"], simulated_ok=True)
d = gov.authorize(intent)
if not d.allowed:
    return d.summary()            # JANGAN broadcast

# baru kirim — private
res = send_private_tx(signed.raw_transaction, chain_id,
                      prefer="flashbots", public_w3=w3)  # public_w3 = fallback sadar
if res.status == "sent_private":
    gov.record(intent, res.tx_hash)
```

Aturan main:
- **Jangan auto-resend ke public** kalau private relay nolak — itu balikin kamu ke jalur rawan frontrun. Eskalasi error dulu.
- Private RPC **tidak** melindungi dari honeypot/tax/malicious approval — safety gate token (honeypot.is/GoPlus) tetap jalan terpisah.
- Chain tanpa relay terdaftar (`mev.py` cuma daftarin Ethereum mainnet default) → fallback public **dengan warning**, atau set `HERMES_PRIVATE_RPC`.
- Solana: gak ada private mempool ala EVM — pakai Jito bundle atau prioritization fee tinggi.

Endpoint (verified 2026): Flashbots Protect `rpc.flashbots.net/fast`, MEV Blocker `rpc.mevblocker.io`. Cek docs relay buat dukungan chain terbaru saat implement.
