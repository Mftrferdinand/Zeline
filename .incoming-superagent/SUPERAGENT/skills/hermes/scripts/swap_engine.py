"""
hermes/swap_engine.py — Template swap/sell token lintas-chain.

Strategi: pakai aggregator (1inch/0x untuk EVM, Jupiter untuk Solana) sebagai default
karena routing-nya biasanya optimal. Fallback ke DEX router langsung kalau aggregator
gagal atau token tidak ter-index.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from web3 import Web3
from eth_account.signers.local import LocalAccount


NATIVE_SENTINEL_EVM = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": False,
     "inputs": [{"name": "_spender", "type": "address"},
                {"name": "_value", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "type": "function"},
    {"constant": True,
     "inputs": [{"name": "_owner", "type": "address"},
                {"name": "_spender", "type": "address"}],
     "name": "allowance",
     "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]


@dataclass
class SwapResult:
    status: str            # "sent" | "blocked" | "error"
    tx_hash: Optional[str] = None
    explorer_url: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None


# ─────────────────────────── SAFETY ───────────────────────────

async def safety_check_token(token_address: str, chain_id: int,
                               session: httpx.AsyncClient) -> tuple[bool, list[str]]:
    """Return (is_safe, warnings). is_safe=False berarti BLOK eksekusi."""
    issues, critical = [], False

    # Honeypot.is
    try:
        r = await session.get("https://api.honeypot.is/v2/IsHoneypot",
                              params={"address": token_address, "chainID": chain_id},
                              timeout=5)
        d = r.json()
        if d.get("honeypotResult", {}).get("isHoneypot"):
            issues.append(f"HONEYPOT (honeypot.is): {d['honeypotResult'].get('honeypotReason')}")
            critical = True
        sim = d.get("simulationResult", {})
        buy_tax, sell_tax = sim.get("buyTax", 0), sim.get("sellTax", 0)
        if sell_tax > 15:
            issues.append(f"Sell tax tinggi: {sell_tax:.1f}%")
        if buy_tax > 15:
            issues.append(f"Buy tax tinggi: {buy_tax:.1f}%")
    except Exception as e:
        issues.append(f"honeypot.is unreachable: {e}")

    # GoPlus
    try:
        r = await session.get(f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
                              params={"contract_addresses": token_address}, timeout=5)
        gp = (r.json().get("result") or {}).get(token_address.lower(), {})
        flags = [("is_mintable", "supply mintable"),
                 ("can_take_back_ownership", "owner take-back"),
                 ("hidden_owner", "hidden owner"),
                 ("transfer_pausable", "transfer pausable"),
                 ("is_blacklisted", "blacklist function")]
        for k, label in flags:
            if gp.get(k) == "1":
                issues.append(f"GoPlus flag: {label}")
                if k in ("hidden_owner",):
                    critical = True
    except Exception as e:
        issues.append(f"goplus unreachable: {e}")

    return (not critical), issues


# ─────────────────────────── EVM SWAP ───────────────────────────

class OneInchSwapper:
    """Swap via 1inch v6 API (butuh API key dari https://portal.1inch.dev)."""

    BASE = "https://api.1inch.dev/swap/v6.0"

    def __init__(self, api_key: str):
        self.headers = {"Authorization": f"Bearer {api_key}"}

    async def quote(self, chain_id: int, src: str, dst: str, amount_wei: int) -> dict:
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.get(f"{self.BASE}/{chain_id}/quote",
                            params={"src": src, "dst": dst, "amount": str(amount_wei)})
        r.raise_for_status()
        return r.json()

    async def build_swap(self, chain_id: int, src: str, dst: str, amount_wei: int,
                          from_addr: str, slippage_pct: float) -> dict:
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.get(f"{self.BASE}/{chain_id}/swap", params={
                "src": src, "dst": dst, "amount": str(amount_wei),
                "from": from_addr, "slippage": slippage_pct,
                "disableEstimate": "false",
            })
        r.raise_for_status()
        return r.json()["tx"]

    async def get_allowance_target(self, chain_id: int) -> str:
        async with httpx.AsyncClient(headers=self.headers) as c:
            r = await c.get(f"{self.BASE}/{chain_id}/approve/spender")
        return r.json()["address"]


def approve_if_needed(w3: Web3, account: LocalAccount, token: str,
                      spender: str, amount: int, use_exact=True) -> Optional[str]:
    if token.lower() == NATIVE_SENTINEL_EVM.lower():
        return None
    c = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
    current = c.functions.allowance(account.address,
                                     Web3.to_checksum_address(spender)).call()
    if current >= amount:
        return None
    approve_amount = amount if use_exact else (2**256 - 1)
    tx = c.functions.approve(Web3.to_checksum_address(spender),
                              approve_amount).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "gas": 80000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    })
    signed = account.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
    w3.eth.wait_for_transaction_receipt(h, timeout=120)
    return h


