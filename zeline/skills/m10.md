# m10 — Web3 / On-Chain Operations (NEW in v3)

---

## Operator Profile

On-chain operator. Builds reliable scripts that interact with EVM/non-EVM chains. Bias toward simulation before broadcast, gas optimization, RPC redundancy, nonce safety.

---

## Stack Defaults

```
EVM JS:           ethers v6 (preferred for stability) OR viem (preferred for type safety / perf)
EVM Python:       web3.py v7+
Solana:           @solana/web3.js + @solana/spl-token
Wallet gen:       ethers.Wallet.createRandom() / bip39 + ethers HDNode
Storage:          .env for keys, encrypted JSON for batch wallets
Indexing/RPC:     Alchemy, Infura, Ankr, public RPCs (with fallback array)
```

---

## RPC Fallback Pattern (anti-fragile)

```javascript
const { JsonRpcProvider, FallbackProvider } = require('ethers');

const RPCS = {
  ethereum: [
    'https://eth.llamarpc.com',
    'https://rpc.ankr.com/eth',
    `https://mainnet.infura.io/v3/${process.env.INFURA_KEY}`,
  ],
  base: [
    'https://mainnet.base.org',
    'https://base.llamarpc.com',
    'https://base.publicnode.com',
  ],
  bsc: [
    'https://bsc-dataseed.binance.org',
    'https://bsc-dataseed1.defibit.io',
    'https://rpc.ankr.com/bsc',
  ],
  polygon: [
    'https://polygon-rpc.com',
    'https://rpc.ankr.com/polygon',
  ],
};

function getProvider(chain) {
  const providers = RPCS[chain].map((url, i) => ({
    provider: new JsonRpcProvider(url),
    priority: i + 1,
    weight: 1,
    stallTimeout: 1500,
  }));
  return new FallbackProvider(providers, { quorum: 1 });
}
```

---

## Wallet Operations

### Generate fresh wallet (random)

```javascript
const { Wallet } = require('ethers');
const w = Wallet.createRandom();
console.log({ address: w.address, privateKey: w.privateKey, mnemonic: w.mnemonic.phrase });
```

### Generate from mnemonic (BIP39, deterministic — for batch ops)

```javascript
const { Mnemonic, HDNodeWallet } = require('ethers');
const phrase = 'word1 word2 ... word12';
const mnem = Mnemonic.fromPhrase(phrase);

// Derive multiple wallets from same mnemonic
for (let i = 0; i < 300; i++) {
  const w = HDNodeWallet.fromMnemonic(mnem, `m/44'/60'/0'/0/${i}`);
  console.log(i, w.address);
}
```

### Wallet pool with encrypted storage

```javascript
const fs = require('fs');
const { Wallet } = require('ethers');

// Save batch encrypted
async function saveWallets(wallets, password, path = 'wallets.enc.json') {
  const encrypted = await Promise.all(wallets.map(w => w.encrypt(password)));
  fs.writeFileSync(path, JSON.stringify(encrypted, null, 2));
}

// Load batch
async function loadWallets(password, provider, path = 'wallets.enc.json') {
  const blobs = JSON.parse(fs.readFileSync(path, 'utf-8'));
  return Promise.all(blobs.map(b => Wallet.fromEncryptedJson(b, password).then(w => w.connect(provider))));
}
```

---

## Transaction Pattern (simulate → estimate → send → wait)

```javascript
const { Contract, parseEther, parseUnits } = require('ethers');

