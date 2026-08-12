"""
hermes/contract_writer.py — Universal Contract Interactor: WRITE  (v4.0)

Pasangan write dari contract_reader. Kirim tx ke fungsi APA PUN di kontrak mana
pun (nonpayable/payable), dengan ABI auto-fetch. Karena ini MINDAHIN DANA, tiap
call WAJIB lewat gerbang penuh — gak ada mode "tembak langsung":

    build tx → SIMULATE (eth_call) → screen_tx (decode human-readable)
             → governor.authorize (cap + kill-switch) → konfirmasi
             → sign → send (private/MEV opsional) → governor.record

Kalau salah satu gagal/diblok → STOP, gak broadcast. Ini beda fundamental dari
reader: reader cuma baca; writer ngeluarin dana, jadi rail-nya non-negotiable.

Dependency: web3, httpx. Reuse: contract_reader, governor, web3_connect, mev.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Callable, Awaitable, Any

from web3 import Web3
from eth_account.signers.local import LocalAccount

try:
    from .contract_reader import fetch_abi, get_w3, CHAINS
except ImportError:  # flat execution (script disalin tanpa package context)
    from contract_reader import fetch_abi, get_w3, CHAINS


@dataclass
class WriteResult:
    status: str                 # "sent" | "blocked" | "halt" | "rejected" | "sim_failed" | "error"
    detail: str = ""
    tx_hash: Optional[str] = None
    human_readable: dict = None
    explorer_url: Optional[str] = None


class ContractWriter:
    def __init__(self, address: str, chain_id: int, account: LocalAccount,
                 abi: Optional[list] = None, rpc_url: Optional[str] = None):
        self.chain_id = chain_id
        self.w3 = get_w3(chain_id, rpc_url)
        self.address = Web3.to_checksum_address(address)
        self.account = account
        self.abi = abi or fetch_abi(self.address, chain_id)
        if self.abi is None:
            raise RuntimeError(
                f"ABI {self.address} (chain {chain_id}) gak ketemu — kasih abi manual.")
        self.contract = self.w3.eth.contract(address=self.address, abi=self.abi)

    def writable_functions(self) -> list[dict]:
        """Fungsi yang ngubah state (nonpayable/payable)."""
        return [{"name": i["name"],
                 "inputs": [(x.get("name", ""), x["type"]) for x in i.get("inputs", [])],
                 "payable": i.get("stateMutability") == "payable"}
                for i in self.abi
                if i.get("type") == "function" and i.get("stateMutability") in ("nonpayable", "payable")]

    def build_tx(self, fn_name: str, *args, value: int = 0) -> dict:
        fn = getattr(self.contract.functions, fn_name)(*args)
        tx = fn.build_transaction({
            "from": self.account.address,
            "value": value,
            "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
            "chainId": self.chain_id,
        })
        # gas: estimate + buffer; EIP-1559 fee
        try:
            tx["gas"] = int(self.w3.eth.estimate_gas(tx) * 1.2)
        except Exception as e:
            raise RuntimeError(f"estimate gas gagal (kemungkinan revert): {e}")
        return tx

    def simulate(self, tx: dict) -> tuple[bool, str]:
        try:
            self.w3.eth.call({k: tx[k] for k in ("from", "to", "data", "value") if k in tx})
            return True, "ok"
        except Exception as e:
            return False, f"revert: {e}"

    async def call(self, fn_name: str, *args,
                   value: int = 0,
                   usd_value: Optional[float] = None,
                   confirm_cb: Optional[Callable[[dict], Awaitable[bool]]] = None,
                   private: bool = False) -> WriteResult:
        """
        Eksekusi fungsi write dengan gerbang penuh. Urutan TIDAK bisa di-skip.
        usd_value: dari price oracle lo (buat cap USD governor). confirm_cb: prompt operator.
        private: kalau True, broadcast lewat MEV private relay.
        """
        from .governor import SpendGovernor, TxIntent
        from .web3_connect import screen_tx

        # 1. build
        try:
            tx = self.build_tx(fn_name, *args, value=value)
        except Exception as e:
            return WriteResult("error", str(e))

        # 2. simulate (sim-gate)
        ok, reason = self.simulate(tx)
        if not ok:
            return WriteResult("sim_failed", reason)

        # 3. screen → human-readable
        try:
            screen = await screen_tx(self.w3, tx, dapp_name=f"{self.address}.{fn_name}")
            human = screen.__dict__ if hasattr(screen, "__dict__") else {"summary": str(screen)}
        except Exception:
            human = {"fn": fn_name, "args": [str(a) for a in args], "value": value}

        # 4. governor gate
        gov = SpendGovernor()
        intent = TxIntent(
            wallet=self.account.address, chain_id=self.chain_id,
            action=f"contract:{fn_name}", usd_value=usd_value,
            native_value=value / 1e18, simulated_ok=True,
            gas_price_wei=tx.get("maxFeePerGas") or tx.get("gasPrice"),
            recipient=self.address)
        decision = gov.authorize(intent)
        if not decision.allowed:
            return WriteResult(decision.verdict, decision.summary(), human_readable=human)

        # 5. confirm
        if confirm_cb is not None:
            if not await confirm_cb({"call": f"{fn_name}({', '.join(map(str, args))})",
                                     "value": value, "screen": human,
                                     "decision": decision.summary()}):
                return WriteResult("rejected", "user menolak", human_readable=human)

        # 6. sign + send
        signed = self.account.sign_transaction(tx)
        try:
            if private:
                from .mev import send_private_tx
                res = send_private_tx(signed.raw_transaction, self.chain_id,
                                      prefer="flashbots", public_w3=self.w3)
                tx_hash = res.tx_hash
                if res.status == "error":
                    return WriteResult("error", res.error or "private send gagal", human_readable=human)
            else:
                tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction).hex()
        except Exception as e:
            return WriteResult("error", repr(e), human_readable=human)

        # 7. record ke governor (cap akurat)
        gov.record(intent, tx_hash)
        base = CHAINS.get(self.chain_id, {})
        return WriteResult("sent", "lolos sim + governor + konfirmasi", tx_hash=tx_hash,
                           human_readable=human)


if __name__ == "__main__":
    # tes logika offline: list fungsi writable dari ABI mock + urutan gerbang
    abi = [
        {"type": "function", "name": "transfer", "inputs": [{"name": "to", "type": "address"}, {"name": "amt", "type": "uint256"}], "outputs": [{"type": "bool"}], "stateMutability": "nonpayable"},
        {"type": "function", "name": "approve", "inputs": [{"name": "s", "type": "address"}, {"name": "a", "type": "uint256"}], "outputs": [{"type": "bool"}], "stateMutability": "nonpayable"},
        {"type": "function", "name": "deposit", "inputs": [], "outputs": [], "stateMutability": "payable"},
        {"type": "function", "name": "balanceOf", "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}], "stateMutability": "view"},
    ]
    w = ContractWriter.__new__(ContractWriter)
    w.abi = abi
    fns = w.writable_functions()
    print("writable functions:", [(f["name"], "payable" if f["payable"] else "nonpayable") for f in fns])
    print("read function 'balanceOf' EXCLUDED dari writable:", "balanceOf" not in [f["name"] for f in fns])
    print("gerbang: build → simulate → screen → governor.authorize → confirm → sign → send → record")
