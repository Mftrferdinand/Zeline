# Reference: Web3 / Smart Contract Exploitation (v2)

> **AUTHORIZED USE ONLY.** Apply this only to assets you own or that are explicitly in-scope for a bug bounty / pentest program you are enrolled in. Verify scope before acting; out-of-scope = stop. No destructive actions or DoS against production without written program permission.

> Full coverage: finding, exploiting, and proving vulnerabilities in smart contracts,
> dApps, and on-chain systems. PoC in Foundry/Hardhat fork — prove exploits work safely.

## Platform Landscape
- **Immunefi** — largest smart contract bounty platform; rewards based on impact (% of funds at risk). Requires strong technical PoC + realistic funds-at-risk estimate.
- **Code4rena / Sherlock / Cantina** — competitive time-boxed audit contests; findings graded by severity (Critical/High/Medium) + write-up quality + dedup handling.
- **HackenProof, protocol in-house programs** — check individual policies.

## Core Exploit Verification
- **PoC in fork/local/testnet.** Fork mainnet to Foundry/Hardhat to prove the exploit path.
- **Funds-at-risk estimation** must be realistic — don't overclaim, back every number with exploit path proof.
- **Whitehat rescue** only through official program procedures when available.

---

## Smart Contract Vulnerability Classes

### Reentrancy
- **Single-function reentrancy**: external call before state update, check-effect-interaction violated.
- **Cross-function reentrancy**: state shared across functions, one function calls external while other expects clean state.
- **Cross-contract reentrancy**: reentrancy through tokens with hooks (ERC777, ERC721 onReceived, ERC1155).
- **Read-only reentrancy**: view function returns stale state during reentrant call — used for oracle price manipulation.
- **Detection**: grep for external calls before state writes, look for callback-capable tokens.
- **Exploit (Foundry)**:
```solidity
// Attacker contract
function attack() external {
    target.deposit{value: 1 ether}();
    target.withdraw(1 ether);
}
receive() external payable {
    if (address(target).balance >= 1 ether) {
        target.withdraw(1 ether);  // reenter
    }
}
```
- **Fix**: checks-effects-interactions, reentrancy guard (OpenZeppelin), pull-over-push.

### Access Control Failures
- **Detection**: sensitive functions without modifiers; missing `onlyOwner`; uninitialized implementation; init function callable multiple times; role misconfiguration.
- **Exploitation**: call protected functions from unauthorized accounts, reinitialize via delegatecall to implementation, abuse over-privileged roles.
- **Fix**: consistent access modifiers, Ownable/AccessControl patterns, `_disableInitializers()`, init protection.

### Oracle / Price Manipulation
- **Detection**: price from single DEX spot price; no TWAP; dependency on manipulatable reserves; stale Chainlink feeds (no heartbeat check).
- **Exploitation**: flash loan → swap to manipulate pool price → trigger target using manipulated price. Foundry fork PoC.
- **Common targets**: lending protocols (borrow at manipulated price), CDPs (mint more), perps (manipulate funding/mark).
- **Fix**: TWAP or robust oracle (multi-source), sanity bounds, staleness checks, delay mechanisms.

### Integer / Accounting Errors
- **Detection**: rounding favoring attacker, unchecked math (Solidity <0.8 or `unchecked` blocks), share/asset ratio inflation attacks (ERC4626 donation attack), fee calculation errors.
- **Common patterns**: division before multiplication causing precision loss, first-deposit frontrun (donate to manipulate share price), rebase token incompatibility, fee-on-transfer token accounting.
- **Exploitation**: precision attacks — deposit tiny amounts to drain due to rounding to zero.
- **Fix**: safe math libraries, rounding in protocol's favor, invariant validation, virtual shares.

### Logic / Invariant Violations
- **Detection**: protocol invariants (total supply, collateralization ratio, value conservation) broken by specific operation sequences.
- **Exploitation**: Foundry invariant tests (`forge test` with `invariant_` prefix) to find violation paths.
- **Fix**: enforce invariants on-chain, restrict state transitions, add assertions.

### DoS / Griefing
- **Detection**: unbounded loops, gas griefing, dependency on external calls that can revert, block stuffing vectors.
- **Exploitation**: make a function consume all gas (long loop), revert via external dependency, lock funds.
- **Fix**: avoid unbounded loops, pull pattern, gas limits, handle external call failures gracefully.

### Signature Vulnerabilities
- **Detection**: signatures without nonce/domain separator/expiry; cross-chain replay; signature malleability, ECDSA nonce reuse.
- **Exploitation**: replay signatures across contexts/chains, forge via malleability, recover keys via nonce reuse.
- **Fix**: EIP-712 domain separator, nonce, deadline, chainId in digest.

### Front-running / MEV
- **Detection**: sensitive operations without ordering protection (no slippage, no commit-reveal).
- **Exploitation**: sandwich attacks on swaps, liquidation frontrunning, NFT mint sniping, MEV extraction.
- **Fix**: slippage bounds, commit-reveal schemes, private mempool/Flashbots, auction-based ordering.

### Upgradeability & Proxy Vulnerabilities
- **Detection**: storage collision (different layout in impl vs proxy); uninitialized implementation (can be taken over); exposed proxy admin; upgrade without timelock.
- **Exploitation**: self-destruct + CREATE2 to replace implementation, initialize uninitialized implementation, corrupt storage via collision.
- **Fix**: proven proxy patterns (UUPS/Transparent), locked initializer, timelock on upgrades, storage gaps.

### Flash Loan Attack Vectors
- **Detection**: price derived from pool balances, governance voting weight based on holdings, liquidation without incentive.
- **Flash loan sources**: Aave, Uniswap V3, Balancer — typically zero-fee or low-fee.
- **Exploitation pattern**: borrow → manipulate → extract → repay. All in one transaction.
- **Common targets**: oracle-dependent protocols, governance with token-based voting, under-collateralized lending.

### Cross-chain / Bridge Vulnerabilities
- **Detection**: message validation gaps, replay across chains, missing finality checks, validator threshold bypass.
- **Exploitation**: forge cross-chain messages, replay deposits on multiple chains, exploit race conditions.
- **Fix**: replay protection (nonce + chainId), validate message proofs, sufficient validator threshold, finality delays.

---

## dApp / Off-chain Surface

- **Front-end attacks**: misleading signing UI, wrong contract addresses, XSS triggering malicious transactions.
- **API/indexer attacks**: auth bypass on API that controls protocol parameters, IDOR on user data, exposed admin keys.
- **Bridge & cross-chain**: message validation, proof verification, cross-chain replay.
- **Wallet integration**: unlimited approvals, phishing flows via dApp front-end, RPC manipulation.

---

## Contract Analysis Workflow
1. Get verified source from explorer or program repo.
2. Map architecture: actors, fund flows, external calls, trust assumptions, invariants.
3. Form hypotheses per vulnerability class above.
4. Prove with **Foundry PoC on fork** (test that fails = invariant broken).
5. Estimate funds at risk → severity.
6. Report with full exploit scenario, PoC code, financial impact.

## Anti-patterns
- "Just test small on mainnet" → NO. Always fork/testnet for exploit testing.
- Overclaiming "critical, all funds can be stolen" without full exploit path proof.
- Missing scope check: many programs only cover specific contracts, not entire protocol.