async function sendTx({ wallet, to, value = 0n, data = '0x' }) {
  const tx = {
    to,
    value,
    data,
    nonce: await wallet.getNonce('pending'),     // 'pending' prevents nonce gaps
  };

  // 1. Simulate (catches reverts before paying gas)
  try {
    await wallet.call(tx);
  } catch (e) {
    throw new Error(`Simulation reverted: ${e.shortMessage || e.message}`);
  }

  // 2. Estimate gas with 20% buffer
  const gasEst = await wallet.estimateGas(tx);
  tx.gasLimit = (gasEst * 120n) / 100n;

  // 3. Fee data (EIP-1559)
  const fee = await wallet.provider.getFeeData();
  tx.maxFeePerGas = fee.maxFeePerGas;
  tx.maxPriorityFeePerGas = fee.maxPriorityFeePerGas;

  // 4. Broadcast
  const resp = await wallet.sendTransaction(tx);
  console.log('Sent:', resp.hash);

  // 5. Wait for confirmation
  const rcpt = await resp.wait(1);   // 1 confirmation
  if (rcpt.status === 0) throw new Error(`Tx reverted: ${resp.hash}`);
  return rcpt;
}
```

---

## Contract Interaction (ethers v6)

```javascript
const { Contract } = require('ethers');

const ERC20_ABI = [
  'function balanceOf(address) view returns (uint256)',
  'function transfer(address,uint256) returns (bool)',
  'function decimals() view returns (uint8)',
  'function symbol() view returns (string)',
  'event Transfer(address indexed from, address indexed to, uint256 value)',
];

async function checkBalance(provider, token, holder) {
  const c = new Contract(token, ERC20_ABI, provider);
  const [bal, dec, sym] = await Promise.all([c.balanceOf(holder), c.decimals(), c.symbol()]);
  return { raw: bal, formatted: Number(bal) / 10 ** Number(dec), symbol: sym };
}

async function transferToken(wallet, token, to, amount) {
  const c = new Contract(token, ERC20_ABI, wallet);
  const dec = await c.decimals();
  const tx = await c.transfer(to, parseUnits(String(amount), dec));
  return tx.wait();
}
```

---

## Nonce Management (avoid stuck/replaced tx)

```javascript
class NonceManager {
  constructor(wallet) { this.wallet = wallet; this.cache = null; }

  async next() {
    if (this.cache === null) {
      this.cache = await this.wallet.getNonce('pending');
    }
    const n = this.cache++;
    return n;
  }

  async reset() {
    this.cache = await this.wallet.getNonce('pending');
  }
}

// Use across multiple parallel sends
const nm = new NonceManager(wallet);
const txs = await Promise.all(targets.map(async t => {
  return wallet.sendTransaction({ to: t, value: parseEther('0.01'), nonce: await nm.next() });
}));
```

---

## Gas Optimization

```javascript
async function getGasStrategy(provider, urgency = 'standard') {
  const fee = await provider.getFeeData();
  const mult = { slow: 0.85, standard: 1.0, fast: 1.25, asap: 1.5 }[urgency] || 1.0;
  return {
    maxFeePerGas: BigInt(Math.floor(Number(fee.maxFeePerGas) * mult)),
    maxPriorityFeePerGas: BigInt(Math.floor(Number(fee.maxPriorityFeePerGas) * mult)),
  };
}
```

---

## Airdrop Eligibility Checker (O(1) lookup)

```javascript
const fs = require('fs');

// Build set once from snapshot
const eligible = new Set(
  JSON.parse(fs.readFileSync('snapshot.json'))
    .map(a => a.toLowerCase())
);
console.log(`Loaded ${eligible.size.toLocaleString()} eligible addresses`);

function isEligible(address) {
  return eligible.has(address.toLowerCase());
}

// For Telegram bot integration:
bot.onText(/^\/check (.+)/, (msg, match) => {
  const addr = match[1].trim();
  if (!/^0x[a-fA-F0-9]{40}$/.test(addr)) {
    return bot.sendMessage(msg.chat.id, '❌ Invalid address format.');
  }
  const ok = isEligible(addr);
  bot.sendMessage(msg.chat.id, ok ? `✅ Eligible: ${addr}` : `❌ Not in snapshot: ${addr}`);
});
```

For 1M+ addresses: switch to SQLite with index on `address` column.

---

## Mass Mining/Farming Pattern (with rate limits + concurrency cap)

```javascript
const pLimit = require('p-limit').default;
const limit = pLimit(5);   // max 5 concurrent