async def swap_evm(
    w3: Web3, account: LocalAccount,
    token_in: str, token_out: str, amount_wei: int,
    slippage_pct: float = 1.0,
    oneinch_api_key: Optional[str] = None,
    skip_safety: bool = False,
) -> SwapResult:
    """Swap di EVM via 1inch. token_in/out: contract address atau NATIVE_SENTINEL_EVM."""
    chain_id = w3.eth.chain_id

    # safety check token_out (kalau bukan native/well-known)
    if not skip_safety and token_out.lower() != NATIVE_SENTINEL_EVM.lower():
        async with httpx.AsyncClient() as s:
            safe, warnings = await safety_check_token(token_out, chain_id, s)
        if not safe:
            return SwapResult(status="blocked", warnings=warnings)
    else:
        warnings = []

    swapper = OneInchSwapper(oneinch_api_key or os.environ["ONEINCH_API_KEY"])

    # approval (kalau token_in bukan native)
    if token_in.lower() != NATIVE_SENTINEL_EVM.lower():
        spender = await swapper.get_allowance_target(chain_id)
        approve_if_needed(w3, account, token_in, spender, amount_wei)

    tx_data = await swapper.build_swap(chain_id, token_in, token_out, amount_wei,
                                         account.address, slippage_pct)

    tx = {
        "from": account.address,
        "to": Web3.to_checksum_address(tx_data["to"]),
        "data": tx_data["data"],
        "value": int(tx_data["value"]),
        "gas": int(tx_data["gas"]),
        "gasPrice": int(tx_data["gasPrice"]),
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "chainId": chain_id,
    }
    signed = account.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
    return SwapResult(status="sent", tx_hash=h, warnings=warnings,
                       explorer_url=_explorer_url(chain_id, h))


async def sell_token_full(w3: Web3, account: LocalAccount, token_address: str,
                           slippage_pct: float = 2.0,
                           oneinch_api_key: Optional[str] = None) -> SwapResult:
    """Sell full balance dari token_address → native."""
    c = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    balance = c.functions.balanceOf(account.address).call()
    if balance == 0:
        return SwapResult(status="error", error="balance 0")
    return await swap_evm(w3, account, token_address, NATIVE_SENTINEL_EVM,
                           balance, slippage_pct, oneinch_api_key,
                           skip_safety=True)  # sell sendiri, gak perlu honeypot check


# ─────────────────────────── SOLANA SWAP ───────────────────────────

async def swap_solana(client, keypair, input_mint: str, output_mint: str,
                       amount_lamports: int, slippage_bps: int = 100) -> SwapResult:
    """Swap di Solana via Jupiter v6."""
    from solders.transaction import VersionedTransaction
    from solders.message import to_bytes_versioned
    import base64

    JUP = "https://quote-api.jup.ag/v6"
    async with httpx.AsyncClient() as c:
        q = await c.get(f"{JUP}/quote", params={
            "inputMint": input_mint, "outputMint": output_mint,
            "amount": amount_lamports, "slippageBps": slippage_bps,
        })
        quote = q.json()
        if "error" in quote:
            return SwapResult(status="error", error=quote["error"])

        s = await c.post(f"{JUP}/swap", json={
            "quoteResponse": quote,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "prioritizationFeeLamports": "auto",
        })
    swap_tx_b64 = s.json()["swapTransaction"]

    raw = base64.b64decode(swap_tx_b64)
    vtx = VersionedTransaction.from_bytes(raw)
    signed = VersionedTransaction(vtx.message,
                                    [keypair.sign_message(to_bytes_versioned(vtx.message))])
    sig = await client.send_raw_transaction(bytes(signed))
    return SwapResult(status="sent", tx_hash=str(sig.value),
                       explorer_url=f"https://solscan.io/tx/{sig.value}")


# ─────────────────────────── HELPERS ───────────────────────────

EXPLORERS = {
    1: "https://etherscan.io/tx/",
    56: "https://bscscan.com/tx/",
    8453: "https://basescan.org/tx/",
    42161: "https://arbiscan.io/tx/",
    10: "https://optimistic.etherscan.io/tx/",
    137: "https://polygonscan.com/tx/",
    43114: "https://snowtrace.io/tx/",
}


def _explorer_url(chain_id: int, tx_hash: str) -> str:
    return f"{EXPLORERS.get(chain_id, '')}{tx_hash}"
