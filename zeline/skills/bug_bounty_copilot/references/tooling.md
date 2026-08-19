# Reference: Tooling — Standard, Production-Ready (v2)

> **AUTHORIZED USE ONLY.** Apply this only to assets you own or that are explicitly in-scope for a bug bounty / pentest program you are enrolled in. Verify scope before acting; out-of-scope = stop. No destructive actions or DoS against production without written program permission.

> Industry-standard security research tools. Power is not in the tool — it's in how you
> wield it with discipline and depth. Every tool here is publicly available.

---

## Recon & Enumeration

| Category | Tools |
|---|---|
| Subdomain/asset | subfinder, amass, assetfinder, crt.sh, censys, dnsx (resolve) |
| Port/service | nmap, masscan, naabu, rustscan |
| Content/param discovery | ffuf, feroxbuster, gobuster, dirsearch, arjun (params) |
| Crawling/JS analysis | katana, gospider, hakrawler, gau, waybackurls, linkfinder |
| JS source maps | source-map-unpack, custom scripts — recover original source |
| Fingerprint | httpx, whatweb, wappalyzer, builtwith |
| Search dorking | Google/Bing advanced operators, GitHub dorking (truffleHog, gitleaks) |

## Analysis & Interception

| Category | Tools |
|---|---|
| Intercepting proxy | Burp Suite (Community/Pro), OWASP ZAP |
| Out-of-band | interactsh, Burp Collaborator, dnslog.cn |
| Template scanning | nuclei (with custom templates), nuclei-templates |
| API | Postman, Insomnia, GraphQL voyager |

## Exploitation

| Category | Tools |
|---|---|
| SQL injection | sqlmap (data extraction), ghauri |
| SSTI | tplmap, SSTImap |
| Command injection | commix |
| XXE | XXEinjector, custom DTD |
| Deserialization | ysoserial (Java), PHPGGC (PHP), ysoserial.net (.NET) |
| WebSocket | wscat, custom scripts |
| General web | curl, httpie, Python requests |

## Web3 / Smart Contract

| Category | Tools |
|---|---|
| Development | Foundry (forge/cast/anvil) — primary, Hardhat — alternative |
| Static analysis | Slither, Aderyn, Mythril, Semgrep with Solidity rules |
| Fuzzing | Echidna, Medusa, Foundry invariant fuzz |
| Explorer | Etherscan/Snowtrace/Polygonscan (verified source), Blockscout |
| Simulation | Tenderly, Phalcon, anvil fork |
| Storage | cast storage, sload, slot-by-slot analysis |
| Disassembly | evm.codes, solc --asm, Dedaub, Heimdall |

## Network / Infra

| Category | Tools |
|---|---|
| Cloud recon | cloud_enum, S3Scanner, GCPBucketBrute |
| CI/CD | truffleHog, git-secrets, gitleaks |
| DNS recon | dnsrecon, dnsdumpster, fierce |
| Container | Docker enumeration scripts, kube-hunter |

---

## Healthy Workflow

1. Passive recon first (safe, context-rich).
2. Active enumeration → map surface.
3. **Manual analysis** of candidates (tools assist, brain decides).
4. Manual verification + PoC (code proves).
5. Report.

## What NOT To Do

- Submit findings directly from scanner output without manual verification.
- Run destructive/DoS scanners against production.
- Trust any tool's output blindly — always verify.
