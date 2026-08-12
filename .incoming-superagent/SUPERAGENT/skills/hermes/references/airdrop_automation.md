# Airdrop Automation (Multi-Wallet User-Owned)

Reference untuk menjalankan task airdrop di banyak wallet milik user — bridge, swap rutin, daily check-in, claim, dst.

## Penting: Sybil Detection

**Hermes harus mengingatkan user satu kali per sesi:**

> "Banyak proyek airdrop (LayerZero, zkSync, Linea, dll) punya tim deteksi sybil yang akan blacklist semua wallet terkait jika polanya terdeteksi (timing identik, funding dari source yang sama, on-chain graph terhubung, transaction pattern identik). Saya bisa randomisasi delay, amount, dan order, tapi tidak menjamin kelolosan dari deteksi sybil. ToS proyek adalah tanggung jawab Anda."

User boleh melanjutkan setelah acknowledge.

## Arsitektur

```
┌─────────────────────────────────────┐
│  TaskQueue (per wallet)             │
│  - bridge_to_arbitrum               │
│  - swap_eth_usdc                    │
│  - lend_aave                        │
│  - daily_checkin_someproject        │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Scheduler                          │
│  - random delay (e.g. 5–120 min)    │
│  - random amount within range       │
│  - random execution order           │
│  - per-wallet RPC rotation          │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Executor (one wallet at a time)    │
│  - load encrypted key               │
│  - run task                         │
│  - log result                       │
│  - sleep random                     │
└─────────────────────────────────────┘
```

## Anti-Pattern Randomization

Biar wallet tidak terlihat seperti bot army:

```python
import random, asyncio
from datetime import datetime, timedelta

class WalletScheduler:
    def __init__(self, wallets: list[dict], tasks: list[callable],
                 delay_range_min=(5, 180),  # menit antar wallet
                 jitter_amount_pct=15):     # variance amount ±15%
        self.wallets = wallets
        self.tasks = tasks
        self.delay_range = delay_range_min
        self.jitter = jitter_amount_pct

    def jitter_amount(self, base: float) -> float:
        pct = random.uniform(-self.jitter/100, self.jitter/100)
        return round(base * (1 + pct), 6)

    async def run(self):
        order = self.wallets[:]
        random.shuffle(order)            # randomisasi urutan eksekusi
        
        for w in order:
            shuffled_tasks = self.tasks[:]
            random.shuffle(shuffled_tasks)  # task order pun acak per wallet
            
            for task in shuffled_tasks:
                try:
                    amount = self.jitter_amount(task.base_amount)
                    await task.execute(w, amount=amount)
                except Exception as e:
                    log_error(w["label"], task.name, str(e))
                
                # delay antar task (di wallet yang sama)
                await asyncio.sleep(random.uniform(60, 300))
            
            # delay antar wallet
            delay_min = random.uniform(*self.delay_range)
            await asyncio.sleep(delay_min * 60)
```

Tambahan trik:

- **Funding source diversifikasi**: kalau bisa, kirim gas dari source berbeda (CEX berbeda, atau bridge dari berbagai chain). Funding ratusan wallet dari satu CEX dengan timing berdekatan = red flag besar.
- **RPC rotation**: jangan pakai satu Alchemy/Infura key untuk semua wallet — beberapa proyek melihat heuristik ini.
- **Browser fingerprint** (kalau task ada UI-nya): pakai proxy berbeda + browser profile berbeda per wallet. Skill ini fokus on-chain, tapi worth disebut.

## Task Templates

### 1. Bridge (LayerZero, Stargate, Across, Hop)

```python
async def bridge_stargate(w3, account, from_chain_id: int, to_chain_id: int,
                           token: str, amount: int):
    """Stargate v2 universal bridge."""
    STARGATE_ROUTER = {1: "0x...", 42161: "0x...", 8453: "0x..."}
    LZ_CHAIN_ID = {1: 30101, 42161: 30110, 8453: 30184, ...}
    
    router = w3.eth.contract(address=STARGATE_ROUTER[from_chain_id], abi=STG_ABI)
    
    # quote LZ fee
    native_fee, lz_fee = router.functions.quoteOFT(...).call()
    
    # build send tx
    tx = router.functions.send(
        LZ_CHAIN_ID[to_chain_id],
        to_bytes32(account.address),
        amount,
        amount * 995 // 1000,   # min received (0.5% slippage)
        {"refundAddress": account.address, ...},
        {"nativeFee": native_fee, "zroFee": 0},
        b"",
    ).build_transaction({
        "from": account.address,
        "value": native_fee,
        ...
    })
    # sign & send
```

### 2. Daily Swap Rutin (untuk volume di chain target)

```python
async def daily_swap_loop(w3, account, base="USDC", quote="ETH",
                          min_usd=10, max_usd=50, swap_back=True):
    """Swap kecil USDC→ETH→USDC, biar ada activity tapi loss minimal (cuma fee)."""
    amount_usd = random.uniform(min_usd, max_usd)
    # 1. swap base → quote
    tx1 = await swap_via_aggregator(w3, account, base, quote, amount_usd)
    await asyncio.sleep(random.uniform(60, 600))
    if swap_back:
        # 2. swap quote → base (full balance dari leg pertama)
        tx2 = await swap_via_aggregator(w3, account, quote, base, "all")
    return [tx1, tx2 if swap_back else None]
```

