"""
hermes/monitoring.py — On-chain monitoring utilities.

Wallet activity tracking, contract event listening, portfolio aggregation,
mempool watching, price alerts. Pakai WebSocket + Webhook untuk efisiensi.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Optional

import httpx
from web3 import AsyncWeb3, Web3


# ─────────────────────────── Wallet Activity ───────────────────────────

@dataclass
class WalletEvent:
    direction: str          # "incoming" | "outgoing"
    tx_hash: str
    from_addr: str
    to_addr: str
    value_wei: int
    block_number: int
    ts: int


async def watch_wallet_websocket(w3_async: AsyncWeb3, watched_addr: str,
                                   ) -> AsyncIterator[WalletEvent]:
    """Stream block-by-block, yield events untuk watched_addr.

    NOTE: hanya catch native ETH transfer + tx ke/dari contract.
    Untuk ERC-20 transfer, watch Transfer event terpisah (lihat watch_erc20_transfers).
    """
    watched = watched_addr.lower()
    sub_id = await w3_async.eth.subscribe("newHeads")

    async for header in w3_async.socket.process_subscriptions():
        block_hash = header["params"]["result"]["hash"]
        block = await w3_async.eth.get_block(block_hash, full_transactions=True)
        for tx in block.transactions:
            from_a = (tx["from"] or "").lower()
            to_a = (tx.get("to") or "").lower()
            if watched not in (from_a, to_a):
                continue
            yield WalletEvent(
                direction="outgoing" if from_a == watched else "incoming",
                tx_hash=tx["hash"].hex(),
                from_addr=tx["from"], to_addr=tx.get("to") or "",
                value_wei=int(tx["value"]),
                block_number=int(block["number"]),
                ts=int(block["timestamp"]),
            )


async def watch_erc20_transfers(w3_async: AsyncWeb3,
                                  watched_addr: str,
                                  token_addresses: Optional[list[str]] = None
                                  ) -> AsyncIterator[dict]:
    """Stream ERC-20 Transfer event yang involve watched_addr.

    token_addresses: kalau None, listen semua token (banyak event!).
    Untuk performa, batasi ke token yang relevan.
    """
    transfer_topic = w3_async.keccak(text="Transfer(address,address,uint256)").hex()
    padded_addr = "0x" + "0" * 24 + watched_addr.removeprefix("0x").lower()

    # Subscribe 2x: sebagai "from" topic dan sebagai "to" topic
    filters = []
    for idx, role in enumerate(["from", "to"]):
        topic_slot = idx + 1   # topic[1] = from, topic[2] = to
        topics = [transfer_topic, None, None]
        topics[topic_slot] = padded_addr
        filters.append(topics)

    queues = []
    for f_topics in filters:
        params = {"topics": f_topics}
        if token_addresses:
            params["address"] = token_addresses
        q_id = await w3_async.eth.subscribe("logs", params)
        queues.append(q_id)

    async for msg in w3_async.socket.process_subscriptions():
        log = msg["params"]["result"]
        from_a = "0x" + log["topics"][1][-40:]
        to_a = "0x" + log["topics"][2][-40:]
        amount = int(log["data"], 16)
        yield {
            "token": log["address"],
            "from": from_a, "to": to_a, "amount": amount,
            "tx_hash": log["transactionHash"],
            "block": int(log["blockNumber"], 16),
        }


# ─────────────────────────── Portfolio ───────────────────────────

async def portfolio_zerion(address: str, api_key_b64: Optional[str] = None) -> dict:
    """Multi-chain EVM portfolio via Zerion. api_key_b64 = base64('{key}:')."""
    key = api_key_b64 or os.environ.get("ZERION_API_KEY_B64")
    if not key:
        raise RuntimeError("set ZERION_API_KEY_B64 env var")

    async with httpx.AsyncClient(headers={"Authorization": f"Basic {key}"}) as c:
        r = await c.get(f"https://api.zerion.io/v1/wallets/{address}/positions",
                        params={"filter[positions]": "no_filter"})
    return r.json()


async def portfolio_solana_birdeye(address: str,
                                     api_key: Optional[str] = None) -> dict:
    key = api_key or os.environ.get("BIRDEYE_API_KEY")
    headers = {"X-API-KEY": key, "x-chain": "solana"} if key else {"x-chain": "solana"}
    async with httpx.AsyncClient(headers=headers) as c:
        r = await c.get(f"https://public-api.birdeye.so/v1/wallet/token_list",
                        params={"wallet": address})
    return r.json()


async def get_full_portfolio(wallets: dict[str, str]) -> dict:
    """wallets: {'evm': '0x...', 'solana': '...'} → aggregate."""
    out = {"per_chain": {}, "total_usd": 0.0}

    if "evm" in wallets:
        try:
            data = await portfolio_zerion(wallets["evm"])
            usd = 0.0
            for p in data.get("data", []):
                attr = p.get("attributes", {})
                usd += attr.get("value", 0) or 0
            out["per_chain"]["evm"] = {"value_usd": usd, "raw": data}
            out["total_usd"] += usd
        except Exception as e:
            out["per_chain"]["evm"] = {"error": str(e)}

    if "solana" in wallets:
        try:
            data = await portfolio_solana_birdeye(wallets["solana"])
            items = data.get("data", {}).get("items", [])
            usd = sum(i.get("valueUsd", 0) or 0 for i in items)
            out["per_chain"]["solana"] = {"value_usd": usd, "items_count": len(items)}
            out["total_usd"] += usd
        except Exception as e:
            out["per_chain"]["solana"] = {"error": str(e)}

    return out


# ─────────────────────────── Price Monitoring ───────────────────────────

async def dexscreener_price(token_address: str, chain: str = "ethereum") -> Optional[dict]:
    """Get current price + liquidity via DexScreener. Free, multi-chain, ~30s update."""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
    pairs = r.json().get("pairs", [])
    if not pairs:
        return None
    relevant = [p for p in pairs if p["chainId"] == chain]
    if not relevant:
        return None
    # ambil pair dengan likuiditas tertinggi
    best = max(relevant, key=lambda p: p["liquidity"].get("usd", 0))
    return {
        "price_usd": float(best["priceUsd"]),
        "liquidity_usd": best["liquidity"]["usd"],
        "volume_24h": best["volume"]["h24"],
        "price_change_24h": best["priceChange"]["h24"],
        "pair_address": best["pairAddress"],
        "dex": best["dexId"],
    }


async def price_alert_loop(token_address: str, chain: str,
                            on_change: Callable[[dict], Awaitable[None]],
                            threshold_pct: float = 10,
                            poll_seconds: int = 30):
    """Yield alert tiap ada perubahan ≥ threshold dari last."""
    last = None
    while True:
        try:
            data = await dexscreener_price(token_address, chain)
            if data:
                price = data["price_usd"]
                if last is not None:
                    change = (price - last) / last * 100
                    if abs(change) >= threshold_pct:
                        await on_change({"price": price, "change_pct": change,
                                          "liquidity": data["liquidity_usd"],
                                          "ts": int(time.time())})
                last = price
        except Exception as e:
            print(f"price poll err: {e}")
        await asyncio.sleep(poll_seconds)


# ─────────────────────────── Liquidation Risk ───────────────────────────

async def aave_health_monitor(w3: Web3, address: str,
                                alert_threshold: float = 1.3,
                                poll_seconds: int = 60
                                ) -> AsyncIterator[dict]:
    """Monitor Aave health factor. Yield dict tiap < threshold."""
    from .swap_engine import approve_if_needed  # placeholder import — di production import dari defi engine
    AAVE_POOLS = {
        1: "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        42161: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        10: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        8453: "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    }
    ABI = [{"name": "getUserAccountData",
             "inputs": [{"name": "user", "type": "address"}],
             "outputs": [
                 {"name": "totalCollateralBase", "type": "uint256"},
                 {"name": "totalDebtBase", "type": "uint256"},
                 {"name": "availableBorrowsBase", "type": "uint256"},
                 {"name": "currentLiquidationThreshold", "type": "uint256"},
                 {"name": "ltv", "type": "uint256"},
                 {"name": "healthFactor", "type": "uint256"},
             ], "stateMutability": "view", "type": "function"}]

    pool_addr = AAVE_POOLS.get(w3.eth.chain_id)
    if not pool_addr:
        raise RuntimeError(f"Aave not deployed on chain {w3.eth.chain_id}")
    pool = w3.eth.contract(address=pool_addr, abi=ABI)

    while True:
        try:
            data = pool.functions.getUserAccountData(
                Web3.to_checksum_address(address)).call()
            hf = data[5] / 1e18
            if hf < alert_threshold:
                yield {
                    "severity": "CRITICAL" if hf < 1.1 else "WARN",
                    "health_factor": hf,
                    "collateral_usd": data[0] / 1e8,
                    "debt_usd": data[1] / 1e8,
                    "action": "repay debt OR add collateral",
                    "ts": int(time.time()),
                }
        except Exception as e:
            print(f"aave check err: {e}")
        await asyncio.sleep(poll_seconds)


# ─────────────────────────── Multi-Channel Notify ───────────────────────────

async def notify_telegram(bot_token: str, chat_id: str, message: str,
                            parse_mode: str = "Markdown"):
    async with httpx.AsyncClient() as c:
        await c.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                     json={"chat_id": chat_id, "text": message,
                           "parse_mode": parse_mode})


async def notify_discord(webhook_url: str, message: str):
    async with httpx.AsyncClient() as c:
        await c.post(webhook_url, json={"content": message})


class Notifier:
    """Simple multi-channel notifier."""

    def __init__(self,
                 telegram: Optional[tuple[str, str]] = None,  # (bot_token, chat_id)
                 discord_webhook: Optional[str] = None):
        self.telegram = telegram
        self.discord = discord_webhook

    async def send(self, message: str, severity: str = "info"):
        tasks = []
        prefix = {"info": "ℹ", "warn": "⚠", "critical": "🚨"}.get(severity, "")
        msg = f"{prefix} {message}" if prefix else message
        if self.telegram:
            tasks.append(notify_telegram(self.telegram[0], self.telegram[1], msg))
        if self.discord:
            tasks.append(notify_discord(self.discord, msg))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# ─────────────────────────── RPC Failover ───────────────────────────

class RPCRouter:
    """Round-robin RPC failover — pakai untuk reliability."""

    def __init__(self, urls: list[str]):
        if not urls:
            raise ValueError("at least one RPC URL required")
        self.urls = urls
        self._idx = 0

    @property
    def current(self) -> str:
        return self.urls[self._idx]

    def _rotate(self):
        self._idx = (self._idx + 1) % len(self.urls)

    def w3(self) -> Web3:
        return Web3(Web3.HTTPProvider(self.current,
                                       request_kwargs={"timeout": 10}))

    async def call_with_failover(self, fn: Callable, *args, **kwargs):
        last_err = None
        for _ in range(len(self.urls)):
            try:
                return fn(self.w3(), *args, **kwargs)
            except Exception as e:
                last_err = e
                self._rotate()
        raise RuntimeError(f"all RPCs failed: {last_err}")
