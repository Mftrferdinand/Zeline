# On-Chain Monitoring

Reference untuk monitor on-chain events: track wallet/transaction realtime, watch contract events, wallet portfolio tracking, dan mempool monitoring (untuk frontrun protection & alpha).

## Daftar Isi

**Basic monitoring:**
1. Wallet Activity Tracker
2. Contract Event Listener
3. Portfolio Tracker (Multi-Chain)
4. Mempool Monitoring (basic)
5. Price & Liquidity Monitoring
6. Liquidation Alert (DeFi Risk)
7. NFT Floor / Whale Monitoring (basic)

**Advanced monitoring** (expanded):

8. Mempool Sniffer (Realtime) — filter framework + frontrun protection + approval drainer detection
9. Smart-Money Tracker — Nansen, Arkham, Dune integration + copy-trade pattern + top-holder watch
10. NFT Whale Alert — Reservoir WS stream + ME polling + floor drop alert
11. Contract Deployment Listener — new token detector + auto-classify + Solana SPL watcher
12. End-to-End Integration Example
13. Storage & Persistence

Lompat ke section yang relevan dengan task.

## 1. Wallet Activity Tracker

Pantau alamat tertentu (whale, smart money) — bisa dipakai untuk:
- Copy trading
- Alert kalau ada wallet user yang dipindahkan dananya
- Tracking sybil hunter atau scam wallet

```python
import asyncio
from web3 import AsyncWeb3

async def watch_wallet_tx(w3_async: AsyncWeb3, watched_addr: str,
                            on_outgoing, on_incoming):
    """Subscribe new blocks, scan tx yang melibatkan watched_addr."""
    sub_id = await w3_async.eth.subscribe("newHeads")
    watched = watched_addr.lower()
    
    async for header in w3_async.socket.process_subscriptions():
        block = await w3_async.eth.get_block(header["params"]["result"]["hash"],
                                               full_transactions=True)
        for tx in block.transactions:
            from_addr = tx["from"].lower() if tx["from"] else None
            to_addr = tx["to"].lower() if tx["to"] else None
            
            if from_addr == watched:
                await on_outgoing(tx, block)
            elif to_addr == watched:
                await on_incoming(tx, block)
```

### Smart Money Tracking via API (Lebih Praktis)

Manual block-scanning lambat. Pakai API yg sudah terindex:

```python
# Nansen API (paid, paling komprehensif)
NANSEN = "https://api.nansen.ai/v1"

# Arkham Intelligence API (free tier ada)
ARKHAM = "https://api.arkhamintelligence.com"

# Dune Analytics — query custom
DUNE = "https://api.dune.com/api/v1"

async def get_smart_money_swaps(token_address: str, chain: str, hours: int = 24):
    """Contoh: query Dune untuk swap whale di token X 24 jam terakhir."""
    # Pakai pre-built Dune query
    async with httpx.AsyncClient(headers={"X-Dune-API-Key": DUNE_KEY}) as c:
        r = await c.get(f"{DUNE}/query/{QUERY_ID}/results")
    return r.json()
```

### Webhook-Based (Recommended untuk Produksi)

Alchemy, Helius, QuickNode punya webhook service yg push event ke endpoint Anda — jauh lebih efisien daripada polling.

```python
# Setup webhook via Alchemy Notify API
async def create_alchemy_webhook(addresses: list[str], webhook_url: str):
    async with httpx.AsyncClient(headers={"X-Alchemy-Token": ALCHEMY_AUTH}) as c:
        r = await c.post("https://dashboard.alchemy.com/api/create-webhook", json={
            "network": "ETH_MAINNET",
            "webhook_type": "ADDRESS_ACTIVITY",
            "addresses": addresses,
            "webhook_url": webhook_url,
        })
    return r.json()

# Lalu di sisi webhook receiver Hermes:
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/webhook/wallet-activity")
async def handle_activity(request: Request):
    data = await request.json()
    # data["event"]["activity"] = list of tx
    for tx in data["event"]["activity"]:
        await handle_wallet_event(tx)
    return {"ok": True}
```

## 2. Contract Event Listener

Listen ke event log spesifik (transfer, swap, mint, dst).

```python
async def listen_transfers(w3_async, token_address: str, on_event):
    """Listen ERC-20 Transfer events untuk token tertentu."""
    transfer_topic = w3_async.keccak(text="Transfer(address,address,uint256)").hex()
    
    sub_id = await w3_async.eth.subscribe("logs", {
        "address": token_address,
        "topics": [transfer_topic],
    })
    
    async for msg in w3_async.socket.process_subscriptions():
        log = msg["params"]["result"]
        from_addr = "0x" + log["topics"][1][-40:]
        to_addr = "0x" + log["topics"][2][-40:]
        amount = int(log["data"], 16)
        await on_event({"from": from_addr, "to": to_addr,
                          "amount": amount, "tx_hash": log["transactionHash"]})
```

