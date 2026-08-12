# DeFi Operations

Reference untuk operasi DeFi: lending/borrowing, staking, liquidity provision, dan perpetuals trading.

## Lending & Borrowing

### Aave V3 (Multi-chain)

```python
AAVE_V3_POOLS = {
    1: "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",          # Ethereum
    42161: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",      # Arbitrum
    10: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",         # Optimism
    137: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",        # Polygon
    8453: "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",       # Base
    43114: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",      # Avalanche
}

AAVE_POOL_ABI = [
    {"name": "supply", "inputs": [
        {"name": "asset", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "onBehalfOf", "type": "address"},
        {"name": "referralCode", "type": "uint16"},
    ], "outputs": [], "type": "function"},
    {"name": "withdraw", "inputs": [
        {"name": "asset", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "to", "type": "address"},
    ], "outputs": [{"type": "uint256"}], "type": "function"},
    {"name": "borrow", "inputs": [
        {"name": "asset", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "interestRateMode", "type": "uint256"},
        {"name": "referralCode", "type": "uint16"},
        {"name": "onBehalfOf", "type": "address"},
    ], "outputs": [], "type": "function"},
    {"name": "repay", "inputs": [
        {"name": "asset", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "interestRateMode", "type": "uint256"},
        {"name": "onBehalfOf", "type": "address"},
    ], "outputs": [{"type": "uint256"}], "type": "function"},
    {"name": "getUserAccountData", "inputs": [{"name": "user", "type": "address"}],
     "outputs": [
        {"name": "totalCollateralBase", "type": "uint256"},
        {"name": "totalDebtBase", "type": "uint256"},
        {"name": "availableBorrowsBase", "type": "uint256"},
        {"name": "currentLiquidationThreshold", "type": "uint256"},
        {"name": "ltv", "type": "uint256"},
        {"name": "healthFactor", "type": "uint256"},
     ], "stateMutability": "view", "type": "function"},
]

async def aave_supply(w3, account, asset: str, amount: int):
    pool_addr = AAVE_V3_POOLS[w3.eth.chain_id]
    pool = w3.eth.contract(address=pool_addr, abi=AAVE_POOL_ABI)
    
    approve_if_needed(w3, account, asset, pool_addr, amount)
    
    tx = pool.functions.supply(asset, amount, account.address, 0).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "gas": 400000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    })
    signed = account.sign_transaction(tx)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()

async def aave_borrow(w3, account, asset: str, amount: int, variable_rate: bool = True):
    pool_addr = AAVE_V3_POOLS[w3.eth.chain_id]
    pool = w3.eth.contract(address=pool_addr, abi=AAVE_POOL_ABI)
    
    # Cek health factor sebelum borrow — kalau < 1.5 risky
    data = pool.functions.getUserAccountData(account.address).call()
    available_borrow = data[2]   # dalam base unit (USD * 1e8)
    
    interest_mode = 2 if variable_rate else 1
    tx = pool.functions.borrow(asset, amount, interest_mode, 0, account.address)\
        .build_transaction({...})
    # ... sign & send

async def aave_health(w3, account) -> dict:
    pool = w3.eth.contract(address=AAVE_V3_POOLS[w3.eth.chain_id], abi=AAVE_POOL_ABI)
    data = pool.functions.getUserAccountData(account.address).call()
    return {
        "collateral_usd": data[0] / 1e8,
        "debt_usd": data[1] / 1e8,
        "available_borrow_usd": data[2] / 1e8,
        "ltv_pct": data[4] / 100,
        "health_factor": data[5] / 1e18,    # > 1 aman, < 1 liquidatable
    }
```

### Compound V3 (Comet)

```python
# Compound V3 punya satu Comet per market (USDC market, ETH market, dst)
COMPOUND_V3_USDC = {
    1: "0xc3d688B66703497DAA19211EEdff47f25384cdc3",       # Ethereum
    42161: "0x9c4ec768c28520B50860ea7a15bd7213a9fF58bf",   # Arbitrum
    8453: "0xb125E6687d4313864e53df431d5425969c15Eb2F",    # Base
}

# Function utama: supply(address asset, uint amount), withdraw, borrow (via withdrawTo base)
```

### Morpho Blue (Lebih efisien dari Aave)

```python
# Morpho Blue: market-based, setiap market punya loan_token + collateral_token + oracle + IRM + LLTV
MORPHO_BLUE = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"   # mainnet, sama di sebagian chain lain

# Pakai SDK @morpho-org atau API: https://api.morpho.org
```

## Liquid Staking

### Lido (stETH)

