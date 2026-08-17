# skills/zeline-zenith-z61.md — Skill Registry & Reflection Loop (v4.2)
# Called from AGENTS.md on every session start. Read this first, then route.
# ⚡ CONSOLIDATED July 2026: 59→48 skills via cluster mergers (see CONSOLIDATION NOTES below)

---

## CONSOLIDATION NOTES (v4.2 → v7, July 2026)

These skills have been merged to reduce loading redundancy. The secondary files still exist with redirect headers — keyword matching still works, just loads the primary file instead.

| Cluster | Merged | Into | Rationale |
|---|---|---|---|
| **Content Creation** | z19 → z4 | zeline-zenith-z4 (creation+strategy) | Same pipeline: voice/hook/formats + strategy/calendars = one skill |
| **Content Writing** | z21 → z20 | zeline-zenith-z20 (copy+research) | Writing + analytics = inseparable; research feeds copy, copy needs analytics |
| **Self-Audit** | z32 → z29 | zeline-zenith-z29 (audit+improvement) | Audit evaluates state, improvement closes the loop — two faces of same meta-system |
| **Strategy** | z35 → z30 | zeline-zenith-z30 (strategy+shaping) | z35 frames problem at top of funnel, z30 decomposes it — combined thinking pipeline |
| **Debug** | z34 → z31 | zeline-zenith-z31 (diagnosis+systematic) | z31 = fast lookup (known errors), z34 = systematic method (unknown bugs) — never used independently |
| **CTF Sub-skills** | z23—z28 | dispatched by z22 | All CTF categories already routed through zeline-zenith-z22 — sub-skill files kept for detailed playbooks only |

---

## REGISTRY

Scan table. Match intent. Load matched skill(s) on demand.
Do NOT preload skill files — token cost.

