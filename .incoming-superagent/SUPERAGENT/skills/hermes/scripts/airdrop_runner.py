"""
hermes/airdrop_runner.py — Multi-wallet airdrop task orchestrator.

Pattern:
1. Definisikan tasks (callable async yang menerima `wallet` + `**params`)
2. Daftarkan wallet (semua user-owned, dari WalletManager)
3. Jalankan scheduler dengan randomized delay + amount jitter
4. Persist progress di SQLite — bisa resume kalau crash
"""
from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("hermes.runner")


@dataclass
class TaskSpec:
    name: str
    func: Callable[..., Awaitable]   # async function
    base_amount: Optional[float] = None
    params: dict = field(default_factory=dict)
    frequency: str = "once"           # "daily" | "weekly" | "once"


@dataclass
class WalletRef:
    label: str
    address: str
    chain: str
    private_key_loader: Callable[[], str]   # lazy load supaya secret hanya di-decrypt saat dipakai


# ─────────────────────────── STATE ───────────────────────────

class RunState:
    def __init__(self, db_path: Path = Path.home() / ".hermes" / "runs.db"):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("""CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT, wallet TEXT, task TEXT,
            status TEXT, tx_hash TEXT, error TEXT, ts INTEGER,
            PRIMARY KEY(run_id, wallet, task))""")
        self.conn.commit()

    def already_done(self, run_id: str, wallet: str, task: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM runs WHERE run_id=? AND wallet=? AND task=?",
            (run_id, wallet, task)).fetchone()
        return bool(row and row[0] == "success")

    def record(self, run_id, wallet, task, status, tx_hash=None, error=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
            (run_id, wallet, task, status, tx_hash, error, int(time.time())))
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "RunState":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ─────────────────────────── SCHEDULER ───────────────────────────

class WalletScheduler:
    def __init__(self, wallets: list[WalletRef], tasks: list[TaskSpec],
                 delay_between_wallets_min=(5, 180),   # menit
                 delay_between_tasks_sec=(60, 300),    # detik
                 amount_jitter_pct: float = 15,
                 run_id: Optional[str] = None,
                 dry_run: bool = False):
        self.wallets = wallets
        self.tasks = tasks
        self.dwm = delay_between_wallets_min
        self.dts = delay_between_tasks_sec
        self.jitter = amount_jitter_pct
        self.run_id = run_id or str(uuid.uuid4())
        self.dry_run = dry_run
        self.state = RunState()

    def _jitter(self, base: Optional[float]) -> Optional[float]:
        if base is None:
            return None
        pct = random.uniform(-self.jitter / 100, self.jitter / 100)
        return round(base * (1 + pct), 6)

    async def _execute_task(self, wallet: WalletRef, task: TaskSpec):
        if self.state.already_done(self.run_id, wallet.label, task.name):
            logger.info(f"[{wallet.label}] skip {task.name} (sudah success)")
            return

        params = dict(task.params or {})
        if task.base_amount is not None:
            params["amount"] = self._jitter(task.base_amount)

        if self.dry_run:
            logger.info(f"[DRY] [{wallet.label}] {task.name} params={params}")
            self.state.record(self.run_id, wallet.label, task.name,
                              "dry_run", error=None)
            return

        try:
            result = await task.func(wallet=wallet, **params)
            tx = getattr(result, "tx_hash", None) or (result.get("tx_hash") if isinstance(result, dict) else None)
            self.state.record(self.run_id, wallet.label, task.name, "success", tx_hash=tx)
            logger.info(f"[{wallet.label}] {task.name} OK tx={tx}")
        except Exception as e:
            self.state.record(self.run_id, wallet.label, task.name, "error", error=str(e))
            logger.exception(f"[{wallet.label}] {task.name} GAGAL: {e}")

    async def run(self):
        order = self.wallets[:]
        random.shuffle(order)
        logger.info(f"Run {self.run_id} dimulai — {len(order)} wallets, {len(self.tasks)} tasks")

        for i, w in enumerate(order):
            tasks_shuffled = self.tasks[:]
            random.shuffle(tasks_shuffled)
            for t in tasks_shuffled:
                await self._execute_task(w, t)
                # delay antar task di wallet yang sama
                wait_s = random.uniform(*self.dts)
                logger.debug(f"sleep {wait_s:.0f}s before next task")
                if not self.dry_run:
                    await asyncio.sleep(wait_s)

            if i < len(order) - 1:
                wait_min = random.uniform(*self.dwm)
                logger.info(f"[{w.label}] selesai, sleep {wait_min:.0f}m sebelum wallet berikutnya")
                if not self.dry_run:
                    await asyncio.sleep(wait_min * 60)

        logger.info(f"Run {self.run_id} selesai")


# ─────────────────────────── EXAMPLE TASKS ───────────────────────────

# Task harus async function dengan signature `(wallet: WalletRef, **params) -> result`

async def task_swap_loop(wallet: WalletRef, amount: float,
                          base_token: str, quote_token: str,
                          rounds: int = 1, swap_back: bool = True):
    """Stub. Implementasi: load private key, build w3 + account, panggil swap_engine."""
    from web3 import Web3
    from eth_account import Account
    from .swap_engine import swap_evm, NATIVE_SENTINEL_EVM

    # User harus provide RPC URL per chain (best practice: env var per chain_id)
    import os
    rpc = os.environ.get(f"RPC_{wallet.chain.upper()}")
    if not rpc:
        raise RuntimeError(f"set RPC_{wallet.chain.upper()} env var")

    w3 = Web3(Web3.HTTPProvider(rpc))
    pk = wallet.private_key_loader()
    account = Account.from_key(pk)

    amount_wei = w3.to_wei(amount, "ether")
    result = await swap_evm(w3, account, base_token, quote_token, amount_wei, slippage_pct=1.0)
    return result


async def task_daily_checkin(wallet: WalletRef, contract: str):
    """Stub: call contract.checkIn()."""
    from web3 import Web3
    from eth_account import Account
    import os

    rpc = os.environ[f"RPC_{wallet.chain.upper()}"]
    w3 = Web3(Web3.HTTPProvider(rpc))
    account = Account.from_key(wallet.private_key_loader())

    abi = [{"name": "checkIn", "inputs": [], "type": "function"}]
    c = w3.eth.contract(address=Web3.to_checksum_address(contract), abi=abi)
    tx = c.functions.checkIn().build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 100000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    })
    signed = account.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
    return {"tx_hash": h}


# ─────────────────────────── USAGE EXAMPLE ───────────────────────────

if __name__ == "__main__":
    """
    # contoh main:
    from .wallet_manager import WalletManager
    import os

    wm = WalletManager(master_password=os.environ["HERMES_MASTER_PW"])
    wallet_refs = [
        WalletRef(label=w["label"], address=w["address"], chain=w["chain"],
                  private_key_loader=lambda lbl=w["label"]: wm.get(lbl).private_key)
        for w in wm.list_wallets() if w["chain"] == "evm"
    ]

    tasks = [
        TaskSpec(
            name="linea_swap_volume",
            func=task_swap_loop,
            base_amount=0.005,   # ETH
            params={"base_token": NATIVE_SENTINEL_EVM,
                    "quote_token": "0x...USDC on linea...",
                    "rounds": 1, "swap_back": True},
            frequency="daily",
        ),
    ]

    scheduler = WalletScheduler(wallet_refs, tasks, dry_run=False)
    asyncio.run(scheduler.run())
    """
    pass
