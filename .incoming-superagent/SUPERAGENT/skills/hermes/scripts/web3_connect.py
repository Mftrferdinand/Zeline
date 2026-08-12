"""
hermes/web3_connect.py — Sign-in & signature utilities.

Cover SIWE (EIP-4361), EIP-712 typed data, EIP-1271 universal verification,
ENS resolution, dan safety screening for WalletConnect-style requests.
"""
from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_account.signers.local import LocalAccount
from web3 import Web3


# ─────────────────────────── SIWE (EIP-4361) ───────────────────────────

@dataclass
class SiweMessage:
    domain: str
    address: str
    statement: str
    uri: str
    chain_id: int
    nonce: str
    version: str = "1"
    issued_at: Optional[str] = None
    expiration_time: Optional[str] = None

    def render(self) -> str:
        if not self.issued_at:
            self.issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = [
            f"{self.domain} wants you to sign in with your Ethereum account:",
            self.address, "",
            self.statement, "",
            f"URI: {self.uri}",
            f"Version: {self.version}",
            f"Chain ID: {self.chain_id}",
            f"Nonce: {self.nonce}",
            f"Issued At: {self.issued_at}",
        ]
        if self.expiration_time:
            lines.append(f"Expiration Time: {self.expiration_time}")
        return "\n".join(lines)


