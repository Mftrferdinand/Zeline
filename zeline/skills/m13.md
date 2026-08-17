# m13 — Universal NFT Minter
# Load ONLY when: mint, NFT, opensea, manifold, zora, seadrop, mint nft, mint contract, claim NFT, mint url

---

## Mission
One command → mint anywhere. No asking questions. Gas auto, mint function auto-detected, chain auto-detected.

User input shapes accepted:
- OpenSea URL → `https://opensea.io/collection/<slug>` or `https://opensea.io/assets/<chain>/<contract>/<id>`
- Direct contract: `0xABC... on base` or just `0xABC...` (default mainnet)
- Manifold claim link → `https://app.manifold.xyz/c/<id>`
- Zora link → `https://zora.co/collect/<chain>:<contract>/<token>`
- Raw command: "mint <contract> <amount> on <chain>"

Default amount = 1. Default chain = ethereum unless URL says otherwise.

---

## Architecture

```
parse input → resolve(contract, chain, mintFn, price, args)
            → auto gas (estimate + buffer)
            → simulate (eth_call)
            → send tx
            → wait receipt
            → report
```

Never ask user for: gas, function name, ABI. Detect everything.

---

## Step 1 — Parse input

```js
// parseTarget.js
function parseTarget(input) {
  const s = input.trim();

  // OpenSea assets URL
  let m = s.match(/opensea\.io\/assets\/([\w-]+)\/(0x[a-fA-F0-9]{40})(?:\/(\d+))?/);
  if (m) return { source: 'opensea', chain: chainSlug(m[1]), contract: m[2], tokenId: m[3] };

  // OpenSea collection URL → needs slug → contract lookup
  m = s.match(/opensea\.io\/collection\/([\w-]+)/);
  if (m) return { source: 'opensea_slug', slug: m[1] };

  // Manifold
  m = s.match(/manifold\.xyz\/c\/(\w+)/);
  if (m) return { source: 'manifold', claimId: m[1] };

  // Zora
  m = s.match(/zora\.co\/collect\/(\w+):(0x[a-fA-F0-9]{40})(?:\/(\d+))?/);
  if (m) return { source: 'zora', chain: chainSlug(m[1]), contract: m[2], tokenId: m[3] };

  // "mint 0xABC on base 5"
  m = s.match(/(0x[a-fA-F0-9]{40})(?:\s+on\s+(\w+))?(?:\s+x?(\d+))?/i);
  if (m) return { source: 'direct', contract: m[1], chain: m[2] || 'ethereum', amount: Number(m[3] || 1) };

  throw new Error('cannot parse mint target');
}

function chainSlug(s) {
  const map = {
    ethereum: 'ethereum', eth: 'ethereum', mainnet: 'ethereum',
    base: 'base',
    matic: 'polygon', polygon: 'polygon',
    arbitrum: 'arbitrum', arb: 'arbitrum',
    optimism: 'optimism', op: 'optimism',
    zora: 'zora-network',
  };
  return map[s.toLowerCase()] || s;
}
```

## Step 2 — Resolve mint function (detective mode)

Try mint function signatures in order. First one that exists wins.

```js
const MINT_SIGNATURES = [
  // most common public mint patterns
  { sig: 'mint(uint256)',                    args: ['amount'] },
  { sig: 'mint(address,uint256)',            args: ['to', 'amount'] },
  { sig: 'publicMint(uint256)',              args: ['amount'] },
  { sig: 'mintPublic(uint256)',              args: ['amount'] },
  { sig: 'mint()',                           args: [] },
  { sig: 'claim(uint256)',                   args: ['amount'] },
  // OpenSea Seadrop
  { sig: 'mintPublic(address,address,address,uint256)', args: ['nftContract','feeRecipient','minterIfNotPayer','quantity'], protocol: 'seadrop' },
  // Zora 1155
  { sig: 'mintWithRewards(address,uint256,uint256,bytes,address)', args: ['minter','tokenId','quantity','minterArgs','mintReferral'], protocol: 'zora' },
  // ERC1155 generic
  { sig: 'mint(address,uint256,uint256,bytes)', args: ['to','id','amount','data'] },
];

async function detectMintFunction(contract, signer) {
  for (const fn of MINT_SIGNATURES) {
    const iface = new ethers.Interface([`function ${fn.sig} payable`]);
    const data = iface.encodeFunctionData(
      fn.sig.split('(')[0],
      synthArgs(fn, signer.address)
    );
    try {
      // eth_call with 0 value to check if function exists & succeeds with synth args
      await signer.provider.call({
        to: contract,
        from: signer.address,
        data,
        value: 0
      });
      return fn;
    } catch (e) {
      // Distinguish "no such function" from "reverted with reason"
      if (e.data && e.data !== '0x') return fn;  // function exists but needs payment/proof
      continue;
    }
  }
  throw new Error('no recognized mint function');
}
```