### Filter Specific Events

```python
async def listen_uniswap_swaps(w3_async, pool_address: str):
    """Listen Uniswap V3 Swap events di pool tertentu."""
    swap_topic = w3_async.keccak(
        text="Swap(address,address,int256,int256,uint160,uint128,int24)"
    ).hex()
    
    sub_id = await w3_async.eth.subscribe("logs", {
        "address": pool_address,
        "topics": [swap_topic],
    })
    
    async for msg in w3_async.socket.process_subscriptions():
        log = msg["params"]["result"]
        # Decode amounts dari data field (int256 signed)
        # amount0, amount1, sqrtPriceX96, liquidity, tick
        # ...
```

### Historical Event Query

```python
def get_historical_transfers(w3, token_address: str, from_block: int, to_block: int,
                              wallet: str | None = None):
    """Fetch historical Transfer events. Batch untuk avoid RPC limit (biasanya 10k blocks)."""
    BATCH = 10_000
    events = []
    
    transfer_filter = w3.eth.filter({
        "address": token_address,
        "fromBlock": from_block, "toBlock": min(from_block + BATCH, to_block),
        "topics": [w3.keccak(text="Transfer(address,address,uint256)").hex()] + (
            [None, "0x" + wallet.removeprefix("0x").zfill(64)] if wallet else []
        ),
    })
    events.extend(transfer_filter.get_all_entries())
    # ... loop sampai to_block
    return events
```

## 3. Portfolio Tracker (Multi-Chain)

Aggregate holdings across chain — pakai API biar tidak perlu RPC call per token.

```python
# Zerion API (EVM multi-chain, gratis tier ada)
ZERION = "https://api.zerion.io/v1"

async def get_portfolio_zerion(address: str) -> dict:
    async with httpx.AsyncClient(headers={"Authorization": f"Basic {ZERION_KEY_B64}"}) as c:
        r = await c.get(f"{ZERION}/wallets/{address}/positions",
                        params={"filter[chain_ids]": "ethereum,arbitrum,base,optimism,polygon"})
    return r.json()

# DeBank API (EVM only, lebih akurat untuk DeFi position)
async def get_portfolio_debank(address: str) -> dict:
    async with httpx.AsyncClient(headers={"AccessKey": DEBANK_KEY}) as c:
        r = await c.get(f"https://pro-openapi.debank.com/v1/user/all_complex_protocol_list",
                        params={"id": address})
    return r.json()

# Birdeye API (Solana)
async def get_portfolio_solana(address: str) -> dict:
    async with httpx.AsyncClient(headers={"X-API-KEY": BIRDEYE_KEY}) as c:
        r = await c.get(f"https://public-api.birdeye.so/v1/wallet/token_list?wallet={address}")
    return r.json()
```

### Aggregate Cross-VM Portfolio

```python
async def get_full_portfolio(wallets: dict[str, str]) -> dict:
    """wallets: {chain_family: address}, e.g. {'evm': '0x...', 'solana': '...', 'sui': '...'}"""
    results = {}
    if "evm" in wallets:
        results["evm"] = await get_portfolio_zerion(wallets["evm"])
    if "solana" in wallets:
        results["solana"] = await get_portfolio_solana(wallets["solana"])
    if "sui" in wallets:
        # pakai BlockVision or SuiVision API
        pass
    
    # Total USD value
    total_usd = sum(p["total_usd"] for p in results.values())
    return {"total_usd": total_usd, "per_chain": results}
```

## 4. Mempool Monitoring

Watch pending tx (sebelum di-include ke block). Use cases:
- **Frontrun protection**: kalau ada bot mau frontrun swap user, tahan tx
- **Alpha hunting**: deteksi whale buy sebelum confirmed
- **MEV detection**: spot sandwich attack target

```python
async def watch_mempool(w3_async, on_pending):
    """Subscribe pending transactions (butuh node yg support, biasanya Alchemy/Infura premium)."""
    sub_id = await w3_async.eth.subscribe("newPendingTransactions", True)   # True = full tx
    
    async for msg in w3_async.socket.process_subscriptions():
        tx = msg["params"]["result"]
        await on_pending(tx)
```

### Filter Mempool: Buy Tx untuk Token Tertentu