```
ID  | DOMAIN                              | KEYWORDS (high-weight)
----|-------------------------------------|------------------------------------------------
z2  | Monetization & value generation     | monetize, pricing, jual, jualan, cuan, funnel
z3  | Infrastructure & deployment         | VPS, deploy, SSH, nginx, docker, systemd
z4  | Content creation & distribution (merged z19) | viral, hook, caption, thread, naskah, konten, content calendar, strategy, pillar, platform adapter, content OS
z5  | Process orchestration & bots        | telegram bot, cron, webhook, n8n, automate, otomatis
z6  | Data transformation & insight       | spreadsheet, excel, csv, dataset, snapshot, laporan
z7  | Protocol binding & service bridge   | API, REST, webhook, midtrans, integrasi
z8  | Inference systems & AI builder      | LLM, prompt, claude API, openrouter, kimi, agent, add model, tambah model, registry model
z9  | File & artifact production          | PDF, DOCX, XLSX, PPTX, generate file, dokumen
z10  | Interface construction              | landing page, react, tailwind, frontend, UI
z11 | Web3 / crypto operations            | wallet, airdrop, on-chain, RPC, ethers, viem, mint
z12 | Security audit & review             | audit, vulnerability, exploit, scam check, malicious
z13 | Batch / parallel operations         | batch, parallel, bulk, mass, queue, worker, snapshot
z14 | Universal NFT minter (any contract) | mint, opensea, manifold, zora, seadrop, NFT, claim, drop
z15 | Daily assistant: briefing & alerts  | briefing, ringkasan harian, alert, kabarin kalau, pantau harga, alarm
z16 | Daily assistant II: watchdog/vault/multimodal/triage | watchdog, restart kalau mati, simpen alamat, macro, voice note, screenshot, triage
z17 | Software engineering & coding       | coding, bikin app, backend, API server, database, testing, scaffold, refactor, golang, rust, fastapi
z18 | Power pack: planner/swarm/automation/backtest/dashboard/voice | rencanain, workflow, otomatis kalau, backtest, dashboard, ngomong, swarm, tim agent
m18 | Creative & media generation         | comfyui, manim, excalidraw, ascii video, slide, design system, diagram, deck
m19 | Desktop & physical control          | isaac sim, omniverse, robot, macos control, applescript, scene, usd, mobility, ros2
m20 | Humanizer & brand voice             | humanizer, brand voice, ai tone, manusiawi, rewrite nada, voice profile
m21 | Enterprise & defensive ops          | KQL, azure, log analytics, sentinel, HIDS, auto firewall, block ip, intrusion
m22 | Scientific & deep research          | deep research, AI-Q, tooluniverse, hipotesis, citations, riset ilmiah, eksperimen
m23 | Executive function & neurodivergent | task breakdown, executive function, ADHD, overwhelmed, fokus, context switch, prioritas
m24 | MCP-builder & prompt engineering    | MCP server, bikin MCP, FastMCP, prompt engineering, fix prompt, audit prompt
m25 | Compliance, CI/CD & code migration  | compliance, regulasi, GDPR, SOC2, CI/CD, pipeline, code migration, port bahasa
m26 | Product & spec workflows            | PRD, to-issues, grill me, spec, user story, TDD, internal comms
z19 | ⚠️ MERGED → zeline-zenith-z4 (redirect header in file) | REFERENCED: content calendar, content strategy, content pillar, carousel, reels, platform adapter, scroll-stopper
z20 | Copywriting & content research (merged z21) | AIDA, PAS, CHEF, copywriting framework, SEO writing, storytelling, localization, competitor analysis, trend research, social listening, A/B caption, best time post
z21 | ⚠️ MERGED → zeline-zenith-z20 (redirect header in file) | REFERENCED: competitor analysis, trend research, social listening, audience insight, content pipeline, A/B caption, best time post
m30 | Client Revenue Engine (bulk gig, API-first) | client revenue, garapan, bulk gig, harvest, scrape API, anti-browser, mass akun, otomasi job klien, ekstrak data
m31 | Airdrop Intelligence (eligibility/sybil/claim/exit) | eligibility airdrop, skor airdrop, layak airdrop, sybil, anti-sybil, claim window, jadwal claim, kalender airdrop, vesting unlock, exit plan, kapan jual token
z22 | CTF / Whitehat toolkit                  | CTF, capture the flag, whitehat, bug bounty, decode flag, crypto challenge, caesar, xor cipher, hash identify, forensics, HTB, THM
m33 | Pre-TGE Alpha Radar (deteksi airdrop dini) | alpha airdrop, pre-TGE, points program, testnet incentivized, radar airdrop, proyek belum ada token, worth difarming
m34 | Farming Portfolio & ROI Optimizer       | ROI farming, portfolio airdrop, untung rugi farming, gas vs hasil, farm mana lanjut, drop farming, wallet nganggur
m35 | Auto Guide Studio (panduan otomatis)    | bikin panduan airdrop, tutorial airdrop, guide step by step, artikel airdrop, embed referral
m36 | Tokenomics & Unlock Pressure Engine     | unlock token, vesting, cliff, kalender unlock, sell pressure, tokenomics, jual sebelum unlock
m37 | Anti-Scam Sentinel (brand protection)   | situs palsu, domain palsu, typosquat, phishing airdrop, scam check, warning scam, drainer
m38 | Contract-Change & Claim-Address Watcher | kontrak berubah, proxy upgrade, claim address, aman claim gak, watch kontrak, cek sebelum claim
m39 | Community Intelligence (dengerin komunitas) | topik trending komunitas, sentimen komunitas, FUD, analisis chat, pertanyaan member, ide konten komunitas
m40 | Omni-Repurpose Engine (1 konten → semua) | repurpose konten, ubah jadi thread, carousel IG, script tiktok, script youtube, multi platform
m41 | Video Script-to-Screen Pipeline         | bikin video, script video, storyboard, voiceover, subtitle, srt, reels, youtube video
m42 | Hook A/B Lab + Performance Predictor    | hook, judul konten, headline, a/b test judul, stop scroll, prediksi engagement, varian judul
z23 | CTF Web Exploitation (sub of z22)       | CTF web, SQLi, SSTI, SSRF, IDOR, XXE, JWT, deserialization, prototype pollution, race condition
z24 | CTF Binary Exploitation / pwn (sub z22) | CTF pwn, buffer overflow, ret2win, ret2libc, format string, heap, ROP, pwntools, GOT leak
z25 | CTF Reverse Engineering (sub of z22)    | CTF reverse, angr, symbolic execution, Ghidra, radare2, decompile, keygen, deobfuscation
z26 | CTF Cryptography (sub of z22)           | CTF crypto, RSA attack, Wiener, Hastad, common modulus, Coppersmith, padding oracle, nonce reuse
z27 | CTF Forensics & Stego (sub of z22)      | CTF forensics, stego, pcap, memory dump, volatility, binwalk, exiftool, zsteg, steghide
z28 | CTF Prompt-Injection/LLM (sub of z22)   | CTF prompt injection, LLM red-team, jailbreak, Gandalf, Lakera, Mosscap, OWASP LLM01, extract system prompt

H1  | Swap & sell via aggregator          | swap, 1inch, jupiter, jual token, sell, DEX
H2  | Cross-chain bridge                  | bridge, LayerZero, stargate, LI.FI, across, hop
H3  | DeFi (lending/staking/perp)         | aave, lido, GMX, hyperliquid, pendle, defi
H4  | Token launch & NFT mint sniping     | snipe, honeypot, PairCreated, GoPlus, sniping
H5  | Whale & mempool tracking            | mempool, whale, nansen, arkham, smart money, tracker
H6  | NFT buy/sell (marketplace)          | beli NFT, blur, magic eden, tensor, reservoir, listing
H7  | Web3 sign-in & typed signing        | SIWE, walletconnect, EIP-712, ENS, permit, EIP-1271
H8  | Browser dApp automation             | buka dapp, browser, playwright, navigate, connect wallet, isi form
H9  | Universal contract read/write       | baca/tulis kontrak, read/write, call fungsi, ABI, inspect, proxy, eksekusi
H10 | Crypto dev: deploy/compile/test     | deploy kontrak, compile, forge, solidity, test, verify, CREATE2, bikin token

z29  | Self-audit & self-improvement (merged z32) | improve system, self-audit, upgrade brain, self improve, belajar, makin pinter, auto fix, upgrade diri, learn
z30  | Deep strategy & problem shaping (merged z35) | strategy, architecture, decompose, plan, design system, problem shaping, brainstorming, sign-off, framing, vague goal, uncertainty
z31  | Fault diagnosis & systematic debug (merged z34) | error, bug, debug, gagal, stack trace, systematic debug, RCA, root cause, hypothesis, intermittent, reproduce
z32  | ⚠️ MERGED → zeline-zenith-z29 (redirect header in file) | REFERENCED: self improve, belajar, makin pinter, auto fix, upgrade diri, learn
z33  | Agentic eval & self-critique        | eval, self-critique, variance, konsistensi, flaky, regression, ukur
z34  | ⚠️ MERGED → zeline-zenith-z31 (redirect header in file) | REFERENCED: systematic debug, RCA, root cause, hypothesis, intermittent, reproduce
z35  | ⚠️ MERGED → zeline-zenith-z30 (redirect header in file) | REFERENCED: problem shaping, brainstorming, sign-off, framing, vague goal, uncertainty
```