## Step 3 — Detect mint price

```js
const PRICE_READERS = [
  'function mintPrice() view returns (uint256)',
  'function price() view returns (uint256)',
  'function cost() view returns (uint256)',
  'function PRICE() view returns (uint256)',
  'function publicSalePrice() view returns (uint256)',
];

async function detectPrice(contract, provider, amount = 1) {
  for (const sig of PRICE_READERS) {
    try {
      const c = new ethers.Contract(contract, [sig], provider);
      const fnName = sig.match(/function (\w+)/)[1];
      const p = await c[fnName]();
      return p * BigInt(amount);
    } catch { continue; }
  }
  // Last resort: simulate mint with 0 value, parse revert
  return 0n;  // free mint or seadrop (price from drop config)
}
```

## Step 4 — Auto gas (the "biar dia nentuin sendiri" part)

```js
async function autoGas(provider, txRequest) {
  // EIP-1559 chains
  const feeData = await provider.getFeeData();
  
  // Estimate with buffer
  let gasLimit;
  try {
    gasLimit = await provider.estimateGas(txRequest);
    gasLimit = gasLimit * 120n / 100n;  // +20% buffer
  } catch (e) {
    // estimateGas failed → simulation reverts. Surface this NOW.
    throw new Error(`gas estimate failed (likely revert): ${e.shortMessage || e.message}`);
  }

  // Priority fee: median of last 5 blocks
  const priorityFee = feeData.maxPriorityFeePerGas
    ? feeData.maxPriorityFeePerGas * 110n / 100n  // +10% to land faster
    : ethers.parseUnits('1.5', 'gwei');

  // Max fee: baseFee*2 + priority (covers next-block spike)
  const baseFee = feeData.gasPrice ?? feeData.maxFeePerGas;
  const maxFee = (baseFee * 2n) + priorityFee;

  return {
    gasLimit,
    maxPriorityFeePerGas: priorityFee,
    maxFeePerGas: maxFee,
  };
}
```

For Base / L2s: base fee tiny, just bump priority a bit. For Ethereum mainnet: priority matters more for inclusion speed.

## Step 5 — Send + report

```js
async function mintOne(target, wallet) {
  const provider = getProvider(target.chain);
  const signer = wallet.connect(provider);

  const fn = await detectMintFunction(target.contract, signer);
  const price = await detectPrice(target.contract, provider, target.amount || 1);

  const iface = new ethers.Interface([`function ${fn.sig} payable`]);
  const args = buildArgs(fn, target, signer.address);
  const data = iface.encodeFunctionData(fn.sig.split('(')[0], args);

  const txRequest = {
    to: target.contract,
    from: signer.address,
    data,
    value: price,
  };

  // Simulate one more time with real args
  try {
    await provider.call(txRequest);
  } catch (e) {
    throw new Error(`simulation revert: ${parseRevert(e)}`);
  }

  const gas = await autoGas(provider, txRequest);
  const tx = await signer.sendTransaction({ ...txRequest, ...gas });
  console.log(`[SENT] ${tx.hash}`);

  const receipt = await tx.wait();
  return {
    hash: tx.hash,
    block: receipt.blockNumber,
    gasUsed: receipt.gasUsed.toString(),
    status: receipt.status === 1 ? 'success' : 'reverted',
    explorer: explorerUrl(target.chain, tx.hash),
  };
}
```