### 3. Lend/Borrow (Aave, Compound, dst)

```python
async def aave_supply_withdraw(w3, account, asset: str, amount: int, hold_seconds: int):
    pool = w3.eth.contract(address=AAVE_POOL, abi=AAVE_POOL_ABI)
    approve_if_needed(w3, account, asset, AAVE_POOL, amount)
    # supply
    tx = pool.functions.supply(asset, amount, account.address, 0).build_transaction({...})
    # ... send
    
    await asyncio.sleep(hold_seconds + random.randint(0, 3600))
    
    # withdraw
    tx = pool.functions.withdraw(asset, MAX_UINT, account.address).build_transaction({...})
    # ... send
```

### 4. Daily Check-in (kalau ada signMessage / contract call simple)

```python
async def daily_checkin(w3, account, project_contract: str):
    abi = [{"name": "checkIn", "inputs": [], "type": "function"}]
    c = w3.eth.contract(address=project_contract, abi=abi)
    tx = c.functions.checkIn().build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 100000, ...
    })
    # sign & send
```

### 5. Claim Airdrop (read merkle proof, call claim)

```python
async def claim_airdrop(w3, account, distributor: str, amount: int, proof: list[bytes]):
    abi = [{"name": "claim",
            "inputs": [{"name": "account", "type": "address"},
                       {"name": "amount", "type": "uint256"},
                       {"name": "proof", "type": "bytes32[]"}],
            "type": "function"}]
    c = w3.eth.contract(address=distributor, abi=abi)
    tx = c.functions.claim(account.address, amount, proof).build_transaction({...})
    # sign & send
```

Untuk dapat merkle proof: biasanya proyek expose endpoint `/api/proof?address={addr}` → return proof + amount.

## Multi-Chain Multi-Task Orchestration

```python
# scripts/airdrop_runner.py pattern

TASKS = [
    {
        "name": "linea_volume",
        "chain_id": 59144,
        "module": "tasks.swap_loop",
        "params": {"base": "USDC", "quote": "WETH", "min_usd": 5, "max_usd": 30,
                   "rounds": 3, "swap_back": True},
        "frequency": "daily",
    },
    {
        "name": "base_bridge_back",
        "chain_id": 8453,
        "module": "tasks.bridge",
        "params": {"to_chain_id": 1, "token": "ETH", "amount": 0.001},
        "frequency": "weekly",
    },
    {
        "name": "scroll_daily_checkin",
        "chain_id": 534352,
        "module": "tasks.checkin",
        "params": {"contract": "0x..."},
        "frequency": "daily",
    },
]

async def run_daily(wallets: list, tasks: list):
    daily = [t for t in tasks if t["frequency"] == "daily"]
    scheduler = WalletScheduler(wallets, daily)
    await scheduler.run()
```

## Logging & Resume

Crash di tengah batch 100-wallet itu pasti terjadi. Persist state:

```python
import sqlite3

def init_db():
    conn = sqlite3.connect(".hermes/runs.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT, wallet TEXT, task TEXT,
        status TEXT, tx_hash TEXT, error TEXT,
        ts INTEGER, PRIMARY KEY(run_id, wallet, task))""")
    return conn

def already_done(conn, run_id, wallet, task):
    cur = conn.execute("SELECT status FROM runs WHERE run_id=? AND wallet=? AND task=?",
                       (run_id, wallet, task))
    row = cur.fetchone()
    return row and row[0] == "success"
```

Saat resume, skip wallet+task yang sudah `success`.

## Gas Funding Strategy

Sebelum batch run, pre-check gas balance setiap wallet. Kalau ada wallet dengan gas habis:

```python
async def gas_topup_plan(wallets, min_gas_eth=0.005, refill_eth=0.01):
    needs = []
    for w in wallets:
        bal = await get_native_balance(w["address"], w["chain_id"])
        if bal < min_gas_eth:
            needs.append({"wallet": w, "deficit": refill_eth - bal})
    return needs   # tampilkan ke user; user yang putuskan top up dari mana
```

Hermes **tidak boleh** otomatis kirim gas dari satu central wallet ke ratusan wallet — itu jejak yang paling kelihatan ke sybil hunter. Tanyakan user dulu strategi funding.

## Konfirmasi Sebelum Batch Run (template)

```
🤖 BATCH PLAN
─────────────
Wallets:      24 (semua user-owned, terenkripsi)
Chain:        Linea (59144)
Tasks:        swap_loop (volume rotation), daily_checkin
Delay range:  5–180 menit antar wallet
Amount jitter: ±15%
Estimated total time: ~6 jam
Estimated total gas (semua wallet): ~0.018 ETH (~$45)
─────────────
⚠️  Reminder: deteksi sybil bisa membatalkan semua wallet ini sekaligus.
Lanjut? (yes/no/dry-run)
```

`dry-run` mode: jalankan semua kalkulasi + tampilkan tx parameter, tapi jangan broadcast.