### Skill marketplace (skills.sh) — opsional, PARANOID
Skill pihak ketiga masuk lewat `tools/skill_market.py` → **quarantine**, BUKAN auto-aktif.
Wajib audit (z12) → operator pindah ke `skills/` → `skill_integrity.py generate` (re-lock).
Tidak pernah auto-install + auto-activate. Sama prinsip z32: friksi = fitur.

---

## ROUTING

```
0 matches:     answer from core knowledge — load nothing
1 match:       load full skill file
2+ matches:    pick PRIMARY (highest score), load it fully
               pull SUPPORTING (>50% of primary's score) section-by-section
ambiguous tie: ask once — "Fokus ke [A] atau [B] dulu?"
H-skill hit:   load skills/zeline-crypto/DISPATCH.md FIRST, then specific reference
```

### Zeline routing
H1-H7 are NOT standalone files. They map to `skills/zeline-crypto/references/*.md`:
```
H1 → zeline-crypto/references/swap.md
H2 → zeline-crypto/references/bridge.md
H3 → zeline-crypto/references/defi.md
H4 → zeline-crypto/references/sniping.md
H5 → zeline-crypto/references/monitoring.md (advanced sections 8-11)
H6 → zeline-crypto/references/nft.md
H7 → zeline-crypto/references/web3_connect.md
```
Load `zeline/DISPATCH.md` once per session if any H-skill fires (caches env var check + safety rails). Then load specific reference. Combine with z11/z13/z14 if task overlaps.

---

## REFLECTION LOOP

Runs silently after every output. Non-negotiable.

```
✅ Immediately executable / usable as-is?
✅ Anything the operator will need next that's missing?
✅ Generic advice — could be replaced with operator-specific code?
✅ Faster or cleaner path missed?
✅ Did I include the run command / deploy step?
✅ Token usage justified — could same value land in fewer lines?
```

Any fail → revise BEFORE outputting.
Upgrade exists → append: `🔧 Upgrade: [one line]`

---

## REFLECTION ESCAPE HATCH

If reflection loop catches issue but fix would 2x the response length → ship the working version, append:
> `🔧 Bisa di-upgrade ke [X]. Mau elaborasi?`