```python
async def watch_buys(w3_async, target_token: str, min_eth: float = 1.0):
    """Trigger callback kalau ada pending tx buy token X dengan amount > min_eth."""
    async def on_pending(tx):
        if not tx.get("to"):
            return
        # Check kalau tx ke Uniswap router
        if tx["to"].lower() not in [r.lower() for r in UNI_ROUTERS]:
            return
        # Decode input, cek path mengandung target_token
        if target_token.lower() not in tx["input"].lower():
            return
        value_eth = int(tx.get("value", "0x0"), 16) / 1e18
        if value_eth >= min_eth:
            print(f"WHALE BUY: {value_eth} ETH from {tx['from']}")
    
    await watch_mempool(w3_async, on_pending)
```

### Private Mempool (untuk Sniper)

Public mempool dilihat semua bot. Untuk eksekusi yg tidak bisa difrontrun:

```python
# Flashbots Protect (Ethereum mainnet)
PROTECT_RPC = "https://rpc.flashbots.net"

# bloXroute Trader API (multi-chain, lower latency)
# Eden Network

# Solana — Jito bundle untuk transaksi yang bypass mempool publik
JITO_BUNDLE_API = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
```

## 5. Price & Liquidity Monitoring

```python
# DexScreener (gratis, multi-chain, near-realtime)
async def dexscreener_pair(chain: str, pair_address: str):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair_address}")
    return r.json()
    # → priceUsd, liquidity.usd, volume.h24, txns.h24, priceChange, etc.

# CoinGecko (untuk token established)
async def coingecko_price(coingecko_id: str):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": coingecko_id, "vs_currencies": "usd,btc,eth"})
    return r.json()

# Alert kalau price/liquidity berubah drastis
async def price_alert(token_address: str, chain: str,
                       threshold_pct: float = 10, poll_seconds: int = 30):
    last_price = None
    while True:
        data = await dexscreener_pair(chain, get_main_pair(token_address))
        price = float(data["pair"]["priceUsd"])
        if last_price is not None:
            change = (price - last_price) / last_price * 100
            if abs(change) >= threshold_pct:
                yield {"price": price, "change_pct": change, "ts": time.time()}
        last_price = price
        await asyncio.sleep(poll_seconds)
```

## 6. Liquidation Alert (DeFi Risk)

Critical untuk user yang punya borrow position:

```python
async def monitor_liquidation_risk(w3, account, alert_threshold: float = 1.3):
    """Cek health factor Aave secara berkala, alert kalau mendekati 1.0 (liquidate)."""
    while True:
        health = await aave_health(w3, account)
        if health["health_factor"] < alert_threshold:
            yield {"severity": "WARN" if health["health_factor"] > 1.1 else "CRITICAL",
                   "health": health["health_factor"], 
                   "action_needed": "repay or add collateral"}
        await asyncio.sleep(60)
```

## 7. NFT Floor / Whale Monitoring

```python
# Reservoir untuk EVM NFT
async def watch_collection_floor(slug: str, chain: str = "ethereum",
                                   target_floor_eth: float = None):
    base = RESERVOIR_BASE_URL[chain]
    last_floor = None
    while True:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{base}/collections/v7", 
                            params={"slug": slug},
                            headers={"x-api-key": RESERVOIR_API_KEY})
        floor = r.json()["collections"][0]["floorAsk"]["price"]["amount"]["native"]
        
        if last_floor and abs(floor - last_floor) / last_floor > 0.05:
            yield {"floor": floor, "change": floor - last_floor}
        if target_floor_eth and floor <= target_floor_eth:
            yield {"floor": floor, "trigger": "TARGET_REACHED"}
        last_floor = floor
        await asyncio.sleep(30)
```

## Hermes Alert Channels

Output dari monitor → kirim ke channel user:

```python
async def notify_user(channel: str, message: str, severity: str = "info"):
    """channel: 'telegram'|'discord'|'email'|'webhook'"""
    if channel == "telegram":
        await send_telegram(TG_BOT_TOKEN, TG_CHAT_ID, message)
    elif channel == "discord":
        await send_discord_webhook(DISCORD_WEBHOOK, message)
    # dst.
```

## Best Practices

1. **Pakai WebSocket / Webhook, bukan polling.** RPC polling = waste of credits, lambat.
2. **Cache aggressively.** Token metadata (decimals, symbol) tidak berubah — simpan di local cache.
3. **Rate limit handling.** Sebagian besar API gratis punya limit. Implement backoff + multiple keys rotation.
4. **Reorg awareness.** Tx yang sudah confirmed bisa di-reorg di chain seperti Polygon/BSC. Tunggu 12+ block sebelum trust state.
5. **Multi-RPC failover.** Punya minimal 2 RPC per chain — kalau satu down, switch.