## Step 6 — One-shot CLI

```js
// mint.js — single entrypoint
import { ethers } from 'ethers';
import 'dotenv/config';

const RPCS = {
  ethereum: process.env.RPC_ETH || 'https://eth.llamarpc.com',
  base: process.env.RPC_BASE || 'https://mainnet.base.org',
  polygon: process.env.RPC_POLYGON || 'https://polygon-rpc.com',
  arbitrum: process.env.RPC_ARB || 'https://arb1.arbitrum.io/rpc',
  optimism: process.env.RPC_OP || 'https://mainnet.optimism.io',
  'zora-network': process.env.RPC_ZORA || 'https://rpc.zora.energy',
};
const getProvider = (chain) => new ethers.JsonRpcProvider(RPCS[chain]);

const explorers = {
  ethereum: 'https://etherscan.io/tx/',
  base: 'https://basescan.org/tx/',
  polygon: 'https://polygonscan.com/tx/',
  arbitrum: 'https://arbiscan.io/tx/',
  optimism: 'https://optimistic.etherscan.io/tx/',
  'zora-network': 'https://explorer.zora.energy/tx/',
};
const explorerUrl = (chain, hash) => explorers[chain] + hash;

async function main() {
  const input = process.argv.slice(2).join(' ');
  if (!input) { console.error('usage: node mint.js <url|contract> [chain] [amount]'); process.exit(1); }

  const target = parseTarget(input);
  if (target.source === 'opensea_slug') {
    // resolve slug → contract via OpenSea API
    const r = await fetch(`https://api.opensea.io/api/v2/collections/${target.slug}`,
      { headers: { 'x-api-key': process.env.OPENSEA_KEY } });
    const j = await r.json();
    target.contract = j.contracts[0].address;
    target.chain = j.contracts[0].chain;
  }

  const wallet = new ethers.Wallet(process.env.PRIVATE_KEY);
  const result = await mintOne(target, wallet);

  console.log(`\n[${result.status.toUpperCase()}] ${target.contract} on ${target.chain}`);
  console.log(`hash:    ${result.hash}`);
  console.log(`gasUsed: ${result.gasUsed}`);
  console.log(`view:    ${result.explorer}`);
}

main().catch(e => { console.error(`[FAIL] ${e.message}`); process.exit(1); });
```

Usage:
```bash
node mint.js https://opensea.io/assets/base/0xABC.../1
node mint.js 0xABC... base
node mint.js 0xABC... ethereum 3
```

## Step 7 — Mass mint with N wallets (combo with m12)
```js
import pLimit from 'p-limit';

const wallets = JSON.parse(fs.readFileSync('wallets.json'))
  .map(w => new ethers.Wallet(w.pk));

const target = parseTarget(process.argv[2]);
const limit = pLimit(5);

const tasks = wallets.map(w => limit(async () => {
  try { return await mintOne(target, w); }
  catch (e) { return { wallet: w.address, error: e.message }; }
}));

const results = await Promise.allSettled(tasks);
const ok = results.filter(r => r.value?.status === 'success').length;
console.log(`[DONE] ${ok}/${wallets.length} minted`);
fs.writeFileSync(`mint-${Date.now()}.json`, JSON.stringify(results, null, 2));
```

---

## Special protocols

### OpenSea Seadrop (most "drop" collections)
SeaDrop contract address (constant): `0x00005EA00Ac477B1030CE78506496e8C2dE24bf5`
```js
const SEADROP = '0x00005EA00Ac477B1030CE78506496e8C2dE24bf5';
const abi = ['function mintPublic(address nftContract, address feeRecipient, address minterIfNotPayer, uint256 quantity) payable'];
const seadrop = new ethers.Contract(SEADROP, abi, signer);

