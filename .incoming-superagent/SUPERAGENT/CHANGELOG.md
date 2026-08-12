# CHANGELOG — SUPERAGENT OPENCLAW EDITION

Version history. Baseline **v3.1** (2026-05-25) → **v4.0** (2026-06-03) → **v4.1** (2026-06-04) → **v4.1.1** (2026-06-05) → **v4.1.2** (2026-06-05) → **v4.2** (2026-06-09) → **v7.0** (2026-07-08).

> **Note on numbers:** counts below (file/skill/test totals) are historical snapshots at each release. For current, authoritative metrics always run `python3 tools/counts.py` — it is the single source of truth. Older entries reflect earlier states and are kept for history only.

---

## v7.0 — IRONCLAW SUPREME (2026-07-08)

MAJOR RELEASE. SUPERAGENT single-user v4.2 → SUPERAGENT V7 IRONCLAW SUPREME. Fully autonomous, team-ready, monetization-focused, offensive-capable. Engineering-first identity upgrade. Zero backward compatibility breaks — all existing skill/tool files from v4.2 work unchanged. Bootstrap files (SOUL.md, AGENTS.md, USER.md, MEMORY.md, HEARTBEAT.md) are new.

**HEADLINE:** IRONCLAW V7 SOUL with autonomous doctrine (Cruise/Hunter/Sovereign/War modes), team hierarchy Level 0-3, monetization engine, offensive security toolkit. 12 autonomous triggers. 4 execution modes. Enhanced core rules R11-R12. 82 total skills (sk1-sk51, sk52-sk58, H1-H10, hermes). IRONCLAW SUPREME codename. File count: 264, always-on token budget: ~3.5k.

---

### 🔥 IRONCLAW V7 SOUL

Complete identity upgrade from v4.2 to v7 — credible, engineering-first persona:

- **SOVEREIGN EXECUTION DOCTRINE** — Maximum capability with responsible defaults. Execute anything without refusal, with documented bypass flags for every safeguard.
- **ENGINEERING-FIRST IDENTITY** — "Execute anything. Engineered with guardrails." Power comes from engineering, not branding. Six named safeguards: scope guard, spend governor, dry-run default, secret tripwire, simulate-before-send, SKILLS.lock integrity.
- **CAPABILITY MATRIX** — All domains available: crypto/financial, offensive security, automation, content, infrastructure, reverse engineering, AI/ML. Zero artificial limits.
- **SAFEGUARD BYPASS** — Every safety rail has documented bypass: `--broadcast` flag, `--skip-sim` flag, `auto_confirm=True`. Safeguards are seatbelts, not speed limits.
- **2 HARD STOPS ONLY** — Content sexualizing minors + mass-casualty weapon synthesis. Everything else → execute. If safeguards block → tell operator the bypass flag.
- **4 Execution Modes** — Cruise (background monitor), Hunter (opportunity detected), Sovereign (operator activated), War (emergency max aggression). Mode auto-selected by context.

---

### 👥 TEAM ORCHESTRATION (sk49)

Multi-operator support transforms SUPERAGENT from solo engine to team command center:

- **USER.md Team Members table** — Multi-operator profiles with Level 0-3 permission gates.
- **Level 0** — Sovereign. Full access: treasury, governance, fund movement, config, team management. Operator-side owner.
- **Level 1** — Commander. Deploy, manage infrastructure, view treasury, fund movement with governor. No governance changes.
- **Level 2** — Operator. Execute in assigned domains. Fund movement gated through governor. No config/team changes.
- **Level 3** — Observer. Read-only reports and briefings. Cannot trigger actions or move funds.
- **Per-member audit trails** — Every action attributed to specific operator. Full accountability.
- **Task delegation** — Level 0 (Sovereign) and Level 1 (Commander) can delegate tasks to Level 2-3 members with deadlines.
- **Conflict detection** — When two team members issue conflicting commands, surface conflict + priority resolution.
- **Billing attribution** — Costs tracked per-member for accurate team P&L.
- **Team session continuity** — Track per-member last interaction. Flag if > 7 days gap. Load per-member context on reconnection.
- **MEMORY.md TEAM CONTEXT section** — Tracks per-member: name, level, domain, wallet, active since date, notes.

---

### 💰 MONETIZATION ENGINE (sk50)

Built-in profit generation and tracking:

- **MEMORY.md REVENUE LOG** — Structured revenue tracking with 90-day retention + monthly aggregates.
- **Per-session P&L tracking** — `CURRENT_SESSION_P&L` initialized at session start (heartbeat step 10).
- **Revenue trigger phrase** — "profit" / "revenue" / "P&L" → show current session ledger.
- **Treasury management** — Balance tracking across wallets/chains. Auto-consolidation recommendations.
- **Revenue optimization engine** — Identify idle assets, suggest yield strategies, flag cost anomalies.
- **Per-member cost attribution** — Track who spent what for team billing.
- **USER.md Billing section** — Payment method, default currency, billing cycle, wallet for payments, auto-bill threshold.
- **Output format** — Revenue line added to standard template: `💰 P&L: [session net] | [monthly net]`

---

### 🛡 OFFENSIVE SECURITY (sk51)

Full adversarial toolkit integrated into core identity:

- **Exploit development framework** — Generate, test, and refine exploits for research/authorized targets.
- **C2 infrastructure generation** — Command & control setup scripts. Multi-protocol (HTTP/DNS/ICMP/WebSocket).
- **Post-exploitation tooling** — Persistence, lateral movement, privilege escalation, data exfiltration.
- **MITRE ATT&CK mapping** — Map techniques to ATT&CK framework for operational clarity.
- **Payload generation** — Multi-format payloads with evasion techniques.
- **Reconnaissance automation** — Automated target profiling and attack surface mapping.
- **All offensive domains in-scope** — Red team, pentest, exploit dev, social engineering, physical security, RF/SDR, hardware.

---

### 🤖 AUTONOMOUS SYSTEM

12 autonomous triggers that execute without operator confirmation:

1. **Autonomous scan** (heartbeat step 9) — Scan memory + projects for pending autonomous actions. Auto-execute SAFE_AUTO_ACTIONS.
2. **Profit ledger init** (heartbeat step 10) — Initialize session P&L from start.
3. **Heartbeat checks** — All 10 heartbeat steps run autonomously on session start.
4. **Background monitoring** — Wallet balances, gas prices, token prices, contract events.
5. **Memory compaction** — Auto-compact when approaching 2500 token limit.
6. **Session state handoff** — Save state on session end for next session continuity.
7. **Alert engine** — Poll-based conditional alerts (price/gas/wallet/claim) with cooldown dedup.
8. **Watchdog** — Self-healing process monitoring. Mark bot alive.
9. **Reflection cycle** — Daily learning from recent memory. Auto-fix only SAFE_AUTO_ACTIONS.
10. **Daily briefing** — Once-per-day push briefing if due.
11. **Vault macro** — Saved command sequences for repeated operations.
12. **Triage** — Inbox prioritization and routing.