```python
class RPCRouter:
    def __init__(self, urls: list[str]):
        self.urls = urls
        self.idx = 0
    
    async def call(self, method: str, params: list):
        for _ in range(len(self.urls)):
            try:
                return await self._call_url(self.urls[self.idx], method, params)
            except Exception:
                self.idx = (self.idx + 1) % len(self.urls)
        raise RuntimeError("all RPCs failed")
```

---

# Advanced Monitoring (Expanded)

Bagian ini cover monitoring lanjutan: realtime mempool sniffer, smart-money tracker via data provider, NFT whale alert, dan contract deployment listener. Pakai untuk alpha hunting, frontrun protection, atau early-detection scam.

## 8. Mempool Sniffer (Realtime)

Mempool = pending tx pool sebelum di-mine. Yang bisa dideteksi:
- Whale buy/sell sebelum confirmed
- Approval/permit yang mencurigakan
- MEV bot activity (sandwich, frontrun)
- New token deployment / liquidity add

### Source Mempool

| Source | Akses | Latency | Catatan |
|---|---|---|---|
| Public mempool via Alchemy WS `newPendingTransactions` | gratis tier ada | ~50–200ms | terbatas, tidak semua tx |
| Public mempool via QuickNode `newPendingTransactionsFiltered` | gratis tier ada | ~50–200ms | bisa filter |
| Blocknative Mempool API | paid, free trial | ~30ms | terlengkap, multi-chain |
| bloXroute BDN | paid | <10ms | digunakan MEV searcher pro |
| Solana — Helius `enhancedTransactions` WS | gratis tier | <100ms | Solana native |
| Geth/Erigon node sendiri | self-host | <5ms | paling cepat, paling repot |

### Sniffer dengan Filter

```python
import asyncio
from web3 import AsyncWeb3, WebSocketProvider
from typing import Callable, Awaitable

class MempoolSniffer:
    def __init__(self, ws_rpc_url: str, filters: list[dict]):
        """filters: list of dict, each can have:
        - to: contract address yang dimonitor
        - from: address yang dimonitor
        - selector: function selector (4 byte hex, e.g. '0xa9059cbb' for transfer)
        - min_value_eth: minimum value
        - custom: callable(tx) -> bool
        """
        self.url = ws_rpc_url
        self.filters = filters
        self.callbacks: list[Callable] = []

    def on_match(self, callback: Callable[[dict], Awaitable]):
        self.callbacks.append(callback)

    def _matches(self, tx: dict, f: dict) -> bool:
        if "to" in f and (tx.get("to") or "").lower() != f["to"].lower():
            return False
        if "from" in f and tx.get("from", "").lower() != f["from"].lower():
            return False
        if "selector" in f:
            data = tx.get("input") or tx.get("data") or "0x"
            if not data.lower().startswith(f["selector"].lower()):
                return False
        if "min_value_eth" in f:
            value = tx.get("value", 0)
            value_int = int(value, 16) if isinstance(value, str) else int(value)
            if value_int < int(f["min_value_eth"] * 1e18):
                return False
        if "custom" in f and not f["custom"](tx):
            return False
        return True

    async def run(self):
        async with AsyncWeb3(WebSocketProvider(self.url)) as w3:
            sub_id = await w3.eth.subscribe("newPendingTransactions", True)
            async for msg in w3.socket.process_subscriptions():
                tx = msg["params"]["result"]
                if not isinstance(tx, dict):
                    continue
                for f in self.filters:
                    if self._matches(tx, f):
                        for cb in self.callbacks:
                            await cb(tx, f)
                        break
```

### Pattern: Frontrun Protection untuk User Swap

```python
async def watch_for_frontrunners(token_address: str, user_addr: str,
                                   pending_tx_hash: str):
    """Setelah Hermes submit swap, watch mempool untuk MEV bot yang try frontrun.

    Kalau ada bot send tx ke router yang sama dengan priority gas LEBIH TINGGI
    dan timestamp lebih awal, alert user — kemungkinan akan kena sandwich.
    """
    sniffer = MempoolSniffer(WS_RPC, filters=[{
        "to": UNI_V2_ROUTER,
        "selector": "0x38ed1739",   # swapExactTokensForTokens
        "custom": lambda tx: token_address.lower() in tx.get("input", "").lower(),
    }])

    @sniffer.on_match
    async def alert(tx, f):
        if tx["hash"] == pending_tx_hash:
            return  # itu tx kita sendiri
        # Heuristic: ada tx baru ke router yang sama dengan gas lebih tinggi
        await notify("MEV bot suspected, consider cancel & retry via Flashbots")

    await sniffer.run()
```

### Pattern: Detect Approval Drainer Sebelum User Sign