// feeRecipient = read from drop config: getPublicDrop(nftContract)
const cfgAbi = ['function getPublicDrop(address) view returns (tuple(uint80 mintPrice, uint48 startTime, uint48 endTime, uint16 maxTotalMintableByWallet, uint16 feeBps, bool restrictFeeRecipients))'];
const cfg = new ethers.Contract(SEADROP, cfgAbi, provider);
const drop = await cfg.getPublicDrop(nftContract);
const price = drop.mintPrice * BigInt(quantity);

const feeRecipientAbi = ['function getAllowedFeeRecipients(address) view returns (address[])'];
const fr = new ethers.Contract(SEADROP, feeRecipientAbi, provider);
const allowed = await fr.getAllowedFeeRecipients(nftContract);
const feeRecipient = allowed[0];

await seadrop.mintPublic(nftContract, feeRecipient, ethers.ZeroAddress, quantity, { value: price });
```

### Manifold Claims
Manifold ERC721LazyPayableClaim contract: `0x44e94034afce2dd3cd5eb62528f239686fc8f162` (eth), `0x44e94034afce2dd3cd5eb62528f239686fc8f162` (base, same).
```js
const MANIFOLD = '0x44e94034afce2dd3cd5eb62528f239686fc8f162';
const abi = ['function mint(address creatorContractAddress, uint256 instanceId, uint32 mintIndex, bytes32[] merkleProof, address mintFor) payable'];
// instanceId from URL → API call to claim metadata
const url = `https://apps.api.manifoldxyz.dev/public/instance/data?id=${claimId}`;
const meta = await (await fetch(url)).json();
// extract creatorContractAddress, publicData.instanceId, cost
```

### Zora 1155
```js
const abi = ['function mintWithRewards(address minter, uint256 tokenId, uint256 quantity, bytes minterArguments, address mintReferral) payable'];
// minter = ZoraTimedSaleStrategy: 0x777777722D078c97c6ad07d9f36801e653E356Ae
// minterArguments = abi.encode(['address'], [recipient])
const minterArgs = ethers.AbiCoder.defaultAbiCoder().encode(['address'], [signer.address]);
const mintFee = ethers.parseEther('0.000111');  // Zora protocol fee per mint
const value = mintFee * BigInt(quantity);
await contract.mintWithRewards(ZORA_MINTER, tokenId, quantity, minterArgs, ethers.ZeroAddress, { value });
```

---

## ABI fetching fallback (when function detection fails)
Use Blockscout MCP or Etherscan API:
```js
async function fetchAbi(contract, chain) {
  // Blockscout works for many chains free
  const r = await fetch(`https://eth.blockscout.com/api/v2/smart-contracts/${contract}`);
  const j = await r.json();
  return j.abi;
}
```
Then scan ABI for any `payable` function with `mint` in name → try those.

---

## Speed mode (Kakak's "tanpa mikir")
Default behavior: **fire and forget**.
- Skip confirmation prompts
- Auto-pick first viable mint function
- Auto-set gas to "fast" tier (baseFee*2 + 1.5 gwei priority)
- Send → log hash → don't wait if `--nowait` flag
- Print one line per result

If user says "hati-hati" / "simulate first" / "cek dulu" → enable dry-run mode (simulate, print expected cost, ask confirm).

---

## Output format
```
[TARGET] 0xABC... on base × 3
[FN]     mintPublic(uint256) — detected
[PRICE]  0.0042 ETH (0.0014 × 3)
[GAS]    180k @ 0.05 gwei  →  ~$0.02
[SENT]   0xtxhash...
[OK]     block 12345678  gasUsed 142,330
[VIEW]   https://basescan.org/tx/0xtxhash...
```

---

## Failure modes Kakak should know
- **`execution reverted: NotEnabled`** → public mint not active yet, check `saleActive()` or wait
- **`InsufficientFunds`** → price miscalculated, retry with verbose price detection
- **`AlreadyMinted`** → wallet hit per-wallet cap, switch wallet
- **`InvalidMerkleProof`** → it's allowlist-only, public mint not started, m13 won't help here without proof
- **revert no reason** → ABI contract likely uses custom mint flow (Zora drop, Sound, custom Foundation) → manual ABI inspection needed
