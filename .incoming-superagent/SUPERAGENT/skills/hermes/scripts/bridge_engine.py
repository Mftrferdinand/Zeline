"""
hermes/bridge_engine.py — Cross-chain bridge engine.

Default: LI.FI aggregator (routing optimal lintas-chain + lintas-bridge).
Optional: Stargate langsung untuk farming LayerZero points.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from web3 import Web3
from eth_account.signers.local import LocalAccount

try:
    # When imported as part of a package (e.g. hermes.scripts).
    from .swap_engine import approve_if_needed, NATIVE_SENTINEL_EVM
except ImportError:
    # When copied/run as a flat, standalone template (as the README describes).
    from swap_engine import approve_if_needed, NATIVE_SENTINEL_EVM


@dataclass
class BridgeResult:
    status: str
    src_tx_hash: Optional[str] = None
    dst_tx_hash: Optional[str] = None
    estimated_duration_sec: Optional[int] = None
    error: Optional[str] = None


# ─────────────────────────── LI.FI Aggregator ───────────────────────────

LIFI_API = "https://li.quest/v1"


async def lifi_get_quote(from_chain: int, to_chain: int,
                          from_token: str, to_token: str,
                          from_amount: int, from_address: str,
                          to_address: Optional[str] = None,
                          slippage: float = 0.01) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{LIFI_API}/quote", params={
            "fromChain": from_chain, "toChain": to_chain,
            "fromToken": from_token, "toToken": to_token,
            "fromAmount": str(from_amount),
            "fromAddress": from_address,
            "toAddress": to_address or from_address,
            "slippage": slippage,
        })
    r.raise_for_status()
    return r.json()


async def lifi_bridge(w3: Web3, account: LocalAccount,
                       to_chain: int, from_token: str, to_token: str,
                       amount: int, to_address: Optional[str] = None) -> BridgeResult:
    """Bridge via LI.FI optimal route."""
    try:
        quote = await lifi_get_quote(
            from_chain=w3.eth.chain_id,
            to_chain=to_chain,
            from_token=from_token,
            to_token=to_token,
            from_amount=amount,
            from_address=account.address,
            to_address=to_address,
        )
    except httpx.HTTPStatusError as e:
        return BridgeResult(status="error",
                             error=f"LI.FI quote failed: {e.response.text}")

    tx_req = quote["transactionRequest"]

    # Approve kalau dari ERC-20
    from_token_addr = quote["action"]["fromToken"]["address"]
    if from_token_addr.lower() not in (NATIVE_SENTINEL_EVM.lower(),
                                          "0x0000000000000000000000000000000000000000"):
        approve_if_needed(w3, account, from_token_addr, tx_req["to"], amount)

    # Build & send
    value = int(tx_req.get("value", "0x0"), 16) if isinstance(tx_req.get("value"), str) \
        else int(tx_req.get("value", 0))
    gas_limit = int(tx_req["gasLimit"], 16) if isinstance(tx_req["gasLimit"], str) \
        else int(tx_req["gasLimit"])
    gas_price = int(tx_req["gasPrice"], 16) if isinstance(tx_req["gasPrice"], str) \
        else int(tx_req["gasPrice"])

    tx = {
        "from": account.address,
        "to": Web3.to_checksum_address(tx_req["to"]),
        "data": tx_req["data"],
        "value": value,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "chainId": w3.eth.chain_id,
    }
    signed = account.sign_transaction(tx)
    src_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()

    return BridgeResult(
        status="sent",
        src_tx_hash=src_hash,
        estimated_duration_sec=int(quote["estimate"].get("executionDuration", 600)),
    )


async def lifi_track(src_tx_hash: str, from_chain: int,
                      to_chain: int) -> BridgeResult:
    """Poll bridge status sampai DONE atau FAILED."""
    async with httpx.AsyncClient() as c:
        while True:
            r = await c.get(f"{LIFI_API}/status", params={
                "txHash": src_tx_hash,
                "fromChain": from_chain,
                "toChain": to_chain,
            })
            data = r.json()
            status = data.get("status")
            if status == "DONE":
                return BridgeResult(
                    status="completed", src_tx_hash=src_tx_hash,
                    dst_tx_hash=data.get("receiving", {}).get("txHash"),
                )
            if status == "FAILED":
                return BridgeResult(status="error", src_tx_hash=src_tx_hash,
                                     error=data.get("substatusMessage", "bridge failed"))
            await asyncio.sleep(15)


# ─────────────────────────── Stargate V2 (LayerZero Farming) ───────────────────────────

LZ_ENDPOINT_IDS = {
    1: 30101, 8453: 30184, 42161: 30110, 10: 30111,
    137: 30109, 56: 30102, 43114: 30106, 59144: 30183,
    534352: 30214, 5000: 30181,
}

STARGATE_V2_POOLS = {
    # chain_id: {symbol: pool_address}
    1: {
        "USDC": "0xc026395860Db2d07ee33e05fE50ed7bD583189C7",
        "USDT": "0x933597a323Eb81cAe705C5bC29985172fd5A3973",
        "ETH":  "0x77b2043768d28E9C9aB44E1aBfC95944bcE57931",
    },
    8453: {
        "USDC": "0x27a16dc786820B16E5c9028b75B99F6f604b5d26",
        "ETH":  "0xdc181Bd607330aeeBEF6ea62e03e5e1Fb4B6F7C7",
    },
    42161: {
        "USDC": "0xe8CDF27AcD73a434D661C84887215F7598e7d0d3",
        "USDT": "0xcE8CcA271Ebc0533920C83d39F417ED6A0abB7D0",
        "ETH":  "0xA45B5130f36CDcA45667738e2a258AB09f4A5f7F",
    },
    10: {
        "USDC": "0xcE8CcA271Ebc0533920C83d39F417ED6A0abB7D0",
        "ETH":  "0xe8CDF27AcD73a434D661C84887215F7598e7d0d3",
    },
    137: {
        "USDC": "0x9Aa02D4Fae7F58b8E8f34c66E756cC734DAc7fe4",
        "USDT": "0xd47b03ee6d86Cf251ee7860FB2ACf9f91B9fD4d7",
    },
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
         {"name": "_sendParam", "type": "tuple", "components": [
             {"name": "dstEid", "type": "uint32"},
             {"name": "to", "type": "bytes32"},
             {"name": "amountLD", "type": "uint256"},
             {"name": "minAmountLD", "type": "uint256"},
             {"name": "extraOptions", "type": "bytes"},
             {"name": "composeMsg", "type": "bytes"},
             {"name": "oftCmd", "type": "bytes"},
         ]},
         {"name": "_payInLzToken", "type": "bool"},
     ],
     "outputs": [{"name": "fee", "type": "tuple", "components": [
         {"name": "nativeFee", "type": "uint256"},
         {"name": "lzTokenFee", "type": "uint256"},
     ]}],
     "stateMutability": "view", "type": "function"},
]


def _addr_to_bytes32(addr: str) -> bytes:
    return bytes(12) + bytes.fromhex(addr.removeprefix("0x"))


async def stargate_v2_bridge(w3: Web3, account: LocalAccount,
                              to_chain_id: int, token_symbol: str,
                              amount: int, slippage_bps: int = 100,
                              underlying_token_addr: Optional[str] = None) -> BridgeResult:
    """Bridge via Stargate v2 langsung — bagus untuk farming LayerZero points."""
    from_chain_id = w3.eth.chain_id
    if to_chain_id not in LZ_ENDPOINT_IDS:
        return BridgeResult(status="error", error=f"chain {to_chain_id} tidak didukung")
    if token_symbol not in STARGATE_V2_POOLS.get(from_chain_id, {}):
        return BridgeResult(status="error",
                             error=f"{token_symbol} di chain {from_chain_id} tidak ada pool")

    pool_addr = STARGATE_V2_POOLS[from_chain_id][token_symbol]
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_addr), abi=STARGATE_V2_ABI)

    send_param = (
        LZ_ENDPOINT_IDS[to_chain_id],
        _addr_to_bytes32(account.address),
        amount,
        amount * (10000 - slippage_bps) // 10000,
        b"", b"", b"",
    )
    fee = pool.functions.quoteSend(send_param, False).call()
    native_fee = fee[0]

    if token_symbol == "ETH":
        value = native_fee + amount
    else:
        if not underlying_token_addr:
            return BridgeResult(status="error",
                                 error="butuh underlying_token_addr untuk non-ETH")
        approve_if_needed(w3, account, underlying_token_addr, pool_addr, amount)
        value = native_fee

    tx = pool.functions.send(send_param, (native_fee, 0), account.address)\
        .build_transaction({
            "from": account.address,
            "value": value,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            "gas": 500_000,
            "maxFeePerGas": w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        })
    signed = account.sign_transaction(tx)
    src_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
    return BridgeResult(status="sent", src_tx_hash=src_hash,
                         estimated_duration_sec=180)


async def layerzero_scan(src_tx_hash: str) -> dict:
    """Track tx via LayerZeroScan."""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://scan.layerzero-api.com/v1/messages/tx/{src_tx_hash}")
    return r.json()