```python
DANGER_SELECTORS = {
    "0x095ea7b3": "approve(address,uint256)",
    "0xa22cb465": "setApprovalForAll(address,bool)",
    "0xd505accf": "permit(...)",          # ERC-2612 permit
    "0x2b67b570": "permitBatch(...)",     # Permit2
}

async def watch_user_wallet_approvals(user_addr: str):
    sniffer = MempoolSniffer(WS_RPC, filters=[
        {"from": user_addr, "selector": sel}
        for sel in DANGER_SELECTORS
    ])

    @sniffer.on_match
    async def on_approval(tx, f):
        # Decode siapa spender + amount
        await notify(f"🚨 Pending approval dari wallet Anda! "
                       f"Function: {DANGER_SELECTORS[f['selector']]}. "
                       f"Verifikasi spender sebelum confirmed.")

    await sniffer.run()
```

## 9. Smart-Money Tracker (Nansen / Arkham / Dune)

Smart money = wallet yang historisnya profitable (early DeFi, OG NFT trader, dst). Track mereka untuk alpha.

### Nansen API (Paling Akurat, Paid)

```python
NANSEN_API = "https://api.nansen.ai/v1"

class NansenClient:
    def __init__(self, api_key: str):
        self.headers = {"NANSEN-API-KEY": api_key}

    async def get_smart_money_holdings(self, label: str = "Smart Money",
                                          chain: str = "ethereum") -> list[dict]:
        """label: 'Smart Money', 'Smart NFT Trader', 'OG Whale', 'Bridge', dst."""
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.get(f"{NANSEN_API}/wallet-labels/holdings",
                            params={"label": label, "chain": chain})
        return r.json()

    async def get_token_flow(self, token: str, chain: str = "ethereum",
                               hours: int = 24) -> dict:
        """Flow in/out untuk token, broken down by wallet label."""
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.get(f"{NANSEN_API}/tokens/{chain}/{token}/flows",
                            params={"hours": hours})
        return r.json()

    async def get_token_god_mode(self, token: str, chain: str = "ethereum") -> dict:
        """Lengkap: top holders, smart money %, recent buys/sells, etc."""
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.get(f"{NANSEN_API}/tokens/{chain}/{token}/god-mode")
        return r.json()
```

### Arkham Intel API (Free Tier Ada)

```python
ARKHAM_API = "https://api.arkhamintelligence.com"

class ArkhamClient:
    def __init__(self, api_key: str):
        self.headers = {"API-Key": api_key}

    async def get_entity(self, address: str, chain: str = "ethereum") -> dict:
        """Resolve address → entity name (Binance, Jump Trading, individu labeled, dst)."""
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.get(f"{ARKHAM_API}/intelligence/address/{address}",
                            params={"chain": chain})
        return r.json()

    async def get_transfers(self, address: str, chain: str = "ethereum",
                              limit: int = 100) -> list[dict]:
        """Recent transfer history untuk wallet, sudah labeled per counter-party."""
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.get(f"{ARKHAM_API}/transfers",
                            params={"base": address, "chains": chain, "limit": limit})
        return r.json()
```

### Dune Analytics (Custom Query — Paling Fleksibel)

```python
DUNE_API = "https://api.dune.com/api/v1"

class DuneClient:
    def __init__(self, api_key: str):
        self.headers = {"X-Dune-API-Key": api_key}

    async def execute_query(self, query_id: int, params: dict | None = None) -> str:
        """Trigger execution, return execution_id (cek hasil belakangan)."""
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.post(f"{DUNE_API}/query/{query_id}/execute",
                             json={"query_parameters": params or {}})
        return r.json()["execution_id"]

    async def get_results(self, query_id: int) -> dict:
        """Get latest cached results (gratis, fast)."""
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.get(f"{DUNE_API}/query/{query_id}/results")
        return r.json()
```

### Pattern: Copy-Trade Smart Money

```python
async def copy_trade_smart_money(smart_addr: str, hermes_wallet,
                                    max_buy_per_token_eth: float = 0.1,
                                    min_smart_buy_eth: float = 5.0):
    """Watch smart wallet, mirror buy mereka di Hermes wallet (ukuran kecil)."""
    arkham = ArkhamClient(os.environ["ARKHAM_API_KEY"])

    # Cara cepat: poll Arkham transfers tiap N detik
    seen = set()
    while True:
        transfers = await arkham.get_transfers(smart_addr, limit=20)
        for tx in transfers:
            if tx["hash"] in seen:
                continue
            seen.add(tx["hash"])

            # Smart wallet adalah RECEIVER → mereka BUY
            if tx["to"]["address"].lower() != smart_addr.lower():
                continue
            # Bukan stablecoin / native
            if tx["tokenSymbol"] in ("USDC", "USDT", "DAI", "ETH", "WETH"):
                continue

            token = tx["tokenAddress"]
            amount_eth = tx.get("ethValue", 0)
            if amount_eth < min_smart_buy_eth:
                continue

            # Hermes mirror buy (jumlah kecil)
            print(f"COPY: smart bought {tx['tokenSymbol']} ({amount_eth} ETH worth)")
            # await swap_evm(w3, hermes_wallet, NATIVE, token, max_buy_per_token_wei)

        await asyncio.sleep(15)
```