const wallets = await loadWallets('passphrase', provider);

const results = await Promise.allSettled(
  wallets.map(w => limit(async () => {
    try {
      const tx = await runQuest(w);   // operator-specific quest function
      return { addr: w.address, hash: tx.hash, ok: true };
    } catch (e) {
      return { addr: w.address, error: e.message, ok: false };
    }
  }))
);

const ok = results.filter(r => r.value?.ok).length;
console.log(`Done: ${ok}/${wallets.length} succeeded`);
```

---

## Token Snapshot (read holders from chain)

```javascript
const { Contract } = require('ethers');
const ERC20_ABI = ['event Transfer(address indexed from, address indexed to, uint256 value)'];

async function snapshotHolders(provider, token, fromBlock, toBlock) {
  const c = new Contract(token, ERC20_ABI, provider);
  const events = await c.queryFilter('Transfer', fromBlock, toBlock);

  const holders = new Map();
  for (const ev of events) {
    const { from, to, value } = ev.args;
    holders.set(from, (holders.get(from) || 0n) - value);
    holders.set(to,   (holders.get(to)   || 0n) + value);
  }
  return [...holders.entries()].filter(([_, v]) => v > 0n);
}
```

Note: for chains with millions of events, chunk by block range (e.g., 10k blocks/chunk) to avoid RPC limits.

---

## Solana Quick Reference

```javascript
const { Connection, Keypair, LAMPORTS_PER_SOL, PublicKey, SystemProgram, Transaction } = require('@solana/web3.js');
const bs58 = require('bs58');

const connection = new Connection('https://api.mainnet-beta.solana.com', 'confirmed');
const payer = Keypair.fromSecretKey(bs58.decode(process.env.SOL_PRIVATE_KEY));

const balance = await connection.getBalance(payer.publicKey);
console.log('SOL:', balance / LAMPORTS_PER_SOL);

const tx = new Transaction().add(
  SystemProgram.transfer({
    fromPubkey: payer.publicKey,
    toPubkey: new PublicKey(recipient),
    lamports: 0.01 * LAMPORTS_PER_SOL,
  })
);
const sig = await connection.sendTransaction(tx, [payer]);
console.log('Sig:', sig);
```

---

## On-Chain Common Patterns

### Approve + Spend (DEX swap, staking, etc.)

```javascript
const erc20 = new Contract(token, ERC20_ABI, wallet);
const router = new Contract(routerAddress, ROUTER_ABI, wallet);

// Check current allowance
const allowance = await erc20.allowance(wallet.address, routerAddress);
if (allowance < amount) {
  console.log('Approving...');
  const tx = await erc20.approve(routerAddress, ethers.MaxUint256);
  await tx.wait();
}
// Now safe to call router
await router.swap(...);
```

### Multicall (batch reads in one RPC call)

```javascript
const { Multicall } = require('ethereum-multicall');
const mc = new Multicall({ ethersProvider: provider, tryAggregate: true });
const calls = addresses.map(a => ({
  reference: a,
  contractAddress: token,
  abi: ERC20_ABI,
  calls: [{ methodName: 'balanceOf', methodParameters: [a] }],
}));
const { results } = await mc.call(calls);
```

---

## Constraints

- ALWAYS simulate before broadcasting on mainnet
- Use `pending` nonce on parallel sends
- RPC fallback list, never a single endpoint for production
- Never log private keys (use `***` mask)
- Always use FallbackProvider or rotation for read calls at scale
- `.env` for secrets, never inline
- Test on testnet (Sepolia/Base Sepolia) before mainnet for any new flow
- Gas buffer 20% above estimate
- For batch ops: include retry on transient errors, p-limit for concurrency
