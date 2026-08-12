"""
hermes/contract_reader.py — Universal Smart Contract Reader  (v4.0)

Baca KONTRAK APA PUN di banyak chain EVM tanpa nulis engine khusus:
- fetch ABI otomatis (Sourcify → Blockscout, KEYLESS)
- panggil fungsi read (view/pure) mana aja, output di-decode
- list semua fungsi read, deteksi standar (ERC-20/721/1155)
- resolve proxy (EIP-1967) → alamat implementasi sebenarnya
- info token cepat (name/symbol/decimals/totalSupply)

Read-only sepenuhnya — gak ada tx, gak ada dana keluar, gak butuh governor.
Non-EVM (Solana/Sui/Aptos/TON) modelnya beda (program/IDL) — lihat wallets.md.

Dependency: web3, httpx.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Any

import httpx
from web3 import Web3

# EIP-1967 implementation storage slot
_EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

# Registry chain: id → (nama, RPC publik default, base Blockscout kalau ada).
# RPC bisa di-override dari env/RPCRouter. Sourcify support semua chainId standar.
CHAINS: dict[int, dict] = {
    1:     {"name": "ethereum",  "rpc": "https://eth.llamarpc.com",        "blockscout": "https://eth.blockscout.com"},
    56:    {"name": "bsc",       "rpc": "https://bsc-dataseed.binance.org", "blockscout": None},
    137:   {"name": "polygon",   "rpc": "https://polygon-rpc.com",          "blockscout": "https://polygon.blockscout.com"},
    8453:  {"name": "base",      "rpc": "https://mainnet.base.org",         "blockscout": "https://base.blockscout.com"},
    42161: {"name": "arbitrum",  "rpc": "https://arb1.arbitrum.io/rpc",     "blockscout": "https://arbitrum.blockscout.com"},
    10:    {"name": "optimism",  "rpc": "https://mainnet.optimism.io",      "blockscout": "https://optimism.blockscout.com"},
    43114: {"name": "avalanche", "rpc": "https://api.avax.network/ext/bc/C/rpc", "blockscout": None},
    59144: {"name": "linea",     "rpc": "https://rpc.linea.build",          "blockscout": None},
    534352:{"name": "scroll",    "rpc": "https://rpc.scroll.io",            "blockscout": "https://scroll.blockscout.com"},
    324:   {"name": "zksync",    "rpc": "https://mainnet.era.zksync.io",    "blockscout": None},
}

# Selector standar buat deteksi via ABI (offline) / ERC-165
ERC165_IDS = {"erc721": "0x80ac58cd", "erc1155": "0xd9b67a26", "erc721metadata": "0x5b5e139f"}


def get_w3(chain_id: int, rpc_url: Optional[str] = None) -> Web3:
    url = rpc_url or CHAINS.get(chain_id, {}).get("rpc")
    if not url:
        raise ValueError(f"chain {chain_id} belum ada di registry — kasih rpc_url manual")
    return Web3(Web3.HTTPProvider(url))


# ───────────────────────── ABI fetch (keyless) ─────────────────────────
def fetch_abi(address: str, chain_id: int) -> Optional[list]:
    """Sourcify dulu (verified source, no key), fallback Blockscout."""
    addr = Web3.to_checksum_address(address)
    # 1) Sourcify — coba full_match lalu partial_match
    for match in ("full_match", "partial_match"):
        try:
            url = f"https://repo.sourcify.dev/contracts/{match}/{chain_id}/{addr}/metadata.json"
            r = httpx.get(url, timeout=10)
            if r.status_code == 200:
                meta = r.json()
                abi = meta.get("output", {}).get("abi")
                if abi:
                    return abi
        except Exception:
            pass
    # 2) Blockscout (kalau chain-nya punya instance)
    bs = CHAINS.get(chain_id, {}).get("blockscout")
    if bs:
        try:
            r = httpx.get(f"{bs}/api/v2/smart-contracts/{addr}", timeout=10)
            if r.status_code == 200:
                abi = r.json().get("abi")
                if abi:
                    return abi
        except Exception:
            pass
    return None


# ───────────────────────── reader ─────────────────────────
@dataclass
class FunctionInfo:
    name: str
    inputs: list
    outputs: list
    state: str   # view | pure | nonpayable | payable


class ContractReader:
    def __init__(self, address: str, chain_id: int,
                 abi: Optional[list] = None, rpc_url: Optional[str] = None):
        self.chain_id = chain_id
        self.w3 = get_w3(chain_id, rpc_url)
        self.address = Web3.to_checksum_address(address)
        self.abi = abi or fetch_abi(self.address, chain_id)
        if self.abi is None:
            raise RuntimeError(
                f"ABI {self.address} (chain {chain_id}) gak ketemu — kontrak belum "
                f"verified di Sourcify/Blockscout. Kasih abi manual kalau punya.")
        self.contract = self.w3.eth.contract(address=self.address, abi=self.abi)

    def read_functions(self) -> list[FunctionInfo]:
        """Semua fungsi view/pure yang bisa dibaca tanpa tx."""
        out = []
        for item in self.abi:
            if item.get("type") == "function" and item.get("stateMutability") in ("view", "pure"):
                out.append(FunctionInfo(
                    item["name"],
                    [(i.get("name", ""), i["type"]) for i in item.get("inputs", [])],
                    [o["type"] for o in item.get("outputs", [])],
                    item["stateMutability"]))
        return out

    def read(self, fn_name: str, *args) -> Any:
        """Panggil fungsi read apa pun. Output otomatis ke-decode oleh web3."""
        fn = getattr(self.contract.functions, fn_name)
        return fn(*args).call()

    def detect_standard(self) -> list[str]:
        """ERC-165 on-chain kalau ada supportsInterface, plus heuristik ABI."""
        found = []
        names = {i.get("name") for i in self.abi if i.get("type") == "function"}
        # ERC-165 on-chain
        if "supportsInterface" in names:
            for label, iid in ERC165_IDS.items():
                try:
                    if self.contract.functions.supportsInterface(bytes.fromhex(iid[2:])).call():
                        found.append(label.upper())
                except Exception:
                    pass
        # heuristik ABI (offline-friendly)
        if {"transfer", "balanceOf", "totalSupply", "decimals"} <= names and "ERC721" not in found:
            found.append("ERC20")
        if {"ownerOf", "balanceOf"} <= names and "ERC721" not in found:
            found.append("ERC721?")
        if "balanceOfBatch" in names and "ERC1155" not in found:
            found.append("ERC1155?")
        return sorted(set(found))

    def resolve_proxy(self) -> Optional[str]:
        """Kalau ini proxy EIP-1967, return alamat implementasi sebenarnya."""
        raw = self.w3.eth.get_storage_at(self.address, _EIP1967_IMPL_SLOT)
        impl = "0x" + raw.hex()[-40:]
        if int(impl, 16) == 0:
            return None
        return Web3.to_checksum_address(impl)

    def token_info(self) -> dict:
        """Info cepat token ERC-20/721 (field yang gak ada di-skip)."""
        info = {}
        for fn in ("name", "symbol", "decimals", "totalSupply"):
            try:
                info[fn] = self.read(fn)
            except Exception:
                pass
        return info

    def summary(self) -> dict:
        impl = None
        try:
            impl = self.resolve_proxy()
        except Exception:
            pass
        return {
            "address": self.address,
            "chain": CHAINS.get(self.chain_id, {}).get("name", self.chain_id),
            "standards": self.detect_standard(),
            "proxy_implementation": impl,
            "read_functions": [f.name for f in self.read_functions()],
            "token_info": self.token_info(),
        }


if __name__ == "__main__":
    # tes logika offline pakai ABI mock (tanpa network)
    erc20_abi = [
        {"type": "function", "name": "name", "inputs": [], "outputs": [{"type": "string"}], "stateMutability": "view"},
        {"type": "function", "name": "symbol", "inputs": [], "outputs": [{"type": "string"}], "stateMutability": "view"},
        {"type": "function", "name": "decimals", "inputs": [], "outputs": [{"type": "uint8"}], "stateMutability": "view"},
        {"type": "function", "name": "totalSupply", "inputs": [], "outputs": [{"type": "uint256"}], "stateMutability": "view"},
        {"type": "function", "name": "balanceOf", "inputs": [{"name": "o", "type": "address"}], "outputs": [{"type": "uint256"}], "stateMutability": "view"},
        {"type": "function", "name": "transfer", "inputs": [{"type": "address"}, {"type": "uint256"}], "outputs": [{"type": "bool"}], "stateMutability": "nonpayable"},
    ]
    r = ContractReader.__new__(ContractReader)
    r.abi = erc20_abi
    print("read functions:", [f.name for f in r.read_functions()])
    print("detect standard:", r.detect_standard())
    print("chains supported:", [c["name"] for c in CHAINS.values()])
