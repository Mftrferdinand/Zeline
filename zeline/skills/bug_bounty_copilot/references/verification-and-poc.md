# Reference: Verification & PoC — Exploit Development (v2)

> **AUTHORIZED USE ONLY.** Apply this only to assets you own or that are explicitly in-scope for a bug bounty / pentest program you are enrolled in. Verify scope before acting; out-of-scope = stop. No destructive actions or DoS against production without written program permission.

> Goal: prove the bug is real and exploitable with clean, working code.
> The exploit speaks. The code proves.

## PoC Principles

1. **Minimal but complete** — Fewest steps that prove the exploit path end-to-end.
2. **Working code** — Runnable Foundry test, bash one-liner, or Python script. Not pseudo-code.
3. **Full path** — Show the complete attack: setup → trigger → impact.
4. **Reproducible** — Someone else can clone and run it without guessing.
5. **Documented** — Every step explained, every command included.

## What Makes a PoC "Enough"

| Bug | Sufficient proof | Goal |
|---|---|---|
| IDOR | Access another user's resource | Show authorization boundary violated |
| SQLi | Extract data via UNION or blind exfiltration | Prove data access |
| SSRF | Hit internal service or metadata endpoint | Prove internal network access |
| RCE | Execute command, return output | Prove code execution |
| XSS | Execute arbitrary JavaScript in target context | Prove script injection |
| Web3 Reentrancy | Drain funds in Foundry fork test | Prove funds extractable |
| Web3 Oracle | Manipulate price + extract via flash loan in fork | Prove mispricing exploitable |
| Web3 Access Control | Call protected function from unauthorized address in fork | Prove access bypass |

## Reproduction Documentation

Record exactly so anyone can reproduce:
- Prerequisites: accounts/roles needed, initial state, environment setup.
- Numbered steps: every action + relevant request/response.
- Observed vs expected results (why it's a bug).
- Artifacts: screenshots, logs, request dumps.
- Timestamp & request IDs (for vendor log correlation).

## PoC Template (for reports)

```
## Reproduction
Prerequisites: [setup details]

1. [Step one — with exact command/request]
2. [Step two]
3. [Step three]
4. [Observe result — exploit succeeded]

Evidence: [screenshot/log/script output]

Impact note: [what this means for the system/protocol]
```

## For Web3: Foundry Fork PoC Template

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import "forge-std/console.sol";

contract ExploitTest is Test {
    // Contract interfaces
    ITarget target;
    IERC20 token;
    
    address attacker = address(0xBAD);
    
    function setUp() public {
        // Fork mainnet at specific block
        vm.createSelectFork("MAINNET_RPC_URL", BLOCK_NUMBER);
        
        // Setup attacker with starting funds
        vm.deal(attacker, 10 ether);
        // Deal tokens if needed
    }
    
    function testExploit() public {
        vm.startPrank(attacker);
        
        // Step 1: Setup
        // Step 2: Trigger vulnerability
        // Step 3: Extract funds
        
        // Assert exploit worked
        assertGt(token.balanceOf(attacker), initialBalance);
        
        vm.stopPrank();
    }
}
```

## For Web: Curl/HTTP PoC Template

```bash
# Step 1: Setup / authenticate
curl -X POST https://target.com/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"test"}'

# Step 2: Exploit the vulnerability
curl -X GET https://target.com/api/users/999 \
  -H "Authorization: Bearer <token>" \
  -H "X-Forwarded-For: 127.0.0.1"

# Step 3: Observe — returns admin user data despite limited role
```

## Redaction

- Censor tokens, cookies, PII, keys in all artifacts.
- Store evidence securely, delete when no longer needed.
- For Web3: include PoC script (Foundry test) + fork instructions.
