"""
hermes/monitoring_advanced.py — Advanced on-chain monitoring engine.

Implementasi konkret dari section 8–13 di references/monitoring.md:
- MempoolSniffer (realtime, filterable)
- NansenClient / ArkhamClient / DuneClient (smart money data provider)
- NFT whale via Reservoir WS + polling
- Contract deployment listener + bytecode classifier
- MonitorState (SQLite persistence)
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx
from web3 import AsyncWeb3, Web3


# ─────────────────────────── 8. MEMPOOL SNIFFER ───────────────────────────

@dataclass
class MempoolFilter:
    """Filter spec untuk mempool sniffer."""
    to: Optional[str] = None
    from_: Optional[str] = None
    selector: Optional[str] = None       # 4-byte function selector, e.g. '0xa9059cbb'
    min_value_wei: int = 0
    custom: Optional[Callable[[dict], bool]] = None
    tag: str = ""                          # label untuk routing callback

    def matches(self, tx: dict) -> bool:
        if self.to and (tx.get("to") or "").lower() != self.to.lower():
            return False
        if self.from_ and (tx.get("from") or "").lower() != self.from_.lower():
            return False
        if self.selector:
            data = (tx.get("input") or tx.get("data") or "0x").lower()
            if not data.startswith(self.selector.lower()):
                return False
        if self.min_value_wei > 0:
            value = tx.get("value", 0)
            value_int = int(value, 16) if isinstance(value, str) and value.startswith("0x") \
                else int(value)
            if value_int < self.min_value_wei:
                return False
        if self.custom and not self.custom(tx):
            return False
        return True


# Known dangerous selectors (approval / drainer patterns)
DANGER_SELECTORS = {
    "0x095ea7b3": "approve(address,uint256)",
    "0xa22cb465": "setApprovalForAll(address,bool)",
    "0xd505accf": "permit(address,address,uint256,uint256,uint8,bytes32,bytes32)",
    "0x2b67b570": "permit(...) Permit2",
    "0x36568abe": "transferFrom(address,address,uint256)",   # sering dipakai drainer setelah approve
}


class MempoolSniffer:
    """Realtime mempool sniffer with filter dispatch.

    Usage:
        sniffer = MempoolSniffer(ws_url)
        sniffer.add_filter(MempoolFilter(to=ROUTER, selector="0x38ed1739", tag="swap"))
        sniffer.on("swap", on_swap_callback)
        await sniffer.run()
    """

    def __init__(self, ws_rpc_url: str):
        self.url = ws_rpc_url
        self.filters: list[MempoolFilter] = []
        self.callbacks: dict[str, list[Callable]] = {}
        self._stop = False

    def add_filter(self, f: MempoolFilter):
        self.filters.append(f)
        return self

    def on(self, tag: str, callback: Callable[[dict, MempoolFilter], Awaitable]):
        self.callbacks.setdefault(tag, []).append(callback)
        return self

    def stop(self):
        self._stop = True

    async def run(self):
        # Lazy import — beberapa env tidak punya WS extras terpasang
        from web3.providers.persistent import WebSocketProvider

        async with AsyncWeb3(WebSocketProvider(self.url)) as w3:
            await w3.eth.subscribe("newPendingTransactions", True)
            async for msg in w3.socket.process_subscriptions():
                if self._stop:
                    break
                tx = msg.get("params", {}).get("result")
                if not isinstance(tx, dict):
                    continue
                for f in self.filters:
                    if not f.matches(tx):
                        continue
                    for cb in self.callbacks.get(f.tag, []):
                        try:
                            await cb(tx, f)
                        except Exception as e:
                            print(f"[MempoolSniffer] callback error ({f.tag}): {e}")
                    break  # first match wins; pakai tag berbeda kalau perlu multiple


def make_approval_watcher(user_addr: str) -> MempoolSniffer:
    """Helper: watch SEMUA approval/permit dari user wallet."""
    return MempoolSniffer.__new__(MempoolSniffer)   # placeholder; pakai di tempat panggil dengan URL


# ─────────────────────────── 9. SMART MONEY ───────────────────────────

class NansenClient:
    BASE = "https://api.nansen.ai/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("NANSEN_API_KEY")
        if not self.api_key:
            raise RuntimeError("NANSEN_API_KEY required")
        self.headers = {"NANSEN-API-KEY": self.api_key}

    async def smart_money_holdings(self, label: str = "Smart Money",
                                      chain: str = "ethereum") -> dict:
        async with httpx.AsyncClient(headers=self.headers, timeout=15) as c:
            r = await c.get(f"{self.BASE}/wallet-labels/holdings",
                            params={"label": label, "chain": chain})
        r.raise_for_status()
        return r.json()

    async def token_flow(self, token: str, chain: str = "ethereum",
                          hours: int = 24) -> dict:
        async with httpx.AsyncClient(headers=self.headers, timeout=15) as c:
            r = await c.get(f"{self.BASE}/tokens/{chain}/{token}/flows",
                            params={"hours": hours})
        r.raise_for_status()
        return r.json()

    async def token_god_mode(self, token: str, chain: str = "ethereum") -> dict:
        async with httpx.AsyncClient(headers=self.headers, timeout=15) as c:
            r = await c.get(f"{self.BASE}/tokens/{chain}/{token}/god-mode")
        r.raise_for_status()
        return r.json()


class ArkhamClient:
    BASE = "https://api.arkhamintelligence.com"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ARKHAM_API_KEY")
        if not self.api_key:
            raise RuntimeError("ARKHAM_API_KEY required")
        self.headers = {"API-Key": self.api_key}

    async def get_entity(self, address: str, chain: str = "ethereum") -> dict:
        async with httpx.AsyncClient(headers=self.headers, timeout=15) as c:
            r = await c.get(f"{self.BASE}/intelligence/address/{address}",
                            params={"chain": chain})
        r.raise_for_status()
        return r.json()

    async def transfers(self, address: str, chain: str = "ethereum",
                          limit: int = 100) -> list[dict]:
        async with httpx.AsyncClient(headers=self.headers, timeout=15) as c:
            r = await c.get(f"{self.BASE}/transfers",
                            params={"base": address, "chains": chain, "limit": limit})
        r.raise_for_status()
        return r.json().get("transfers", [])


class DuneClient:
    BASE = "https://api.dune.com/api/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("DUNE_API_KEY")
        if not self.api_key:
            raise RuntimeError("DUNE_API_KEY required")
        self.headers = {"X-Dune-API-Key": self.api_key}

    async def get_latest_results(self, query_id: int) -> dict:
        async with httpx.AsyncClient(headers=self.headers, timeout=30) as c:
            r = await c.get(f"{self.BASE}/query/{query_id}/results")
        r.raise_for_status()
        return r.json()

    async def execute_query(self, query_id: int,
                              parameters: Optional[dict] = None) -> str:
        async with httpx.AsyncClient(headers=self.headers, timeout=30) as c:
            r = await c.post(f"{self.BASE}/query/{query_id}/execute",
                             json={"query_parameters": parameters or {}})
        r.raise_for_status()
        return r.json()["execution_id"]


async def copy_trade_arkham(smart_addr: str,
                              on_buy: Callable[[dict], Awaitable],
                              min_eth_value: float = 5.0,
                              poll_seconds: int = 15,
                              state: Optional["MonitorState"] = None):
    """Poll Arkham, trigger callback tiap smart wallet buy non-stable token."""
    arkham = ArkhamClient()
    state = state or MonitorState()
    topic = f"arkham:{smart_addr}"

    STABLE = {"USDC", "USDT", "DAI", "ETH", "WETH", "FRAX", "TUSD"}

    while True:
        try:
            transfers = await arkham.transfers(smart_addr, limit=20)
        except Exception as e:
            print(f"[copy_trade_arkham] arkham err: {e}")
            await asyncio.sleep(poll_seconds * 4)
            continue

        for tx in transfers:
            tx_hash = tx.get("hash") or tx.get("txid")
            if not tx_hash or state.is_seen(topic, tx_hash):
                continue
            state.mark_seen(topic, tx_hash)

            # Hanya kalau smart wallet adalah RECEIVER → buy
            to_addr = (tx.get("to", {}).get("address") or "").lower()
            if to_addr != smart_addr.lower():
                continue
            if tx.get("tokenSymbol") in STABLE:
                continue
            value_usd = tx.get("historicalUSD") or 0
            if value_usd < min_eth_value * 2500:   # rough ETH→USD
                continue

            await on_buy(tx)

        await asyncio.sleep(poll_seconds)


# ─────────────────────────── 10. NFT WHALE ───────────────────────────

async def reservoir_sales_stream(api_key: Optional[str] = None,
                                    min_price_eth: float = 5.0,
                                    collections: Optional[list[str]] = None
                                    ) -> AsyncIterator[dict]:
    """Stream realtime via Reservoir WebSocket."""
    import websockets

    key = api_key or os.environ["RESERVOIR_API_KEY"]
    url = f"wss://ws.reservoir.tools?api_key={key}"

    async with websockets.connect(url) as ws:
        sub_msg = {
            "type": "subscribe", "event": "sale.created",
        }
        if collections:
            sub_msg["filters"] = {"contracts": collections}
        await ws.send(json.dumps(sub_msg))

        async for msg in ws:
            data = json.loads(msg)
            if data.get("event") != "sale.created":
                continue
            sale = data.get("data", {})
            price_eth = sale.get("price", {}).get("amount", {}).get("native", 0)
            if price_eth >= min_price_eth:
                yield sale


async def poll_collection_sales(collection_slug: str,
                                  min_price_eth: float = 1.0,
                                  poll_sec: int = 60,
                                  state: Optional["MonitorState"] = None
                                  ) -> AsyncIterator[dict]:
    state = state or MonitorState()
    topic = f"res-sales:{collection_slug}"
    last_check = int(time.time())

    while True:
        try:
            async with httpx.AsyncClient(
                headers={"x-api-key": os.environ["RESERVOIR_API_KEY"]}, timeout=15
            ) as c:
                r = await c.get(
                    "https://api.reservoir.tools/sales/v6",
                    params={"collection": collection_slug, "limit": 100,
                              "startTimestamp": last_check},
                )
            for sale in r.json().get("sales", []):
                sid = sale.get("id")
                if not sid or state.is_seen(topic, sid):
                    continue
                state.mark_seen(topic, sid)
                price = sale.get("price", {}).get("amount", {}).get("native", 0)
                if price >= min_price_eth:
                    yield sale
            last_check = int(time.time())
        except Exception as e:
            print(f"[poll_collection_sales] err: {e}")
        await asyncio.sleep(poll_sec)


async def floor_drop_alert(collection_slug: str, target_floor_eth: float,
                             poll_sec: int = 60) -> AsyncIterator[dict]:
    """Yield satu kali kalau floor drop di bawah target."""
    while True:
        try:
            async with httpx.AsyncClient(
                headers={"x-api-key": os.environ["RESERVOIR_API_KEY"]}, timeout=15
            ) as c:
                r = await c.get(
                    "https://api.reservoir.tools/collections/v7",
                    params={"slug": collection_slug},
                )
            col = r.json().get("collections", [{}])[0]
            floor = col.get("floorAsk", {}).get("price", {}).get("amount", {}).get("native")
            if floor is not None and floor <= target_floor_eth:
                yield {"collection": col.get("name"), "floor": floor,
                        "target": target_floor_eth, "ts": int(time.time())}
                return
        except Exception as e:
            print(f"[floor_drop_alert] err: {e}")
        await asyncio.sleep(poll_sec)


async def magic_eden_whale_stream(min_price_sol: float = 50,
                                    poll_sec: int = 30,
                                    state: Optional["MonitorState"] = None
                                    ) -> AsyncIterator[dict]:
    state = state or MonitorState()
    topic = "magiceden:whales"
    ME = "https://api-mainnet.magiceden.dev/v2"

    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{ME}/activities",
                                params={"type": "buyNow", "limit": 100})
            for act in r.json():
                sig = act.get("signature")
                if not sig or state.is_seen(topic, sig):
                    continue
                state.mark_seen(topic, sig)
                if act.get("price", 0) >= min_price_sol:
                    yield act
        except Exception as e:
            print(f"[magic_eden_whale_stream] err: {e}")
        await asyncio.sleep(poll_sec)


# ─────────────────────────── 11. CONTRACT DEPLOYMENT ───────────────────────────

# ERC-20 standard function selectors (4-byte)
ERC20_SELECTORS = [
    "a9059cbb",  # transfer
    "70a08231",  # balanceOf
    "18160ddd",  # totalSupply
    "95d89b41",  # symbol
    "06fdde03",  # name
    "313ce567",  # decimals
]
ERC721_SELECTORS = [
    "70a08231",  # balanceOf
    "6352211e",  # ownerOf
    "42842e0e",  # safeTransferFrom(addr,addr,uint256)
    "b88d4fde",  # safeTransferFrom(addr,addr,uint256,bytes)
    "a22cb465",  # setApprovalForAll
]


async def watch_contract_deployments(w3_async: AsyncWeb3,
                                        deployer_filter: Optional[list[str]] = None
                                        ) -> AsyncIterator[dict]:
    """Stream contract deployment baru. tx.to == None → contract creation."""
    deployers = {d.lower() for d in deployer_filter} if deployer_filter else None
    await w3_async.eth.subscribe("newHeads")

    async for msg in w3_async.socket.process_subscriptions():
        block_hash = msg["params"]["result"]["hash"]
        try:
            block = await w3_async.eth.get_block(block_hash, full_transactions=True)
        except Exception:
            continue

        for tx in block.transactions:
            if tx.get("to") is not None:
                continue
            deployer = tx["from"].lower()
            if deployers and deployer not in deployers:
                continue

            try:
                receipt = await w3_async.eth.get_transaction_receipt(tx["hash"])
            except Exception:
                continue

            yield {
                "deployer": tx["from"],
                "contract": receipt.get("contractAddress"),
                "tx_hash": tx["hash"].hex() if hasattr(tx["hash"], "hex") else tx["hash"],
                "block": int(block["number"]),
                "ts": int(block["timestamp"]),
                "input_bytecode_size": len((tx.get("input") or "").removeprefix("0x")) // 2,
            }


def classify_contract(w3: Web3, contract_addr: str) -> dict:
    """Guess jenis contract dari deployed bytecode."""
    code_hex = w3.eth.get_code(Web3.to_checksum_address(contract_addr)).hex().lower()
    if not code_hex or code_hex == "0x":
        return {"guess": "no_code", "scores": {}, "bytecode_size": 0}

    erc20_score = sum(1 for s in ERC20_SELECTORS if s in code_hex)
    erc721_score = sum(1 for s in ERC721_SELECTORS if s in code_hex)

    scores = {"ERC-20 token": erc20_score, "ERC-721 NFT": erc721_score}
    best, best_score = max(scores.items(), key=lambda x: x[1])
    return {
        "guess": best if best_score >= 3 else "unknown",
        "scores": scores,
        "bytecode_size": len(code_hex.removeprefix("0x")) // 2,
    }


async def detect_new_token_launches(w3_async: AsyncWeb3,
                                       uni_v2_factory: str,
                                       weth: str,
                                       on_launch: Callable[[dict], Awaitable],
                                       wait_seconds_for_liquidity: int = 300):
    """Watch ERC-20 deploy → cek dalam N detik apakah ada pair Uniswap V2."""
    factory_abi = [{
        "name": "getPair", "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
        ], "outputs": [{"type": "address"}],
        "stateMutability": "view", "type": "function",
    }]

    async for deploy in watch_contract_deployments(w3_async):
        if not deploy["contract"]:
            continue
        # classify pakai sync w3 — fork sebentar (lazy w3)
        sync_w3 = Web3(Web3.HTTPProvider(w3_async.provider.endpoint_uri))
        cls = classify_contract(sync_w3, deploy["contract"])
        if cls["guess"] != "ERC-20 token":
            continue

        asyncio.create_task(_wait_for_liquidity(
            w3_async, deploy, cls, uni_v2_factory, weth, factory_abi,
            wait_seconds_for_liquidity, on_launch
        ))


async def _wait_for_liquidity(w3_async, deploy, cls, factory_addr, weth, abi,
                                wait_sec, on_launch):
    await asyncio.sleep(wait_sec)
    factory = w3_async.eth.contract(
        address=Web3.to_checksum_address(factory_addr), abi=abi)
    try:
        pair = await factory.functions.getPair(
            Web3.to_checksum_address(deploy["contract"]),
            Web3.to_checksum_address(weth),
        ).call()
        if int(pair, 16) != 0:
            await on_launch({**deploy, "classification": cls, "pair": pair})
    except Exception as e:
        print(f"[detect_new_token_launches] pair check err: {e}")


# ─────────────────────────── 13. STATE PERSISTENCE ───────────────────────────

class MonitorState:
    """Cursor + seen-events store untuk monitoring tasks."""

    def __init__(self, db_path: Optional[Path] = None):
        path = db_path or (Path.home() / ".hermes" / "monitor.db")
        path.parent.mkdir(exist_ok=True, parents=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS cursor (
                topic TEXT PRIMARY KEY,
                value TEXT,
                updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS seen_events (
                topic TEXT,
                event_id TEXT,
                ts INTEGER,
                PRIMARY KEY(topic, event_id)
            );
            CREATE INDEX IF NOT EXISTS idx_seen_ts ON seen_events(ts);
        """)
        self.conn.commit()

    def set_cursor(self, topic: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO cursor VALUES (?, ?, ?)",
            (topic, value, int(time.time())))
        self.conn.commit()

    def get_cursor(self, topic: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM cursor WHERE topic = ?", (topic,)).fetchone()
        return row[0] if row else None

    def mark_seen(self, topic: str, event_id: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_events VALUES (?, ?, ?)",
            (topic, event_id, int(time.time())))
        self.conn.commit()

    def is_seen(self, topic: str, event_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM seen_events WHERE topic=? AND event_id=?",
            (topic, event_id)).fetchone() is not None

    def purge_old(self, days: int = 30):
        """Cleanup seen_events older than N days."""
        cutoff = int(time.time()) - days * 86400
        self.conn.execute("DELETE FROM seen_events WHERE ts < ?", (cutoff,))
        self.conn.commit()


# ─────────────────────────── 12. INTEGRATION EXAMPLE ───────────────────────────

async def hermes_alpha_bot(config: dict):
    """Skeleton alpha bot yang gabungkan beberapa monitor sekaligus.

    config sample:
    {
        "ws_rpc": "wss://...",
        "user_wallet": "0x...",
        "smart_wallets": ["0x...", "0x..."],
        "watch_collections": ["doodles", "pudgypenguins"],
        "telegram": {"bot_token": "...", "chat_id": "..."},
    }
    """
    from .monitoring import Notifier  # reuse Notifier dari monitoring.py basic

    notifier = Notifier(
        telegram=(config["telegram"]["bot_token"], config["telegram"]["chat_id"])
            if config.get("telegram") else None,
        discord_webhook=config.get("discord_webhook"),
    )
    state = MonitorState()
    tasks = []

    # Task 1: watch user wallet untuk pending approval (anti-drainer)
    if config.get("user_wallet"):
        sniffer = MempoolSniffer(config["ws_rpc"])
        for sel, fn_name in DANGER_SELECTORS.items():
            sniffer.add_filter(MempoolFilter(
                from_=config["user_wallet"], selector=sel, tag="danger",
            ))

        async def on_danger(tx, f):
            spender = "0x" + (tx.get("input") or "")[34:74]
            await notifier.send(
                f"🚨 Pending {DANGER_SELECTORS.get(f.selector, '?')} from your wallet!\n"
                f"Spender: {spender}\n"
                f"Hash: {tx.get('hash', '?')}",
                severity="critical",
            )

        sniffer.on("danger", on_danger)
        tasks.append(sniffer.run())

    # Task 2: copy-trade smart wallet (via Arkham)
    for sw in config.get("smart_wallets", []):
        async def on_smart_buy(tx, _sw=sw):
            await notifier.send(
                f"🧠 Smart wallet {_sw[:10]}... bought {tx.get('tokenSymbol', '?')} "
                f"(${tx.get('historicalUSD', 0):,.0f})",
                severity="info",
            )
        tasks.append(copy_trade_arkham(sw, on_smart_buy, state=state))

    # Task 3: NFT whale alert
    async def whale_loop():
        async for sale in reservoir_sales_stream(min_price_eth=5.0,
                                                    collections=config.get("watch_collections")):
            price = sale["price"]["amount"]["native"]
            await notifier.send(
                f"🐳 Whale NFT sale: {sale['token']['collection']['name']} "
                f"#{sale['token']['tokenId']} @ {price} ETH",
                severity="info",
            )

    if config.get("watch_collections"):
        tasks.append(whale_loop())

    # Task 4: contract deployment listener
    if config.get("watch_deployments"):
        from web3.providers.persistent import WebSocketProvider
        async def deploy_loop():
            async with AsyncWeb3(WebSocketProvider(config["ws_rpc"])) as w3:
                async def on_launch(info):
                    await notifier.send(
                        f"🆕 New token launched\n"
                        f"Contract: {info['contract']}\n"
                        f"Deployer: {info['deployer']}\n"
                        f"Pair: {info['pair']}\n"
                        f"⚠ Run safety check before buying",
                        severity="warn",
                    )
                await detect_new_token_launches(
                    w3, config["uni_v2_factory"], config["weth"], on_launch
                )
        tasks.append(deploy_loop())

    await asyncio.gather(*tasks, return_exceptions=True)