def generate_nonce(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def sign_siwe(account: LocalAccount, message: str) -> str:
    """Sign SIWE message via EIP-191 personal_sign."""
    return account.sign_message(encode_defunct(text=message)).signature.hex()


def verify_siwe(message: str, signature: str,
                expected_address: str,
                expected_domain: Optional[str] = None,
                expected_nonce: Optional[str] = None) -> tuple[bool, str]:
    try:
        recovered = Account.recover_message(encode_defunct(text=message),
                                              signature=signature)
    except Exception as e:
        return False, f"recovery failed: {e}"
    if recovered.lower() != expected_address.lower():
        return False, f"address mismatch (got {recovered})"
    if expected_domain and not message.startswith(expected_domain):
        return False, "domain mismatch"
    if expected_nonce and f"Nonce: {expected_nonce}" not in message:
        return False, "nonce mismatch"
    # validasi expiry
    m = re.search(r"Expiration Time: (\S+)", message)
    if m:
        try:
            exp = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp:
                return False, "message expired"
        except Exception:
            pass
    return True, "ok"


# ─────────────────────────── EIP-712 ───────────────────────────

def sign_typed_data(account: LocalAccount, full_message: dict) -> str:
    """Sign EIP-712 typed data. full_message harus include types, domain, primaryType, message."""
    encoded = encode_typed_data(full_message=full_message)
    return account.sign_message(encoded).signature.hex()


def split_signature(sig_hex: str) -> tuple[int, bytes, bytes]:
    """Split 65-byte signature jadi (v, r, s)."""
    sig = bytes.fromhex(sig_hex.removeprefix("0x"))
    if len(sig) != 65:
        raise ValueError(f"signature length {len(sig)} != 65")
    return sig[64], sig[:32], sig[32:64]


# ─────────────────────────── ERC-2612 Permit ───────────────────────────

ERC20_PERMIT_EXT_ABI = [
    {"name": "name", "inputs": [], "outputs": [{"type": "string"}],
     "stateMutability": "view", "type": "function"},
    {"name": "nonces", "inputs": [{"name": "owner", "type": "address"}],
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "DOMAIN_SEPARATOR", "inputs": [], "outputs": [{"type": "bytes32"}],
     "stateMutability": "view", "type": "function"},
]


async def build_permit(w3: Web3, account: LocalAccount, token_addr: str,
                        spender: str, value: int, deadline: int,
                        version: str = "1") -> dict:
    """Build ERC-2612 permit signature. Returns dict {v, r, s, value, deadline}."""
    token = w3.eth.contract(address=Web3.to_checksum_address(token_addr),
                             abi=ERC20_PERMIT_EXT_ABI)
    nonce = token.functions.nonces(account.address).call()
    name = token.functions.name().call()

    full = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Permit": [
                {"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "Permit",
        "domain": {"name": name, "version": version,
                   "chainId": w3.eth.chain_id, "verifyingContract": token_addr},
        "message": {"owner": account.address, "spender": spender,
                    "value": value, "nonce": nonce, "deadline": deadline},
    }
    sig = sign_typed_data(account, full)
    v, r, s = split_signature(sig)
    return {"v": v, "r": "0x" + r.hex(), "s": "0x" + s.hex(),
            "value": value, "deadline": deadline}


# ─────────────────────────── EIP-1271 Universal Verify ───────────────────────────

EIP1271_ABI = [{
    "name": "isValidSignature",
    "inputs": [{"name": "hash", "type": "bytes32"},
                {"name": "signature", "type": "bytes"}],
    "outputs": [{"name": "magic", "type": "bytes4"}],
    "type": "function", "stateMutability": "view",
}]
EIP1271_MAGIC = "0x1626ba7e"


def verify_signature(w3: Web3, address: str, hash32: bytes, signature: str) -> bool:
    """Verify signature dari EOA atau smart contract wallet (Safe, Argent, dst)."""
    code = w3.eth.get_code(Web3.to_checksum_address(address))
    if code == b'':
        # EOA
        try:
            recovered = Account._recover_hash(hash32, signature=signature)
            return recovered.lower() == address.lower()
        except Exception:
            return False
    else:
        c = w3.eth.contract(address=Web3.to_checksum_address(address), abi=EIP1271_ABI)
        try:
            magic = c.functions.isValidSignature(hash32, signature).call()
            return magic.hex() == EIP1271_MAGIC.removeprefix("0x")
        except Exception:
            return False


# ─────────────────────────── ENS ───────────────────────────

def resolve_ens(w3: Web3, name: str) -> Optional[str]:
    """ENS name → address. Returns None kalau tidak ke-resolve."""
    from ens import ENS
    ns = ENS.from_web3(w3)
    try:
        return ns.address(name)
    except Exception:
        return None


def reverse_ens(w3: Web3, address: str) -> Optional[str]:
    """address → primary ENS name."""
    from ens import ENS
    ns = ENS.from_web3(w3)
    try:
        return ns.name(address)
    except Exception:
        return None


# ─────────────────────────── 4byte Decoder ───────────────────────────

async def decode_4byte(selector_hex: str) -> str:
    """Resolve function selector (e.g. '0xa9059cbb') ke human-readable signature."""
    selector = selector_hex if selector_hex.startswith("0x") else "0x" + selector_hex
    async with httpx.AsyncClient() as c:
        r = await c.get("https://www.4byte.directory/api/v1/signatures/",
                        params={"hex_signature": selector}, timeout=5)
    results = r.json().get("results", [])
    if not results:
        return f"unknown ({selector})"
    # ambil yang paling lama (paling kemungkinan benar — newer entries kadang phishing collision)
    return min(results, key=lambda x: x["id"])["text_signature"]


# ─────────────────────────── Transaction Screening ───────────────────────────

@dataclass
class TxScreen:
    safe: bool
    decoded_fn: Optional[str]
    warnings: list[str]
    simulation_passed: bool


async def screen_tx(w3: Web3, tx: dict, dapp_name: str = "unknown") -> TxScreen:
    """Screen tx sebelum sign — decode + simulate + check known bad patterns."""
    warnings = []

    # 1. Decode function
    decoded_fn = None
    data = tx.get("data", "0x")
    if data and len(data) >= 10:
        selector = data[:10]
        decoded_fn = await decode_4byte(selector)
        # Red flags
        if any(p in decoded_fn.lower() for p in [
            "setapprovalforall", "approve(", "permit",
        ]):
            warnings.append(f"⚠ APPROVAL request: {decoded_fn}")
        if "permitall" in decoded_fn.lower() or "increaseallowance" in decoded_fn.lower():
            warnings.append(f"⚠ Broad allowance change: {decoded_fn}")

    # 2. Simulate
    sim_ok = True
    try:
        w3.eth.call({
            "from": tx["from"],
            "to": tx["to"],
            "data": data,
            "value": int(tx.get("value", 0)) if isinstance(tx.get("value"), int)
                else int(tx.get("value", "0x0"), 16),
        })
    except Exception as e:
        sim_ok = False
        warnings.append(f"⚠ Simulation reverted: {str(e)[:200]}")

    # 3. Check value
    value = tx.get("value", 0)
    if isinstance(value, str):
        value = int(value, 16) if value.startswith("0x") else int(value)
    if value > w3.to_wei(10, "ether"):
        warnings.append(f"⚠ HIGH VALUE: {w3.from_wei(value, 'ether')} ETH")

    critical = any("HIGH VALUE" in w or "Simulation reverted" in w for w in warnings)
    return TxScreen(safe=not critical, decoded_fn=decoded_fn,
                     warnings=warnings, simulation_passed=sim_ok)
