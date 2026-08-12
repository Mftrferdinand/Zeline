# NFT: Buy & Sell

Reference untuk beli/jual NFT di marketplace utama. Strategi: pakai API marketplace yang punya order book + fulfillment endpoint, jangan reinvent the wheel.

## EVM: OpenSea / Blur / LooksRare

### OpenSea (Seaport protocol)

OpenSea pakai Seaport. Cara termudah: gunakan **OpenSea API v2** untuk fetch listing, lalu fulfill via Seaport.

```python
import httpx

OS_API = "https://api.opensea.io/v2"
HEADERS = {"X-API-KEY": OPENSEA_API_KEY}

async def get_best_listing(chain: str, contract: str, token_id: str):
    """chain: ethereum, base, arbitrum, optimism, polygon, etc."""
    url = f"{OS_API}/orders/{chain}/seaport/listings"
    params = {
        "asset_contract_address": contract,
        "token_ids": token_id,
        "order_by": "eth_price",
        "order_direction": "asc",
        "limit": 1,
    }
    async with httpx.AsyncClient() as c:
        r = await c.get(url, params=params, headers=HEADERS)
    orders = r.json().get("orders", [])
    return orders[0] if orders else None

async def fulfill_listing(order_hash: str, chain: str, fulfiller_addr: str):
    """Returns ready-to-sign transaction data."""
    url = f"{OS_API}/listings/fulfillment_data"
    payload = {
        "listing": {"hash": order_hash, "chain": chain, "protocol_address": SEAPORT_1_6},
        "fulfiller": {"address": fulfiller_addr},
    }
    async with httpx.AsyncClient() as c:
        r = await c.post(url, json=payload, headers=HEADERS)
    return r.json()["fulfillment_data"]["transaction"]
    # → {to: seaport_addr, value, data}
```

Buat list NFT untuk dijual (create listing) butuh sign EIP-712 message Seaport — lebih kompleks. Library `seaport-js` (Node) atau `seaport-py` jauh lebih mudah daripada manual signing. Pattern singkat:

1. Susun OrderParameters (offerer, offer items, consideration items, start/end time, etc.)
2. Sign typed data v4 dengan private key
3. POST signed order ke OpenSea API `/orders/{chain}/seaport/listings`

### Blur (Ethereum only, lebih cocok untuk volume tinggi)

Blur tidak ada API publik penuh — banyak operasi butuh login token via signing challenge. Untuk Hermes-style automation di Blur:

```python
# 1. Get login challenge
# POST https://core-api.prod.blur.io/auth/challenge {"walletAddress": addr}
# → {message: "..."}
# 2. Sign message dengan personal_sign
# 3. POST /auth/login {walletAddress, message, signature, ...}
# → {accessToken: "..."}
# 4. Pakai accessToken untuk endpoint berikutnya
```

Untuk buy: gunakan endpoint `/v1/collections/{slug}/executable-listings` atau aggregator seperti **Reservoir** (mendukung OpenSea + Blur + LooksRare + X2Y2 di satu API).

### Reservoir (recommended — multi-marketplace aggregator EVM)

```python
RESERVOIR = "https://api.reservoir.tools"

async def buy_nft_via_reservoir(chain_subdomain: str, token: str, taker: str):
    """token format: '{contract}:{tokenId}'"""
    url = f"https://api-{chain_subdomain}.reservoir.tools/execute/buy/v7"
    payload = {
        "items": [{"token": token, "quantity": 1}],
        "taker": taker,
        "source": "hermes-agent",
    }
    async with httpx.AsyncClient() as c:
        r = await c.post(url, json=payload, headers={"x-api-key": RESERVOIR_API_KEY})
    steps = r.json()["steps"]
    # steps biasanya: [approve (kalau perlu), sale (signature/tx)]
    # Iterate → kalau "kind": "transaction" → send_tx
    #          → kalau "kind": "signature" → sign & post back
    return steps
```

Reservoir support: Ethereum, Base, Arbitrum, Optimism, Polygon, Zora, BNB, Avalanche, Linea, Scroll, dll.

## Solana: Magic Eden + Tensor

### Magic Eden API