### Pattern: Track Top Holder Movements

```python
async def watch_top_holders(token_address: str, chain: str = "ethereum",
                              alert_threshold_pct: float = 5.0):
    """Alert kalau top 10 holder bergerakkan > N% holdings mereka."""
    # 1. fetch top holders awal
    nansen = NansenClient(os.environ["NANSEN_API_KEY"])
    god = await nansen.get_token_god_mode(token_address, chain)
    top_holders = god["topHolders"][:10]

    baseline = {h["address"]: h["balance"] for h in top_holders}

    while True:
        await asyncio.sleep(300)   # 5 menit
        god = await nansen.get_token_god_mode(token_address, chain)
        current = {h["address"]: h["balance"] for h in god["topHolders"][:10]}

        for addr, old_bal in baseline.items():
            new_bal = current.get(addr, 0)
            if old_bal == 0:
                continue
            change_pct = (new_bal - old_bal) / old_bal * 100
            if abs(change_pct) >= alert_threshold_pct:
                action = "SOLD" if change_pct < 0 else "BOUGHT MORE"
                await notify(f"🐋 Top holder {addr[:10]}... {action} "
                              f"{abs(change_pct):.1f}% of position")
        baseline = current
```

## 10. NFT Whale Alert

Track aktivitas whale di NFT — useful untuk floor signal, alpha collection, atau frontrun bid.

### Pakai Reservoir Event Stream

```python
RESERVOIR_WS = "wss://ws.reservoir.tools"

async def watch_nft_whale_activity(min_price_eth: float = 5.0,
                                      collections: list[str] | None = None):
    """Stream semua sale/transfer event Reservoir, filter whale-level."""
    import websockets, json

    async with websockets.connect(
        f"{RESERVOIR_WS}?api_key={os.environ['RESERVOIR_API_KEY']}"
    ) as ws:
        # Subscribe to sales channel
        await ws.send(json.dumps({
            "type": "subscribe",
            "event": "sale.created",
            "filters": {"contracts": collections} if collections else {},
        }))

        async for msg in ws:
            data = json.loads(msg)
            if data["event"] != "sale.created":
                continue
            sale = data["data"]
            price_eth = sale["price"]["amount"]["native"]
            if price_eth < min_price_eth:
                continue

            await notify(
                f"🐳 NFT WHALE SALE\n"
                f"Collection: {sale['token']['collection']['name']}\n"
                f"Token: #{sale['token']['tokenId']}\n"
                f"Price: {price_eth} ETH\n"
                f"Buyer: {sale['to'][:10]}...\n"
                f"Seller: {sale['from'][:10]}..."
            )
```

### Alternatif: Polling Reservoir Sales API

```python
async def poll_collection_sales(collection_slug: str,
                                  min_price_eth: float = 1.0,
                                  poll_sec: int = 60):
    seen = set()
    last_check = int(time.time())
    while True:
        async with httpx.AsyncClient(
            headers={"x-api-key": os.environ["RESERVOIR_API_KEY"]}
        ) as c:
            r = await c.get(
                "https://api.reservoir.tools/sales/v6",
                params={"collection": collection_slug, "limit": 100,
                          "startTimestamp": last_check}
            )
        for sale in r.json().get("sales", []):
            if sale["id"] in seen:
                continue
            seen.add(sale["id"])
            if sale["price"]["amount"]["native"] >= min_price_eth:
                yield sale
        last_check = int(time.time())
        await asyncio.sleep(poll_sec)
```

### Pattern: Floor-Below-Threshold Alert

```python
async def floor_drop_alert(collection_slug: str, target_floor_eth: float,
                             notifier: "Notifier"):
    """Buzz Hermes kalau floor turun di bawah target — buy opportunity."""
    while True:
        async with httpx.AsyncClient(
            headers={"x-api-key": os.environ["RESERVOIR_API_KEY"]}
        ) as c:
            r = await c.get(
                "https://api.reservoir.tools/collections/v7",
                params={"slug": collection_slug},
            )
        col = r.json()["collections"][0]
        floor = col["floorAsk"]["price"]["amount"]["native"]
        if floor <= target_floor_eth:
            await notifier.send(
                f"🎯 {col['name']} floor hits {floor} ETH (target {target_floor_eth})",
                severity="warn"
            )
            return  # one-shot
        await asyncio.sleep(60)
```