```python
LIDO_STETH = "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84"   # Ethereum

LIDO_ABI = [{"name": "submit", "inputs": [{"name": "_referral", "type": "address"}],
             "outputs": [{"type": "uint256"}], "stateMutability": "payable", "type": "function"}]

async def lido_stake(w3, account, amount_eth: float):
    lido = w3.eth.contract(address=LIDO_STETH, abi=LIDO_ABI)
    tx = lido.functions.submit("0x0000000000000000000000000000000000000000")\
        .build_transaction({
            "from": account.address,
            "value": w3.to_wei(amount_eth, "ether"),
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 200000,
        })
    signed = account.sign_transaction(tx)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()
```

### Marinade (mSOL) / Jito (JitoSOL) — Solana

```python
# Marinade SDK: https://github.com/marinade-finance/marinade-ts-sdk (Python wrapping ada di komunitas)
# Jito: pakai stake pool program — https://github.com/jito-foundation/stake-pool

# Pattern singkat: deposit SOL → terima mSOL/JitoSOL. Unstake via instant unstake (fee 0.1-0.3%)
# atau delayed (1 epoch ~3 hari).
```

### Restaking (EigenLayer, Symbiotic)

```python
# EigenLayer entry: deposit LST (stETH, etc) ke StrategyManager
EIGEN_STRATEGY_MANAGER = "0x858646372CC42E1A627fcE94aa7A7033e7CF075A"

EIGEN_ABI = [{"name": "depositIntoStrategy",
              "inputs": [
                  {"name": "strategy", "type": "address"},
                  {"name": "token", "type": "address"},
                  {"name": "amount", "type": "uint256"},
              ], "outputs": [{"type": "uint256"}], "type": "function"}]
```

## Liquidity Provision

### Uniswap V3 (Concentrated Liquidity)

```python
UNI_V3_NPM = {  # Nonfungible Position Manager
    1: "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
    42161: "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
    8453: "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",
}

NPM_ABI = [{"name": "mint",
            "inputs": [{"name": "params", "type": "tuple", "components": [
                {"name": "token0", "type": "address"},
                {"name": "token1", "type": "address"},
                {"name": "fee", "type": "uint24"},
                {"name": "tickLower", "type": "int24"},
                {"name": "tickUpper", "type": "int24"},
                {"name": "amount0Desired", "type": "uint256"},
                {"name": "amount1Desired", "type": "uint256"},
                {"name": "amount0Min", "type": "uint256"},
                {"name": "amount1Min", "type": "uint256"},
                {"name": "recipient", "type": "address"},
                {"name": "deadline", "type": "uint256"},
            ]}],
            "outputs": [
                {"name": "tokenId", "type": "uint256"},
                {"name": "liquidity", "type": "uint128"},
                {"name": "amount0", "type": "uint256"},
                {"name": "amount1", "type": "uint256"},
            ], "stateMutability": "payable", "type": "function"}]

def price_to_tick(price: float, decimals0: int, decimals1: int) -> int:
    import math
    return int(math.log(price * 10**(decimals1 - decimals0)) / math.log(1.0001))

def nearest_tick(tick: int, tick_spacing: int) -> int:
    return (tick // tick_spacing) * tick_spacing

TICK_SPACING = {500: 10, 3000: 60, 10000: 200}   # fee tier → spacing

async def uni_v3_provide_liquidity(w3, account,
                                     token0: str, token1: str,
                                     amount0: int, amount1: int,
                                     fee_tier: int, price_lower: float, price_upper: float,
                                     decimals0: int, decimals1: int):
    npm_addr = UNI_V3_NPM[w3.eth.chain_id]
    npm = w3.eth.contract(address=npm_addr, abi=NPM_ABI)
    
    # token0 < token1 by address (uniswap convention)
    if int(token0, 16) > int(token1, 16):
        token0, token1 = token1, token0
        amount0, amount1 = amount1, amount0
        decimals0, decimals1 = decimals1, decimals0
        price_lower, price_upper = 1/price_upper, 1/price_lower
    
    approve_if_needed(w3, account, token0, npm_addr, amount0)
    approve_if_needed(w3, account, token1, npm_addr, amount1)
    
    tick_lower = nearest_tick(price_to_tick(price_lower, decimals0, decimals1),
                               TICK_SPACING[fee_tier])
    tick_upper = nearest_tick(price_to_tick(price_upper, decimals0, decimals1),
                               TICK_SPACING[fee_tier])
    
    params = (token0, token1, fee_tier, tick_lower, tick_upper,
              amount0, amount1, 0, 0, account.address, int(time.time()) + 600)
    
    tx = npm.functions.mint(params).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
        "gas": 600000,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
    })
    signed = account.sign_transaction(tx)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()
```

### Curve / Balancer / Aerodrome

Pattern serupa: approve → call `add_liquidity()` di pool contract. Setiap protocol punya ABI sedikit beda. Pakai SDK resmi atau cek Etherscan ABI tiap pool.

## Perpetuals Trading

### GMX V2 (Arbitrum + Avalanche)