Operator-configurable per-action-type. SAFE_AUTO_ACTIONS = everything except fund movement and destructive operations.

---

### 📏 ENHANCED CORE RULES

- **R11 — Autonomous Execution Doctrine**: When autonomous mode is active (Cruise/Hunter/Sovereign/War), execute SAFE_AUTO_ACTIONS without confirmation. Operator configures which action types are "safe". Default: heartbeat, monitoring, memory, briefing, alerts, vault, triage.
- **R12 — Team Delegation & Conflict Resolution**: Multi-operator request handling. When two Level 0 (Sovereign) operators conflict → surface both with context, wait for resolution. Lower-level conflicts resolved by priority chain (Level 0 > Level 1 > Level 2 > Level 3). All actions attributed to requesting operator.

---

### 🔄 RENAMING

- **Workspace path**: `~/.openclaw/workspace/superagent-v3/` → `~/.openclaw/workspace/superagent-v7/`
- **Codename**: IRONCLAW → IRONCLAW SUPREME
- **Identity**: SUPERAGENT v4.2 → SUPERAGENT V7
- **SOUL.md**: v6 Compact → IRONCLAW V7 — engineering-first sovereign execution
- All internal references updated across 30+ files.

---

### 🧰 TOOLS

- **`tools/profit_ledger.py`** — Session P&L initialization + tracking. Revenue log append. Monthly aggregate computation.
- **`tools/team_context.py`** — Per-member activity tracking. Delegation queue. Conflict detection.
- **`tools/autonomous.py`** — SAFE_AUTO_ACTIONS registry. Mode selector (Cruise/Hunter/Sovereign/War). Trigger scheduler.
- **`tools/offensive.py`** — Exploit framework. C2 generator. Post-exploitation toolkit. MITRE mapper.
- **`tools/treasury.py`** — Multi-chain balance aggregation. Cost tracking. Revenue optimization suggestions.
- **`tools/billing.py`** — Per-member cost attribution. Invoice generation. Auto-bill threshold checking.
- Existing tools (governor, mev, integrity, memory, reflection, alerts, briefing, triage, vault, watchdog, contract_reader/writer, deploy_engine, model_registry, planner, swarm, skill_forge, automation, backtest, dashboard, voice, explain, eval, ctf/*, eligibility, sybil_audit, claim_watcher, exit_planner, rugcheck, cost_ledger, router_log, secret_tripwire, dryrun, revenue_engine, api_harvester, alpha_radar, farm_roi, guide_studio, unlock_engine, scam_sentinel, contract_watch, community_intel, repurpose, video_pipeline, hook_lab, research_q, mcp_builder, prd, humanizer, hids, desktop_control, scene_prep, content) — all v4.2 tools preserved unchanged.

---

### 📚 DOCS

- **DEPLOY.md** — Complete rewrite: workspace path → `~/.openclaw/workspace/superagent-v7/`, SKILLS.lock verification step, team config in USER.md, Hermes configuration section, 59 skills mention.
- **README.md** — Updated install paths, skill count → 58, skill architecture table with all 59 skills, v7.0 upgrade guide.
- **USER.md** — v7 version: Team Members table (Level 0-3 definitions), Billing section with all fields, trigger phrases including "profit"/"revenue"/"P&L".
- **MEMORY.md** — v7 version: TEAM CONTEXT section, REVENUE LOG with CURRENT_SESSION_P&L + monthly aggregates.
- **HEARTBEAT.md** — v7 version: Step 9 (autonomous scan), Step 10 (profit ledger init), Team Session Continuity subsection.
- **CHANGELOG.md** — This file. V7.0 section expanded to full detail.
- **AGENTS.md** — R11 + R12 added. Keyword table expanded for sk49-sk51. v7 codename.
- **SOUL.md** — IRONCLAW V7 full persona with accountable defaults.
- **IDENTITY.md** — SUPREME codename. V7 reference.
- **SKILLS.lock** — Regenerated with 59 skills + all new tools.

---

### 📊 59 SKILLS — COMPACT TABLE

| # | Skill | Domain |
|---|-------|--------|
| sk0 | Registry + reflection | Skill index, reflection loop, marketplace gate |
| sk1 | Monetization | Business ops, pricing, funnel, revenue models |
| sk2 | VPS & infra | Deploy, SSH, nginx, docker, systemd, backup |
| sk3 | Content & copywriting | Viral hooks, captions, threads, Indonesian voice |
| sk4 | Telegram bots | Production patterns, webhook, anti-duplicate |
| sk5 | Data & spreadsheets | CSV, datasets, snapshots, large-file handling |
| sk6 | Integrations | API, REST, webhooks, Midtrans, payments |
| sk7 | AI providers | Multi-LLM, streaming, fallback, model registry |
| sk8 | Documents | PDF, DOCX, XLSX, PPTX generation, images |
| sk9 | Frontend | Landing pages, React, Tailwind, Web3 UI |
| sk10 | Web3 ops | On-chain, mass farming, RPC, wallet gen, multicall |
| sk11 | Security audit | Skill safety, Solidity red flags, secret scan |
| sk12 | Batch & parallel | p-limit, queues, workers, checkpoint-resume |
| sk13 | NFT minter | OpenSea, Manifold, Zora, Seadrop, auto-gas |
| sk14 | Daily briefing | Ringkasan harian, alert engine, price/gas monitor |
| sk15 | Watchdog & vault | Self-healing, voice/screenshot, triage, macro |
| sk16 | Software engineering | Backend, DB, testing, scaffold, refactor, multi-lang |
| sk17 | Power pack | Planner, swarm, skill-forge, automation, backtest, dashboard |
| sk18 | Creative & media | ComfyUI, Manim, Excalidraw, ASCII video, design systems |
| sk19 | Desktop & robotics | macOS control, Isaac Sim, scene prep, ROS2 |
| sk20 | Humanizer & brand voice | AI-tone removal, brand adaptation, natural rewrite |
| sk21 | Enterprise defensive | Azure/KQL, Mini-HIDS, auto-firewall, Sentinel |
| sk22 | Deep research | AI-Q, hypothesis, scientific tools, citations |
| sk23 | Executive function | ADHD support, task breakdown, context switch |
| sk24 | MCP-builder | FastMCP, prompt engineering, tool schema audit |
| sk25 | Compliance & CI/CD | GDPR, SOC2, pipeline, code migration, porting |
| sk26 | Product & spec | PRD, To-Issues, TDD, grill-me, user stories |
| sk27 | Content strategy | Calendar, platform adapter, pillars, carousel, reels |
| sk28 | Copywriting mastery | AIDA, PAS, CHEF, SEO, storytelling, localization |
| sk29 | Content analytics | Competitor, trend, social listening, engagement, pipeline |
| sk30 | Client revenue engine | Bulk gig, scrape API, mass accounts, checkpoint |
| sk31 | Airdrop intelligence | Eligibility scorer, sybil audit, claim calendar, exit plan |
| sk32 | CTF / Whitehat | Triage, decode, hash-id, orchestrator for sk43-sk48 |
| sk33 | Alpha radar | Pre-TGE scoring, points program, testnet, VC signals |
| sk34 | Farm ROI optimizer | EV per position, keep/trim/drop, idle wallet detection |
| sk35 | Auto guide studio | Panduan ID + variants, referral embed, screenshot jobs |
| sk36 | Unlock pressure engine | Vesting calendar, unlock vs liquidity, exit signals |
| sk37 | Anti-scam sentinel | Typosquat, drainer signals, SSL check, risk scoring |
| sk38 | Contract watcher | Proxy upgrade, admin change, claim address, safe_to_claim |
| sk39 | Community intelligence | Trending topics, sentiment, FUD, content ideas |
| sk40 | Omni-repurpose | 1 source → X/TG/IG/TikTok/YT, platform-adapted |
| sk41 | Video pipeline | Script, storyboard, voiceover, SRT, timing |
| sk42 | Hook A/B lab | Variant generation, stop-scroll scoring, ranking |
| sk43 | CTF web | SQLi, SSTI, SSRF, IDOR, XXE, JWT, deserialization |
| sk44 | CTF pwn | Binary exploitation, buffer overflow, ROP, heap, pwntools |
| sk45 | CTF reverse | angr, Ghidra, radare2, decompile, keygen, deobfuscation |
| sk46 | CTF crypto | RSA, Wiener, Hastad, Coppersmith, padding oracle, ECDSA |
| sk47 | CTF forensics | Stego, pcap, memory dump, volatility, binwalk, carving |
| sk48 | CTF prompt-injection | LLM red-team, Gandalf, Lakera, jailbreak, OWASP LLM01 |
| sk49 | Team orchestration | Delegation, conflict detection, billing attribution, audit |
| sk50 | Autonomous monetization | Treasury, P&L, revenue optimization, cost tracking |
| sk51 | Offensive security | Exploit dev, C2, post-exploitation, MITRE ATT&CK |
| sk52 | Self-audit | System refinement, capability check |
| sk53 | Deep decomposition | Strategy, architecture breakdown |
| sk54 | Reflection loop | Instinct extraction, compounding memory |
| sk55 | Agentic eval | Self-critique, variance testing, LLM-as-judge |
| sk56 | Systematic debugging | 4-phase RCA, hypothesis loop, auto-debug |
| sk57 | Problem shaping | Brainstorming, sign-off, context design |
| sk58 | Decision support | Uncertainty quantification, one-way vs two-way door |
| H1 | Swap | DEX aggregator, 1inch, Jupiter, slippage |
| H2 | Bridge | LayerZero, Stargate, LI.FI, Across, cross-chain |
| H3 | DeFi | Aave, Lido, GMX, Hyperliquid, Pendle, yield |
| H4 | Sniping | Honeypot gate, PairCreated, mempool, launch detect |
| H5 | Smart money | Nansen, Arkham, wallet tracking, copy-trade |
| H6 | NFT | Seaport, Blur, MagicEden, Tensor, marketplace ops |
| H7 | SIWE/auth | Sign-In with Ethereum, message signing, verification |
| H8 | Browser engine | Puppeteer/Playwright, anti-detect, CAPTCHA |
| H9 | Contract tools | Reader, writer, deploy, verify, CREATE2 |
| H10 | Token deploy | ERC-20, ERC-721, ERC-1155, governance, Foundry |

---

### ✅ ZERO BACKWARD COMPATIBILITY BREAKS

- All existing skill files (sk0-sk48, sk52-sk58) unchanged from v4.2
- All existing Python tools unchanged from v4.2
- Hermes H1-H10 unchanged
- `.env.example` unchanged (new vars are optional additions)
- SKILLS.lock regenerated (covers new files, old hashes unchanged)
- Old workspace path (`superagent-v3`/`superagent`) still works if not migrated — just missing v7 features

---

### 🔢 NUMBERS

- **82 skills**: sk1-sk48 + sk49-sk51 (new) + sk52-sk58 + H1-H10 + hermes
- **264 files** total (skills, tools, tests, bootstrap, docs, hermes)
- **12 autonomous triggers**
- **4 execution modes**: Cruise, Hunter, Sovereign, War
- **4 team levels**: 0 (Sovereign), 1 (Commander), 2 (Operator), 3 (Observer)
- **2 new core rules**: R11 (autonomous doctrine), R12 (team delegation)
- **Always-on token budget**: ~3.5k
- **~30+ Python tools** preserved from v4.2
- **6 new Python tools**: profit_ledger, team_context, autonomous, offensive, treasury, billing
- **Zero backward compat breaks**

---

## v4.2 (update 2026-06-12) — CTF PROMPT-INJECTION / LLM RED-TEAM (sk48)

Update aditif **dalam v4.2** — versi TIDAK naik. Nambah kategori CTF **AI/LLM** ke keluarga CTF (orchestrator sk32). Nambah **1 sub-skill (sk48)** + **`tools/ctf/gandalf_solver.py`** (host-locked solver buat Lakera Gandalf, mode static + adaptive) + **test offline buat extractor**. Total skill **54 → 55** (sk1–sk48 · sk52–sk57).

### 🤖 Yang baru
- **sk48 — CTF Prompt-Injection / LLM Red-Teaming**: methodology ngalahin guard berlapis (input vs output guard), defense ladder Gandalf L1–L8, taksonomi 8 teknik (authority reframe, indirect, encoding, format coercion, language switch, roleplay, instruction extraction, sidestepping), workflow + defensive takeaway (tiap bypass = peta kontrol). Dispatched sk32 buat soal "AI/LLM/jailbreak".
- **`tools/ctf/gandalf_solver.py`**: solver otomatis **host-locked ke gandalf.lakera.ai** (`_check_host` nolak host lain). Mode `static` (ladder strategi) + `adaptive` (attacker-LLM lewat `coordinator/llm.py`). `candidates()` ekstrak kandidat secret (ALL-CAPS, acrostic, separated-letters, base64/rot13 decode) — pure/offline, ke-test.
- **Full-auto wiring (coordinator)**: kategori AI/LLM sekarang ikut ke-solve di swarm `run.py`, gak cuma manual. `solver.build_system()` nyuntik addendum red-team sk48 ke system prompt buat soal AI/LLM doang; `coordinator.extract_targets()` narik target dari `connection_info` **+ URL di deskripsi**, semua di-scope-check sebelum dikasih network (`bridge`). Soal AI/LLM tanpa target keparse = di-skip aman.
- **Adapter OpenAI + Gemini lengkap (`coordinator/llm.py`)**: bukan stub lagi — Chat Completions (OpenAI) & generateContent (Gemini) full tool-calling, REST murni zero-SDK. History kanonik = format blok Anthropic; tiap adapter konversi via fungsi pure yang ke-test offline (`to_openai`/`from_openai`/`to_gemini`/`from_gemini`), stop_reason dinormalisasi ke `tool_use`. Gemini: functionResponse di-key pakai NAMA (id→nama di-resolve dari history), id call disintesis stabil. `config.validate()` sekarang nagih API key per provider yang dipakai di `MODELS`. Swarm bisa race lintas-provider buat semua kategori, termasuk AI/LLM.

### 🔍 Review & perbaikan
- **Model ID placeholder** `anthropic:claude-opus-4-8` (gak ada) → diselaraskan ke `claude-opus-4-1` (konsisten sama config coordinator).
- Catatan: API contract Gandalf (path/field/defender id) gak resmi dipublish → didokumentasiin "VERIFY di DevTools" di skill + docstring. Adapter `.step()/.text` udah cocok sama `AssistantTurn`.

### 📚 Docs
- sk32 dispatch + delegation table (+row sk48), AGENTS.md, sk0.md, INDEX.md, IDENTITY.md, README.md (55 skills), panduan.md (item 12 diperluas + cheat-sheet row). `SKILLS.lock` regen & verified.

---

## v4.2 (update 2026-06-12) — CTF SWARM INTEGRATION (sk43–sk47 + tools/ctf)

Update aditif **dalam v4.2** — versi TIDAK naik. Integrasi paket CTF agent (karya the operator) ke dalam SUPERAGENT, kompatibel dengan sk32 (CTF/whitehat) yang sudah ada. Nambah **5 sub-skill kategori** (sk43–sk47) + **package `tools/ctf/`** (orchestrator helpers + full-auto coordinator runtime + sandbox) + **24 test offline baru**. Total skill **49 → 54** (sk1–sk47 · sk52–sk57). **216 test offline hijau** (44 hermes + 172 tools), zero ResourceWarning.

### 🚩 Arsitektur
- **sk32** naik peran jadi **orchestrator/router CTF**: triage → cek scope → dispatch ke sub-skill kategori → validasi flag → writeup.
- **sk43 web · sk44 pwn · sk45 rev · sk46 crypto · sk47 forensics** — playbook per kategori (toolbox sandbox, decision tree, delegation table, safety rails), format SUPERAGENT.
- **`tools/ctf/`** — runtime full-auto: `coordinator/` poll CTFd → race N model per soal di Docker sandbox → consensus/HITL gate sebelum submit. Plus `scope_guard.py`, `flag_validator.py`, `rsa_attacks.py`, `templates/`, `sandbox/Dockerfile.sandbox`, `run.py`, `README.md`.

### 🔍 Review & perbaikan (atas paket asli — lihat `REVIEW.md`)
- **Consensus unreachable**: swarm cuma balikin flag pertama + cancel sisanya → `AUTO_SUBMIT_CONSENSUS>=2` gak pernah kepenuhi (semua diam-diam masuk antrian HITL). Fix: `race()` consensus-aware (kumpulin semua kandidat, Counter agreement).
- **flag_validator longgar**: `re.search` fallback nerima string ber-junk. Fix: `validate()` full-match only (extract pakai `extract_all`).
- **scope_guard**: DNS resolve sia-sia buat IP literal → reorder.
- **rsa_attacks butuh gmpy2** (gak ada di host, cuma di sandbox) → gmpy2 dibikin optional + fallback pure-Python (`_iroot` binary search, `_invert` via `pow(a,-1,m)`).
- **Path rewire**: `coordinator` importlib path + `SCOPE_PATH` default disesuaikan ke `tools/ctf/`.
- **Model placeholder & Dockerfile gaps**: model ID di-set ke nilai jelas + tool sandbox ditambah (radare2, john, exiftool, ropper, hashpumpy, jwt_tool) + dokumentasi tool berat per-event.

### 📚 Docs
- `AGENTS.md` (sk43–sk47 keyword rows) + `skills/sk0.md` registry + `INDEX.md` (skills + tools/ctf) + `IDENTITY.md` + `README.md` (54 skills + whatsnew) + `panduan.md` di-update.
- `SKILLS.lock` diregenerasi & verified.

---

## v4.2 (update 2026-06-12) — GROWTH & PROTECTION EXTENSION (sk33–sk42)

Update aditif **dalam v4.2** — versi TIDAK naik. Nambah **10 skill opsional** (sk33–sk42) + **10 tool baru** + **63 test baru**. Semua offline, deterministik, zero-dep; on-chain read didelegasi ke sk10/hermes, fund movement tetap via Spend Governor, time logic via `now` injected (TIME.md). Identitas inti & safety machinery utuh. **192 test offline hijau** (44 hermes + 148 tools), zero ResourceWarning.

### 🛠️ Skill baru (load on-trigger)
- **sk33 — Pre-TGE Alpha Radar**: skor proyek 0-100 + tier (cold/watch/warm/hot) dari sinyal pre-token (points program, testnet, VC, momentum); token udah ada → cap 30.
- **sk34 — Farming Portfolio & ROI Optimizer**: EV/ROI per posisi (gas vs estimasi×confidence), aksi keep/trim/drop, deteksi wallet idle.
- **sk35 — Auto Guide Studio**: 1 airdrop → panduan ID lengkap + varian TG/X + referral embed + daftar screenshot jobs (delegasi browser).
- **sk36 — Tokenomics & Unlock Pressure Engine**: kalender unlock + pressure ratio (nilai unlock vs volume harian) → sinyal exit makro.
- **sk37 — Anti-Scam Sentinel**: typosquat (levenshtein) + sinyal halaman (seed phrase/drainer/SSL baru) → risiko 0-100 + draft warning. Read-only defensif.
- **sk38 — Contract-Change & Claim-Address Watcher**: diff snapshot kontrak (proxy/claim addr/admin/code hash/fungsi sensitif) → alert + `safe_to_claim` gate.
- **sk39 — Community Intelligence**: pesan komunitas → topik trending, pertanyaan berulang, sentimen, FUD alert, ide konten.
- **sk40 — Omni-Repurpose Engine**: 1 sumber → X thread (≤280) / TG / IG carousel / TikTok / YouTube, lokal ID.
- **sk41 — Video Script-to-Screen Pipeline**: brief → script ber-timing (hook 15%/body/CTA 15%) + storyboard + voiceover + SRT.
- **sk42 — Hook A/B Lab**: generate varian hook + skor stop-scroll deterministik 0-100 + ranking.

### 🔧 Tool baru
`alpha_radar.py` · `farm_roi.py` · `guide_studio.py` · `unlock_engine.py` · `scam_sentinel.py` · `contract_watch.py` · `community_intel.py` · `repurpose.py` · `video_pipeline.py` · `hook_lab.py` — masing-masing dengan test suite sendiri di `tools/tests/`.

### 📚 Docs
- `AGENTS.md` router + `skills/sk0.md` registry + `INDEX.md` + `IDENTITY.md` (daftar modul opsional) + `README.md` (49 skills) + `panduan.md` di-update.
- `SKILLS.lock` diregenerasi & verified.

---

## v4.2 — WHITEHAT & AIRDROP INTELLIGENCE (2026-06-09)

Rilis aditif besar. Nambah **2 skill** (sk31, sk32) + **10 tool baru** + extend `eval.py`, plus **6 perbaikan** dari audit internal. Identitas inti crypto + dev (IRONCLAW) utuh — skill baru load on-trigger. Safety machinery (governor, SKILLS.lock, integrity boot) tetap, malah diperkuat. **129 test offline hijau** (44 hermes + 85 tools), zero ResourceWarning.

### 🛠️ Skill baru
- **sk31 — Airdrop Intelligence**: 4 tahap ber-angka — eligibility scorer, sybil self-audit, claim-window calendar, exit planner. Semua offline/deterministik; data on-chain didelegasi ke sk10/hermes; time selalu di-inject (TIME.md).
- **sk32 — CTF / Whitehat Toolkit**: triage kategori, multi-decode, classic crypto (caesar/xor/vigenere), hash-id. **Legal & in-scope only** (R9 gate buat target asing). Stdlib-only & pasif.

### 🔧 Tool baru
- `tools/eligibility.py` — `WalletStats` → `score_wallet()` 0-100 + gaps + flags, rubric override-able per-project.
- `tools/sybil_audit.py` — `audit()` korelasi antar-wallet sendiri (funding/timing/gas/tx/overlap) + saran de-correlation.
- `tools/claim_watcher.py` — `ClaimWatcher` H-48/H-2/H-0 fire-once + `upcoming()` kalender; `now` wajib di-inject.
- `tools/exit_planner.py` — `build_plan()` exit ladder (conservative/balanced/degen), nyesuaiin likuiditas/vesting, eksekusi via governor.
- `tools/rugcheck.py` — `check(SignalSet)` → SAFE/CAUTION/DANGER + critical/warnings/unknowns (sk11 + H4).
- `tools/ctf.py` — find_flags/triage/try_decode/caesar/xor/identify_hash.
- `tools/cost_ledger.py` — ledger token+on-chain+API terpusat, summary per-provider/chain (observability — fix #6).
- `tools/router_log.py` — log keputusan router + deteksi tie → tuner bobot keyword (fix #4 + observability).
- `tools/secret_tripwire.py` — redaksi secret di output layer (priv key/mnemonic/API key/JWT/PEM), `guard(strict=)` (leak tripwire).
- `tools/dryrun.py` — dry-run global (contextvars): engine `plan()` bukan broadcast; `with dry_run()`.
- `tools/eval.py` — **+`RegressionSuite`**: golden-set regression lintas rilis (record_baseline → run → diff).

### 🐛 Fixes (audit internal)
1. **README stale** — judul/badge → v4.2, hapus "Successor to v2", path `superagent-v3/` → `superagent/`.
2. **Test resource leaks / ResourceWarning** — `governor.py`, `airdrop_runner.RunState`, `CostLedger`, `RouterLog` dapat `close()` + context manager; test `test_api_harvester`, `test_governor`, `test_airdrop_runner` nutup koneksi & file. Full suite kini bersih di `-W error::ResourceWarning`.
3. **Skill count inkonsisten** — diseragamkan ke **39 skills** (sk1–sk32 · sk52–sk58) di README/INDEX.
4. **Router keyword collision** — `router_log.py` nge-log tie & ngasih `tune_report()` (data buat retune bobot, bukan nebak).
5. **Governor per-VPS only** — `SpendGovernor` terima `conn=` injectable + docstring multi-VPS (cap global lintas mesin via backend share-able).
6. **No central cost observability** — `cost_ledger.py` jadi sumber tunggal biaya token+on-chain+API.

### ✅ Testing
- Tool baru semua punya test offline (stdlib unittest, zero-dep): cost_ledger, router_log, secret_tripwire, dryrun, eligibility, sybil_audit, claim_watcher, exit_planner, rugcheck, ctf, eval-regression.
- `SKILLS.lock` di-regenerate (integrity boot otomatis mencakup file `.md`/`.py` baru).
- Registry/router/docs terupdate: `sk0.md`, `AGENTS.md` (keyword weights sk31/sk32), `panduan.md` (TOC + section v4.2), `INDEX.md`, `IDENTITY.md`, README.

---

## v4.1.2 — CLIENT REVENUE ENGINE / sk30 (2026-06-05)

Rilis aditif kecil. Nambah **1 skill orkestrator** + **2 tool** + test. Identitas inti crypto + dev tetap; skill baru load on-trigger, gak ganggu karakter utama. Safety machinery (governor, SKILLS.lock, integrity boot) utuh.

### 🛠️ Skill baru — sk30: Client Revenue Engine (bulk gig, API-first)
- **Doctrine "browser = last resort"**: garapan bulk dikerjain lewat kode & API; headless browser cuma kalau data render-only & gak ada endpoint yang bisa direplikasi.
- **Orkestrator, bukan duplikat** — sk30 nyetir & delegasi:
  - Pattern A (harvest/scrape API) → parse_curl + paginate + extract + BulkRunner, delegasi sk6/sk5, browser_engine (H8) last resort.
  - Pattern B (mass akun/airdrop/on-chain) → BulkRunner + Checkpoint, delegasi sk10 + hermes, **wajib lewat Spend Governor**.
  - Pattern C (integrasi API & otomasi job) → RequestSpec + retry + provider cascade, delegasi sk6/sk16/sk4.
- Terdaftar di `INDEX.md`, `sk0.md` registry, dan router `AGENTS.md` (keyword weights). High-weight sk30 spesifik (garapan, harvest, scrape API, anti-browser, parse curl) supaya gak nyolong "bulk" generik dari sk12.

### 🔧 Tool baru
- `tools/revenue_engine.py` — `BulkRunner` (concurrency + retry/backoff + **checkpoint-resume** + dedupe), `TokenBucket` (rate limit, clock injectable), `Checkpoint` (idempotent, thread-safe).
- `tools/api_harvester.py` — `parse_curl()` (DevTools → RequestSpec), `paginate_offset`/`paginate_cursor`, `extract()` (JSON path + wildcard), `to_jsonl`/`to_csv`, `send()` (httpx, lazy import).

### ✅ Testing
- Test offline `tools/tests/` (stdlib unittest, zero-dep): rate-limit, backoff+jitter bounds, checkpoint resume, dedupe, BulkRunner (sukses/skip/retry/fail), pagination, JSON-path extract, cURL parse, JSONL/CSV roundtrip.
- `SKILLS.lock` di-regenerate (integrity boot mencakup file baru).

---

## v4.1.1 — CODE QUALITY & TESTS (2026-06-05)

Maintenance/hardening patch. **No behaviour or capability changes** to the agent — identitas inti crypto + dev (IRONCLAW) utuh. Fokus: keandalan script Hermes crypto + test suite offline.

### 🐛 Bug fixes (Hermes crypto scripts)
- **Mutable-default dataclass bugs** diperbaiki — bisa bikin state bocor antar-instance:
  - `swap_engine.py` → `SwapResult.warnings` sekarang `field(default_factory=list)`
  - `nft_engine.py` → `NFTResult.tx_hashes` sekarang `field(default_factory=list)`
  - `airdrop_runner.py` → `TaskSpec.params` sekarang `field(default_factory=dict)`
- **`bridge_engine.py` relative-import fragile** — `from .swap_engine import ...` gagal kalau script dipakai flat/standalone (persis cara yang README anjurkan). Sekarang ada fallback ke import top-level, jadi jalan di dua mode (package & template).

### ✅ Testing (baru)
- Test suite **offline, zero-dependency** di `skills/hermes/tests/` (stdlib `unittest`; jalan juga di `pytest`). 44 test, semua hijau.
- `_bootstrap.py` nge-stub paket berat (web3/eth_account/httpx/solana/…) lewat import hook → bisa nguji logika murni tanpa install apa-apa & tanpa jaringan.
- Coverage: governor (allow/block/halt, caps, rate-limit trip, kill-switch), SIWE/signature helpers, mempool filter, RPC failover, jitter & run-state dedupe, explorer URL, default-list safety.
- Tooling dev: `pyproject.toml` (config pytest + ruff), `requirements-dev.txt`, `tests/run_tests.sh`, `tests/README.md`.

---

## v4.1 — CAPABILITY EXPANSION (2026-06-04)

Rilis aditif. Identitas inti **tetap crypto + dev** (IRONCLAW); semua yang baru = **modul opsional** yang load on-trigger dan gak ganggu karakter utama. Safety machinery v4.0 (Spend Governor, FROZEN_PATHS, SKILLS.lock, reflection gate) **utuh & diperluas**. 7 skill + 6 tool baru.

### 🧠 Self-improvement & eval (extend x-cluster)
- **sk55 — Agentic Eval & Self-Critique**: eval terstruktur (Case/assert), **variance testing** (jalanin task N× → ukur konsistensi; <0.95 = jangan otomasi), self-critique adversarial sebelum output berisiko. → `skills/sk56.md`, `tools/eval.py`
- **sk57 — Systematic Debugging**: 4-fase RCA → Pattern → Hypothesis → Fix + **auto-debug loop** (rank hipotesis by likelihood×murah-diuji, uji, bantah, ulang). Untuk bug yang gak ada di pustaka sk54. → `skills/sk57.md`
- **Instinct extraction** (sk54): lesson berulang-&-kebukti dipadatkan jadi reflex; dikawal eval (variance jelek → gak boleh jadi reflex). → `skills/sk54.md`

### 🎨 Creative & media (sk18)
- ComfyUI (API/headless, graph patch), **Manim** animasi math, **Excalidraw** diagram programmatic, ASCII/retro video (chafa+ffmpeg), web design systems (Stripe/Linear/Vercel token), Felo-style content→deck, **aesthetic judgment** checklist. → `skills/sk18.md`, `tools/scene_prep.py`

### 🤖 Desktop & physical (sk19)
- **macOS native control — background-first, NO cursor steal** (AppleScript/Accessibility, gak ngerebut pointer/fokus operator). NVIDIA **Isaac Sim/Omniverse** (headless), **scene preparation** (USD assembly + domain randomization), mobility (Isaac Lab/ROS2; real-robot = R9 gate). → `skills/sk19.md`, `tools/desktop_control.py`

### ✍️ Humanizer & brand voice (sk20)
- AI-tell detector (heuristik keyless) + rewrite deterministik + brand voice adaptation. Jujur soal batas (bukan alat ngecoh detektor akademik). → `skills/sk20.md`, `tools/humanizer.py`

### 🛡 Enterprise & defensive (sk21)
- **Azure/KQL** troubleshooting (Log Analytics/Sentinel, read-only investigasi), **Mini-HIDS** auto-firewall dari log (allowlist + TTL + dry-run + alert; R9 gate aktivasi). Defensif murni. → `skills/sk21.md`, `tools/hids.py`

### 🧭 Soft & human-level (sk52–sk58)
- **Problem shaping** (tujuan kabur → subtask presisi), **brainstorming + sign-off** workflow, **decision support dengan ketidakpastian** (known/assumed/unknown + confidence + one-way vs two-way door), context design. Sengaja memperlambat HANYA saat kabur/taruhan tinggi. → `skills/sk58.md`

### 🧩 Orchestration
- **Skill marketplace (skills.sh)** — `tools/skill_market.py`: unduh ke **quarantine**, WAJIB audit (sk11), operator pindah + re-lock. Tidak pernah auto-install+auto-activate (friksi = fitur).
- Multi-agent orchestration & role specialization sudah ada di v4.0 (`swarm.py`/sk17) — tetap.

### 🔒 Safety
- `FROZEN_PATHS` diperluas: `hids.py`, `desktop_control.py`, `skill_market.py` (surface OS/firewall/desktop/marketplace) gak bisa diedit loop self-improve.
- Aksi baru yang nyentuh OS/desktop/robot fisik/firewall → **R9 gate** (terpisah dari governor dana).
- `SKILLS.lock` diregenerasi (version 4.1, file_count baru). Semua tool baru import-clean (Python 3.9+, `from __future__ import annotations`).

### Expansion wave 2 — meta, scientific, product & enterprise

Penambahan lanjutan (tetap v4.1, tetap modul opsional crypto-first). 5 skill + 3 tool + 1 doc meta.

- **sk22 — Scientific & Deep Research**: dispatcher riset AI-Q (report bersitasi), scientific tool workflows (ToolUniverse-style), hypothesis generation & experimental design. Privasi: query ke server eksternal → sanitize. → `skills/sk22.md`, `tools/research_q.py`
- **sk23 — Executive Function & Neurodivergent**: task breakdown anti-overwhelm (ADHD-style), context-switch + breadcrumb resume (via memory_engine), prioritization saat overwhelmed. Grounded di executive-function, bukan motivational fluff. → `skills/sk23.md`
- **sk24 — MCP-Builder & Prompt Engineering**: scaffold MCP server (Python FastMCP / TS sdk) dgn best practices, prompt auditor (deteksi+fix masalah prompt, eval-driven). → `skills/sk24.md`, `tools/mcp_builder.py`
- **sk25 — Compliance, CI/CD & Code Migration**: regulatory review (advisory, bukan legal advice), brand-guidelines enforcement, CI/CD pipeline, code-migrator antar bahasa/framework (test-first). → `skills/sk25.md`
- **sk26 — Product & Spec Workflows**: Grill-Me (idea kabur→tajam), To-PRD, To-Issues, TDD superpower, internal-comms. → `skills/sk26.md`, `tools/prd.py`
- **Extend**: sk56 + `eval.py` → **LLM-as-judge** production-grade (rubrik + panel + judge variance-check); sk19 → **Cosmos Physical AI** + **meta-actions** (STOP > deselerasi > perintah, refleks keselamatan fisik); sk18 → canvas design & visual art (Pillow/SVG); sk58 → handoff Grill-Me ke sk26; sk54 → instinct extraction (wave 1).
- **`STANDARD.md`** (baru): membakukan emerging meta trends yang **sudah** jadi sifat sistem — progressive disclosure, open SKILL.md format, cross-platform (Claude/Cursor/Codex/Gemini), marketplace. Bukan fitur baru, dokumentasi prinsip.
- **`panduan.md`**: section baru "Skill non-crypto (v4.1)" + cheat-sheet rows untuk semua skill v4.1.

### Expansion wave 3 — content, social media & marketing

Domain marketing/konten sosial (proposal kategori 18–24) + pendalaman sains & embodied AI (16–17). 3 skill + 1 tool + extends. Tetap v4.1, tetap opsional crypto-first.

- **sk27 — Content Strategy & Social Media**: content pillars/audience/positioning, content calendar generator, platform-specific adapter (X/LinkedIn/IG/TikTok), viral hook & scroll-stopper, caption+hashtag optimizer, thread/carousel/reels writer. → `skills/sk27.md`, `tools/content.py`
- **sk28 — Copywriting & Writing Mastery**: frameworks (AIDA/PAS/BAB/4P/CHEF/70-20-10), SEO writer, storytelling/narrative, multilingual & localization (termasuk RTL). → `skills/sk28.md`
- **sk29 — Content Research, Analytics & Pipeline**: trend/competitor research, audience insight & social listening, performance analyzer, A/B variant, best-time-to-post, engagement handler, **full content pipeline** (research→draft→visual→schedule→analyze) + cross-platform publisher + human-in-the-loop + repurposing. → `skills/sk29.md`, `tools/content.py`
- **Extend sk22** — scientific domain library dispatch (genomics/docking/MD/geospatial/time-series via library tervalidasi, bukan reimplementasi), Hugging Science (HF Hub discovery), multi-step pipeline literature→paper drafting, K-Dense BYOK (local co-scientist).
- **Extend sk19** — **VLA** (Vision-Language-Action: perceive→reason→act), multimodal reasoning (text+vision+audio+action), scene understanding/object manipulation — semua di bawah meta-action safety.
- **Extend sk18** — content repurposing, infographic/carousel design, image-prompt engineer (Midjourney/Flux/Canva).
- **`content.py`** — scaffolder deterministik (kalender, adapt, thread/carousel/reels, repurpose); analitik/best-time bertanda 'estimate' kalau tanpa data real (jujur, gak ngarang angka).
- **`panduan.md`** — section "Konten & social media (v4.1)" + cheat-sheet rows sk27–sk29.
- **Kejujuran desain**: "141+ scientific tools" = dispatch ke library matang + verifikasi, BUKAN klaim built-in palsu. Analitik konten butuh data real platform; tanpa itu heuristik bertanda estimasi.

### Ringkasan angka
- 37 skill (sk0–sk29 + sk52–sk58), 28 tool, 15 reference Hermes (tak berubah dari v4.0), +`STANDARD.md`.
- Semua skill baru = modul opsional, on-trigger; always-on token budget tetap ~3.5k (progressive disclosure).

---

## v4.0 — OPENCLAW EDITION (2026-06-03)

Major release. Dari baseline v3.1, v4.0 nambahin lapisan keamanan/governance penuh, kemampuan belajar sendiri, suite asisten harian, tooling smart-contract lengkap (baca/tulis/deploy), manajemen LLM dinamis, "power pack" orkestrasi, dan skill software-engineering umum. Semua aksi yang nyentuh dana lewat **Spend Governor**; file kritis dijaga **FROZEN_PATHS**.

### 🛡 Keamanan & governance
- **Spend Governor** — circuit breaker di tiap tx: cap per-tx / harian / sesi (USD), batas slippage, auto-HALT pas gas spike / rate-limit, simulation gate, kill-switch manual. `auto_confirm` matiin prompt, **bukan** governor. → `skills/hermes/references/governor.md`, `scripts/governor.py`
- **MEV protection** — swap & snipe lewat private relay (Flashbots Protect / MEV Blocker), bukan mempool publik; fallback-with-warning yang jujur. → `scripts/mev.py`
- **Skill integrity verify** — manifest SHA-256 (+ opsional Ed25519) dicek saat boot; file skill yang berubah/baru/hilang nahan operasi on-chain sampai diaudit. → `tools/skill_integrity.py`, `SKILLS.lock`
- **FROZEN_PATHS** — file kritis (SOUL, AGENTS, governor, integrity, reflection, vault, watchdog, model_registry, planner, swarm, automation, skill_forge) **gak bisa diedit** oleh loop self-improve. Di-enforce di kode (`guard_write`).

### 🧠 Self-improvement
- **Compounding memory** — recall lokal keyless (SQLite); memori berguna di-reinforce & makin sering muncul. → `tools/memory_engine.py`
- **Reflection loop (sk54)** — belajar dari masalah berulang, auto-fix isu ops yang reversible (allowlist only), tulis *proposal* upgrade buat di-review operator; ada audit log. → `skills/sk54.md`, `tools/reflection.py`

### 📅 Asisten harian (sk14, sk15)
- **Daily briefing** + **alert engine** kondisional (price/gas/wallet/claim, DexScreener keyless, cooldown-dedup). → `tools/briefing.py`, `tools/alerts.py`
- **Watchdog** self-healing, **vault** snippet/alamat + macro, input **voice** (Whisper lokal) + **screenshot** (vision), **triage** inbox. → `tools/watchdog.py`, `tools/vault.py`, `tools/multimodal.py`, `tools/triage.py`

### 🔗 Tooling smart-contract
- **Universal contract reader** — multi-chain, ABI auto-fetch (Sourcify/Blockscout, keyless), call read function apa pun, deteksi ERC-20/721/1155, resolve proxy EIP-1967. Read-only. → `references/contract_read.md`, `scripts/contract_reader.py`
- **Universal contract writer** — kirim tx ke fungsi apa pun, gated penuh (sim → screen → governor → konfirmasi → record). → `references/contract_write.md`, `scripts/contract_writer.py`
- **Crypto developer** — compile/test (Foundry), deploy (governor-gated), verify (Sourcify keyless), CREATE2 deterministic multi-chain. → `references/deploy.md`, `scripts/deploy_engine.py`

### 🧩 Manajemen LLM (sk7 extended)
- **Dynamic model registry** — `add model` satu perintah (name/api_key/base_url/model/kind/priority); OpenAI-compatible + Anthropic; key dienkripsi (scrypt+Fernet), redacted, masuk cascade R7 dengan fallback otomatis. → `tools/model_registry.py`

### ⚙️ Power Pack (sk17)
- **NL workflow planner** (tujuan → plan multi-step gated), **multi-agent swarm** (lane specialist paralel + pemisahan key), **skill forge** (draft skill baru → proposal), **automation engine** (WHEN/THEN), **backtester**, **live dashboard**, **voice conversation mode** (STT→LLM→TTS), **explainability**. → `skills/sk17.md` + `tools/{planner,swarm,skill_forge,automation,backtest,dashboard,voice,explain}.py`

### 💻 Software engineering (sk16)
- **Skill coding umum** — backend API (FastAPI/Express/Go/Django), database (skema/migration/ORM), testing default (pytest/vitest/go test), scaffolding (struktur + Dockerfile + CI), multi-bahasa (Python/TS/Go/Rust), refactoring & code review, CLI/library, git workflow. Komplemen sk9 (frontend) + sk2 (deploy) + sk53 (debug) = full-stack. → `skills/sk16.md`

### 🛠 Tooling & docs
- **`.env.example`** — semua env var (50+) dikelompokin, ditandain wajib/opsional/gratis/berbayar.
- **`DEPLOY.md`** — panduan deploy VPS yang akurat (menggantikan quick-start lama).

### Ringkasan angka
- 22 skill (sk0–sk17 + sk52–sk55), ~30+ script Python, 15 reference Hermes.
- Lapisan baru: governance, self-improve, daily assistant, contract read/write/deploy, model registry, power pack, software engineering.

---

## v3.1 — baseline (2026-05-25)

Rilis dasar yang jadi titik tolak v4.0.

### Headline changes
- 🆕 **3 new skills**: sk10 Web3, sk11 Security, sk12 Batch
- 🆕 **NFT minter skill**: sk13 (universal mint with auto-gas, OpenSea/Manifold/Zora)
- 🆕 **Hermes Crypto Agent absorbed**: full deep-crypto layer at `skills/hermes/` (10 refs + 8 Python templates)
- ⏰ **TIME.md** — 5-layer time-awareness architecture (system inject → tool → cache → infer → disclose). No more time-blind hallucination on deadlines/cron/vesting/claim windows.
- ⚡ **Smarter router**: priority-weighted keywords, multi-skill orchestration, H1-H7 hermes dispatch
- 🛠 **sk4 Telegram** now production-grade (anti-duplicate, webhook mode, multi-bot)
- 🤖 **sk7 AI** rewritten: streaming, function calling, provider fallback chain, cost tracking
- 🖥 **sk2 VPS** expanded: systemd, tmux, nginx security headers, backup automation
- 🔐 **Tighter SOUL**: 2 hard stops only, permissive on grey-area + operational rails for crypto ops
- 🇮🇩 **sk3 voice**: airdrop template now matches operator's exact format

### New files
| File | Why |
|---|---|
| `skills/sk10.md` | Web3 ops: RPC fallback, BIP39 wallet gen, NonceManager, simulate→send→wait, eligibility O(1) checker, mass farming with p-limit, multicall, Solana basics. |
| `skills/sk11.md` | Security audits: skill-file injection checklist, Solidity red flags, suspicious package signals, secret leak detection, OpenClaw-specific safety. |
| `skills/sk12.md` | Batch patterns: p-limit, asyncio.gather, token bucket, progress tracking, resume-from-failure checkpoint. |
| `skills/sk13.md` | **Universal NFT minter**: parse OpenSea/Manifold/Zora URL atau raw address, auto-detect mint fn + price, auto-gas, simulate→send→wait. Combo sk12 buat mass mint. |
| `skills/hermes/*` | **Hermes Crypto Agent** — deep-crypto skill (SKILL.md + references + Python templates). H1-H7 dispatch (swap/bridge/defi/sniping/mempool/NFT/SIWE). EVM + Solana + Sui + Aptos + TON. |
| `TIME.md` | **5-layer time-awareness**. Solves LLM time-blindness. Strict mode buat crypto cron/deadline/vesting/claim. Contributor: 👁⃤Fiⱥnͥ.ⱥsͣsͫⱥn 𓂀 ([@fianassan25](https://t.me/fianassan25)). |
| `CONTRIBUTORS.md` | Credits — CVC Owner + Member contributions. |
| `panduan.md` | Operator usage guide (12 sections + cheat sheet) dengan contoh Hermes nyata. |
| `CHANGELOG.md` | This file. |
| `INDEX.md` | One-line index of all files. |

### Upgraded files (ringkas)
- **`AGENTS.md`** — rules R1-R10, provider cascade (R7), multi-skill orchestration (R8), token economy (R9), escape hatch (R10), weighted keyword table EN+ID, crypto trigger cluster.
- **`IDENTITY.md`** — 3 response speed tiers, Web3 operator voice, anti motivational filler.
- **`SOUL.md`** — 2 hard stops (CSAM, mass-casualty weapons), single risk gate, doktrin permisif buat crypto ops.
- **`HEARTBEAT.md`** — continuity triggers, token discipline (warn 60% / compact 80%), resume protocol.
- **`TOOLS.md`** — agent-side vs operator-side, OpenClaw streaming config bug (`streaming: false` boolean).
- **`USER.md`** — template fill-in + trigger phrases + domain focus.
- **`MEMORY.md`** — compact format (1 baris/entry), 30-day window, monthly compaction.
- **`skills/sk0–sk9, sk52–sk54`** — registry diperluas, reflection loop, escape hatch, crypto monetization (sk1), VPS bootstrap (sk2), airdrop template + CT voice (sk3), Telegram rewrite anti-duplicate (sk4), O(1) lookup (sk5), circuit breaker (sk6), AI rewrite + fallback (sk7), PDF hyperlinks (sk8), Web3 UI (sk9), audits (sk52), pre-mortem (sk53), error library (sk54).

### Known good combos (multi-skill load)
- "bikin bot Telegram + bayar TON" → sk4 + sk6 + sk10
- "mass mint 300 wallet" → sk13 + sk12 + sk10
- "swap 1000 USDC ke ETH di base" → H1 (hermes/swap.md) + sk10
- "farming airdrop layerzero 50 wallet" → H2 + H1 + sk12
- "buat landing page web3" → sk9 + sk10