### Magic Eden Whale Tracker (Solana)

```python
async def watch_solana_nft_whales(min_price_sol: float = 50):
    ME = "https://api-mainnet.magiceden.dev/v2"
    seen = set()
    while True:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{ME}/activities",
                            params={"type": "buyNow", "limit": 100})
        for act in r.json():
            if act["signature"] in seen:
                continue
            seen.add(act["signature"])
            if act["price"] >= min_price_sol:
                yield act
        await asyncio.sleep(30)
```

## 11. Contract Deployment Listener

Watch deployment contract baru di chain — useful untuk:
- Spot new token / NFT launch (sebelum di-list aggregator)
- Detect rug-pull contract pattern
- Track deployer wallet known scammer

### EVM: Watch Block untuk `tx.to == None`

`tx.to == None` artinya contract creation tx.

```python
async def watch_new_contracts(w3_async: AsyncWeb3,
                                deployer_filter: list[str] | None = None
                                ) -> AsyncIterator[dict]:
    """Stream block, yield setiap contract baru yang dideploy.

    deployer_filter: kalau diisi, hanya yield deployment dari address ini
                     (track known deployer scam / known team).
    """
    deployers = {d.lower() for d in deployer_filter} if deployer_filter else None
    sub_id = await w3_async.eth.subscribe("newHeads")

    async for header in w3_async.socket.process_subscriptions():
        block = await w3_async.eth.get_block(
            header["params"]["result"]["hash"], full_transactions=True
        )
        for tx in block.transactions:
            if tx.get("to") is not None:
                continue   # bukan contract creation
            deployer = tx["from"].lower()
            if deployers and deployer not in deployers:
                continue

            # Get receipt untuk contract address
            receipt = await w3_async.eth.get_transaction_receipt(tx["hash"])
            yield {
                "deployer": tx["from"],
                "contract": receipt["contractAddress"],
                "tx_hash": tx["hash"].hex(),
                "block": int(block["number"]),
                "ts": int(block["timestamp"]),
                "input_bytecode_size": len(tx.get("input", "")) // 2,
            }
```

### Klasifikasi Contract Baru

```python
ERC20_FN_SIGS = ["0xa9059cbb", "0x70a08231", "0x18160ddd",
                 "0x95d89b41", "0x06fdde03"]   # transfer, balanceOf, totalSupply, symbol, name
ERC721_FN_SIGS = ["0x70a08231", "0x6352211e", "0x42842e0e"]   # balanceOf, ownerOf, safeTransferFrom

async def classify_contract(w3, contract_addr: str) -> dict:
    """Tebak jenis contract dari bytecode."""
    bytecode = w3.eth.get_code(contract_addr).hex()

    matches = {
        "ERC-20 token": sum(1 for sig in ERC20_FN_SIGS if sig[2:] in bytecode),
        "ERC-721 NFT": sum(1 for sig in ERC721_FN_SIGS if sig[2:] in bytecode),
    }
    best = max(matches.items(), key=lambda x: x[1])
    return {
        "guess": best[0] if best[1] >= 3 else "unknown",
        "scores": matches,
        "bytecode_size": len(bytecode) // 2,
    }
```

### Pattern: New Token Launch Detector

```python
async def detect_new_token_launches(w3_async, on_launch):
    """Watch contract deployment, alert kalau ada ERC-20 dengan likuiditas dalam 5 menit."""
    async for deploy in watch_new_contracts(w3_async):
        contract = deploy["contract"]
        # Wait 5 menit, lalu cek apakah ada pair Uniswap
        asyncio.create_task(check_liquidity_added(w3_async, contract, deploy, on_launch))

async def check_liquidity_added(w3_async, contract, deploy_info, on_launch):
    await asyncio.sleep(300)
    # Cek factory.getPair(contract, WETH)
    factory = w3_async.eth.contract(address=UNI_V2_FACTORY, abi=FACTORY_ABI)
    pair = await factory.functions.getPair(contract, WETH_ADDR).call()
    if int(pair, 16) != 0:
        # Ada pair — token live
        await on_launch({**deploy_info, "pair": pair})
```

### Solana: Listen Token Program

```python
async def watch_new_spl_tokens(helius_ws_url: str, on_token):
    """Detect new SPL token via Helius enhanced WS."""
    import websockets, json
    async with websockets.connect(helius_ws_url) as ws:
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "transactionSubscribe",
            "params": [
                {"accountInclude": ["TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"]},
                {"transactionDetails": "full",
                 "showRewards": False, "encoding": "jsonParsed"},
            ],
        }))
        async for msg in ws:
            data = json.loads(msg)
            # Parse: instruction InitializeMint → token baru
            ...
```