```python
GMX_V2_EXCHANGE_ROUTER = {
    42161: "0x900173A66dbD345006C51fA35fA3aB760FcD843b",
    43114: "0x79be2F4eC8A4143BaF963206cF133f3710856D0a",
}

# GMX V2 pakai pattern "send tokens + create order"
# 1. Transfer collateral ke OrderVault
# 2. Call createOrder() di ExchangeRouter
# Lihat https://github.com/gmx-io/gmx-synthetics

async def gmx_v2_open_long(w3, account,
                            market_address: str,    # GMX market (e.g. ETH/USD)
                            collateral_token: str,
                            collateral_amount: int,
                            size_usd: int,           # 30 decimals
                            slippage_bps: int = 30):
    # Detail implementation di GMX docs — sketch saja di sini karena multi-step
    pass
```

### dYdX v4 (Cosmos chain)

dYdX sekarang di Cosmos-based chain, bukan EVM lagi. Pakai `dydx-v4-client` Python SDK.

### Hyperliquid

```python
# Hyperliquid pakai REST/WebSocket API, bukan EVM tx untuk perp trading
# (settlement on-chain di Hyperliquid L1, tapi order pakai signature off-chain)

# Library: https://github.com/hyperliquid-dex/hyperliquid-python-sdk
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

# Setup
exchange = Exchange(account, base_url="https://api.hyperliquid.xyz")
info = Info(base_url="https://api.hyperliquid.xyz")

# Open position
order_result = exchange.order(
    coin="ETH", is_buy=True, sz=0.1, limit_px=2500.0,
    order_type={"limit": {"tif": "Gtc"}}, reduce_only=False,
)

# Hyperliquid butuh sign EIP-712 untuk authenticate API (built-in di SDK)
```

## Yield Aggregators

### Yearn V3

```python
# Yearn vault: deposit token → terima yvToken yang auto-compound
YEARN_USDC_VAULT_ETH = "0xBe53A109B494E5c9f97b9Cd39Fe969BE68BF6204"

YEARN_VAULT_ABI = [
    {"name": "deposit", "inputs": [
        {"name": "assets", "type": "uint256"},
        {"name": "receiver", "type": "address"},
    ], "outputs": [{"type": "uint256"}], "type": "function"},
    {"name": "withdraw", "inputs": [
        {"name": "assets", "type": "uint256"},
        {"name": "receiver", "type": "address"},
        {"name": "owner", "type": "address"},
    ], "outputs": [{"type": "uint256"}], "type": "function"},
]
```

### Pendle (Yield Tokenization)

Pendle split yield-bearing assets jadi PT (principal token) + YT (yield token). Strategy umum:

- **Beli PT untuk fixed yield**: e.g. PT-eETH bisa lock yield 8% sampai maturity
- **Beli YT untuk leveraged yield**: high-risk high-reward
- **LP PT/SY untuk Pendle points farming**

```python
# Pakai Pendle SDK: https://api-v2.pendle.finance
PENDLE_API = "https://api-v2.pendle.finance/core/v1"

async def pendle_swap_to_pt(chain_id: int, market: str, amount_in: int,
                              token_in: str, receiver: str):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{PENDLE_API}/sdk/{chain_id}/markets/{market}/swap", params={
            "receiver": receiver, "slippage": 0.01,
            "tokenIn": token_in, "tokenOut": "PT",
            "amountIn": str(amount_in),
        })
    return r.json()   # → tx data ready to send
```

## Safety Checks for DeFi

```python
async def defi_pre_check(w3, account, protocol: str, action: str, amount: int) -> list[str]:
    warnings = []
    
    # 1. Cek apakah contract verified di Etherscan (heuristic trust)
    # 2. Cek TVL protocol via DefiLlama API
    #    GET https://api.llama.fi/protocol/{slug}
    #    Kalau TVL < $1M → warn rug risk
    # 3. Untuk lending: cek health factor TARGET setelah action
    # 4. Untuk LP: warn impermanent loss kalau pair volatil
    # 5. Untuk perp: cek funding rate, kalau ekstrem (>0.1%/8h) → warn cost tinggi
    
    return warnings
```

## Konfirmasi Template

```
🏦 AAVE V3 SUPPLY
─────────────
Chain:      Base
Asset:      1000 USDC
APY:        ≈ 4.2% (variable)
After:      Collateral $1000, Borrow $0
Gas est.:   ~$0.50
─────────────
Lanjut? (yes/no)
```

```
📈 PERP OPEN (GMX V2)
─────────────
Market:     ETH/USD Long
Collateral: 500 USDC
Size:       2x = $1000 (≈0.4 ETH)
Entry est.: $2,500
Liq. price: ≈ $1,275 (-49%)
Slippage:   0.3%
Funding:    +0.008%/h (paying)
─────────────
⚠ Leverage trading = high risk
Lanjut? (yes/no)
```
