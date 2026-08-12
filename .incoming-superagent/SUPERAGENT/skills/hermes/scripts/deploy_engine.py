"""
hermes/deploy_engine.py — Crypto Dev: Compile / Test / Deploy / Verify  (v4.0)

Bikin agent bisa *bangun* kontrak, bukan cuma manggil yang udah ada:
- compile & test via Foundry (forge) — gratis, lokal
- deploy ke chain mana pun → LEWAT governor (deploy = tx, ngeluarin gas)
- verify otomatis ke Sourcify (KEYLESS)
- CREATE2 deterministic deploy (address sama di semua chain)

Deploy itu tx yang ngeluarin dana (gas) → gerbang governor WAJIB, sama kayak
contract_writer. Compile/test read-only (lokal) → gak perlu gate.

Dependency: web3, httpx. Tools eksternal: foundry (forge) buat compile/test.
"""
from __future__ import annotations

import json
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Awaitable

from web3 import Web3
from eth_account.signers.local import LocalAccount

try:
    from .contract_reader import get_w3, CHAINS
except ImportError:
    from contract_reader import get_w3, CHAINS

# Deterministic deployment proxy (Arachnid) — sama di hampir semua chain EVM
CREATE2_FACTORY = "0x4e59b44847b379578588920cA78FbF26c0B4956C"


# ───────────────────────── compile & test (lokal, gratis) ─────────────────────────
def _need_forge():
    if shutil.which("forge") is None:
        raise RuntimeError("foundry belum ada. Install: curl -L https://foundry.paradigm.xyz | bash && foundryup")


def compile_foundry(project_dir: str) -> dict:
    _need_forge()
    r = subprocess.run(["forge", "build", "--json"], cwd=project_dir,
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return {"ok": False, "error": r.stderr or r.stdout}
    return {"ok": True, "out": r.stdout}


def run_tests(project_dir: str, match: Optional[str] = None) -> dict:
    _need_forge()
    cmd = ["forge", "test", "-vv"]
    if match:
        cmd += ["--match-test", match]
    r = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, timeout=600)
    return {"ok": r.returncode == 0, "out": r.stdout, "err": r.stderr}


def load_artifact(project_dir: str, contract_name: str) -> dict:
    """Ambil bytecode + abi dari out/ Foundry."""
    path = Path(project_dir) / "out" / f"{contract_name}.sol" / f"{contract_name}.json"
    art = json.loads(path.read_text())
    return {"abi": art["abi"], "bytecode": art["bytecode"]["object"], "metadata": art.get("metadata")}


# ───────────────────────── address derivation ─────────────────────────
def compute_create2_address(salt: bytes, init_code: bytes,
                            factory: str = CREATE2_FACTORY) -> str:
    """EIP-1014: keccak(0xff ++ factory ++ salt ++ keccak(init_code))[12:]"""
    pre = b"\xff" + bytes.fromhex(factory[2:]) + salt.rjust(32, b"\x00") + Web3.keccak(init_code)
    return Web3.to_checksum_address(Web3.keccak(pre)[12:])


# ───────────────────────── deploy (GATED) ─────────────────────────
@dataclass
class DeployResult:
    status: str                  # "deployed" | "blocked" | "halt" | "rejected" | "error"
    detail: str = ""
    address: Optional[str] = None
    tx_hash: Optional[str] = None


async def deploy(abi: list, bytecode: str, account: LocalAccount, chain_id: int,
                 constructor_args: Optional[list] = None,
                 rpc_url: Optional[str] = None,
                 usd_value: Optional[float] = None,
                 confirm_cb: Optional[Callable[[dict], Awaitable[bool]]] = None,
                 private: bool = False) -> DeployResult:
    """Deploy kontrak — gerbang penuh: build → estimate → governor → konfirmasi → send → record."""
    from .governor import SpendGovernor, TxIntent  # noqa
    w3 = get_w3(chain_id, rpc_url)
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    try:
        tx = contract.constructor(*(constructor_args or [])).build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address, "pending"),
            "chainId": chain_id,
        })
        tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    except Exception as e:
        return DeployResult("error", f"build/estimate gagal (kemungkinan revert constructor): {e}")

    gov = SpendGovernor()
    intent = TxIntent(wallet=account.address, chain_id=chain_id, action="deploy_contract",
                      usd_value=usd_value, simulated_ok=True,
                      gas_price_wei=tx.get("maxFeePerGas") or tx.get("gasPrice"))
    decision = gov.authorize(intent)
    if not decision.allowed:
        return DeployResult(decision.verdict, decision.summary())

    if confirm_cb is not None:
        if not await confirm_cb({"action": "DEPLOY contract", "chain": chain_id,
                                 "constructor_args": [str(a) for a in (constructor_args or [])],
                                 "decision": decision.summary()}):
            return DeployResult("rejected", "user menolak deploy")

    signed = account.sign_transaction(tx)
    try:
        if private:
            from .mev import send_private_tx
            res = send_private_tx(signed.raw_transaction, chain_id, prefer="flashbots", public_w3=w3)
            tx_hash = res.tx_hash
            if res.status == "error":
                return DeployResult("error", res.error or "private send gagal")
        else:
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    except Exception as e:
        return DeployResult("error", repr(e))

    gov.record(intent, tx_hash)
    return DeployResult("deployed", "ok", address=receipt.contractAddress, tx_hash=tx_hash)


# ───────────────────────── verify (Sourcify, keyless) ─────────────────────────
def verify_sourcify(address: str, chain_id: int, metadata_json: str,
                    sources: dict[str, str]) -> dict:
    """Upload metadata + source ke Sourcify. KEYLESS. sources = {path: solidity_source}."""
    import httpx
    files = {"metadata.json": metadata_json, **sources}
    payload = {"address": Web3.to_checksum_address(address), "chain": str(chain_id),
               "files": files}
    try:
        r = httpx.post("https://sourcify.dev/server/verify", json=payload, timeout=30)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": repr(e)}


if __name__ == "__main__":
    # tes derivasi CREATE2 lawan vektor EIP-1014 (offline, deterministik)
    addr = compute_create2_address(salt=b"\x00" * 32, init_code=b"\x00",
                                   factory="0x0000000000000000000000000000000000000000")
    expected = "0x4D1A2e2bB4F88F0250f26Ffff098B0b30B26Bf38"
    print("CREATE2 EIP-1014 vector match (byte-equal):", addr.lower() == expected.lower(), f"({addr})")
    print("forge tersedia:", shutil.which("forge") is not None)
    print("CREATE2 factory:", CREATE2_FACTORY)