## 12. End-to-End Integration Example

Gabungkan semua jadi alpha bot Hermes:

```python
import asyncio
from .monitoring import (
    MempoolSniffer, NansenClient, watch_new_contracts,
    Notifier, watch_top_holders,
)

async def hermes_alpha_bot():
    notifier = Notifier(
        telegram=(os.environ["TG_BOT"], os.environ["TG_CHAT"]),
        discord_webhook=os.environ.get("DISCORD_WH"),
    )

    # Task 1: detect new token launch + alert
    async def watch_launches():
        w3_async = await get_async_w3()
        async for deploy in watch_new_contracts(w3_async):
            cls = await classify_contract(w3_async, deploy["contract"])
            if cls["guess"] == "ERC-20 token":
                # Tunggu pair, lalu safety check
                asyncio.create_task(check_and_alert(deploy, notifier))

    # Task 2: track smart money buys (Nansen)
    async def watch_smart_money():
        nansen = NansenClient(os.environ["NANSEN_API_KEY"])
        seen_buys = set()
        while True:
            flows = await nansen.get_smart_money_holdings("Smart Money", "ethereum")
            for f in flows.get("recentBuys", []):
                if f["txHash"] in seen_buys:
                    continue
                seen_buys.add(f["txHash"])
                await notifier.send(
                    f"🧠 Smart money buy: {f['token']} "
                    f"by {f['walletLabel']} ({f['amountUsd']} USD)",
                    severity="info",
                )
            await asyncio.sleep(60)

    # Task 3: watch user wallet untuk approval suspect
    user_addr = os.environ["USER_WALLET"]
    sniffer = MempoolSniffer(WS_RPC, filters=[
        {"from": user_addr, "selector": "0x095ea7b3"},   # approve()
        {"from": user_addr, "selector": "0xa22cb465"},   # setApprovalForAll()
    ])

    @sniffer.on_match
    async def alert_approval(tx, f):
        await notifier.send(
            f"🚨 Approval tx pending dari wallet Anda! Verify spender: {tx['input'][34:74]}",
            severity="critical",
        )

    # Run semua paralel
    await asyncio.gather(
        watch_launches(),
        watch_smart_money(),
        sniffer.run(),
    )
```

## 13. Storage & Persistence untuk Monitoring

Monitoring task perlu state persistence (last block scanned, seen events, dst):

```python
import sqlite3
from pathlib import Path

class MonitorState:
    """Track last-seen state untuk deduplication & resume."""

    def __init__(self, db_path: Path = Path.home() / ".hermes" / "monitor.db"):
        db_path.parent.mkdir(exist_ok=True, parents=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS cursor (
                topic TEXT PRIMARY KEY, value TEXT, updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS seen_events (
                topic TEXT, event_id TEXT, ts INTEGER,
                PRIMARY KEY(topic, event_id)
            );
        """)

    def set_cursor(self, topic: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO cursor VALUES (?, ?, ?)",
            (topic, value, int(time.time())))
        self.conn.commit()

    def get_cursor(self, topic: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM cursor WHERE topic = ?", (topic,)).fetchone()
        return row[0] if row else None

    def mark_seen(self, topic: str, event_id: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_events VALUES (?, ?, ?)",
            (topic, event_id, int(time.time())))
        self.conn.commit()

    def is_seen(self, topic: str, event_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen_events WHERE topic=? AND event_id=?",
            (topic, event_id)).fetchone()
        return row is not None
```

## Konfirmasi User Template

```
🚨 ALERT — Pending Approval
─────────────
Wallet:     0xYourWallet...
Function:   setApprovalForAll(spender, true)
Spender:    0xSus...Contract (unverified, deployed 2h ago)
Risk:       HIGH — ini biasanya drainer pattern
─────────────
Mau cancel tx ini? Hermes bisa kirim replacement tx dengan gas lebih tinggi.
(cancel / let-it-through / show-details)
```

```
🧠 SMART MONEY BUY DETECTED
─────────────
Wallet:     0x...abc (Nansen label: "Smart Trader")
Token:      $TICKER (0xContract...)
Amount:     12.5 ETH ($31,250)
Time:       2 menit lalu
DexScreener: $0.00021, MC $2.1M, Liq $400k
Safety:     ✓ honeypot.is clean, ✓ GoPlus clean
─────────────
Mau mirror buy? Suggest 0.5 ETH (4% of smart wallet amount).
(yes / custom-amount / skip)
```