```python
ME = "https://api-mainnet.magiceden.dev/v2"

async def get_me_listings(symbol: str, limit=20):
    """symbol = collection slug, e.g. 'mad_lads'"""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{ME}/collections/{symbol}/listings?limit={limit}")
    return r.json()  # [{tokenMint, price, seller, ...}]

async def get_me_buy_instructions(buyer: str, seller: str, token_mint: str,
                                   price_sol: float, auction_house: str | None = None):
    params = {
        "buyer": buyer, "seller": seller,
        "tokenMint": token_mint, "price": price_sol,
    }
    if auction_house:
        params["auctionHouseAddress"] = auction_house
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{ME}/instructions/buy_now", params=params)
    return r.json()  # txSigned (partially) or instruction data
```

### Tensor (lebih cepat, suka dipakai sniper)

```python
TENSOR = "https://api.mainnet.tensordev.io"  # butuh API key

# Endpoint utama: /api/v1/tx/buy → returns serialized transaction
async def tensor_buy(buyer: str, mint: str, max_price_lamports: int):
    url = f"{TENSOR}/api/v1/tx/buy"
    params = {"buyer": buyer, "mint": mint, "maxPrice": max_price_lamports}
    async with httpx.AsyncClient() as c:
        r = await c.get(url, params=params, headers={"x-tensor-api-key": KEY})
    return r.json()["txs"]  # list of base64 serialized tx
```

Untuk **list NFT for sale** di ME/Tensor: panggil `/instructions/sell` atau `/api/v1/tx/list`, sign, broadcast.

## Sui: BlueMove, Clutchy, TradePort

```python
# TradePort GraphQL (multi-chain: Aptos + Sui)
TRADEPORT = "https://api.indexer.xyz/graphql"
# Untuk buy: query best listing → ambil tx payload → sign dengan keypair Sui → execute
```

## Aptos: Wapal, TradePort, Topaz

Sama seperti Sui — fetch listing via TradePort/Wapal API, dapat payload tx, sign dengan `aptos_sdk.account.Account`, broadcast.

## TON: GetGems, Disintar

```python
# GetGems API: https://api.getgems.io
# Buat tx via TON Connect / native transfer ke contract address NFT sale
```

## Bulk Operations (Floor Sweep)

```python
async def sweep_floor(collection_slug: str, count: int, max_price_eth: float,
                      taker_address: str, chain: str = "ethereum"):
    """Beli N NFT termurah dari sebuah collection."""
    # 1. fetch top N listings
    listings = await get_cheapest_listings(collection_slug, count, chain)
    # 2. filter price <= max_price_eth
    listings = [l for l in listings if l["price_eth"] <= max_price_eth]
    # 3. batch via Reservoir buy (bisa multi-item dalam 1 call /execute/buy/v7)
    items = [{"token": l["token"], "quantity": 1} for l in listings]
    # ... lanjutkan dengan flow Reservoir
```

## Safety Check NFT

Sebelum buy, terutama untuk koleksi baru / belum dikenal:

1. **Verifikasi contract address** — minta user paste contract, jangan tebak dari nama. Banyak collection scam pakai nama mirip.
2. **Cek royalty + fee** — beberapa marketplace skip royalty, beberapa enforce. Hitung total cost = price + marketplace fee + royalty + gas.
3. **Cek metadata uri** — kalau metadata host-nya centralized dan tidak ada di IPFS/Arweave, NFT bisa "hilang" gambarnya kelak. Warn user.
4. **Cek "delegated/wrapped" trickery** — beberapa scam pakai contract wrapper yang transfer NFT ke alamat lain pas dibeli. Reservoir/OpenSea biasanya filter ini, tapi kalau pakai contract langsung, verifikasi.

## Konfirmasi User Template

```
🖼  BUY NFT
─────────────
Collection: Pudgy Penguins #4521
Marketplace: OpenSea (via Seaport)
Price:      2.45 ETH
+ Fees:     0.0612 ETH (2.5% marketplace)
+ Royalty:  0.0612 ETH (2.5%)
+ Gas est.: ~$8
─────────────
Total: 2.5724 ETH
Lanjut? (yes/no)
```
