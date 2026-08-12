# sk49 — Team Orchestration & Multi-User Ops (SUPERAGENT V7)
# Load when: team, tim, orchestration, orkestrasi, multi-user, multi-client, multi-operator, delegasi
# Category: Team & Collaboration

## TRIGGER KEYWORDS

### HIGH WEIGHT (3pt)
team, tim, orchestration, orkestrasi, multi-user, multi-client, multi-operator,
task assignment, bagi tugas, delegation, delegasi, billing, klien, client dashboard,
role-based access, RBAC, hierarchy

### MEDIUM WEIGHT (2pt)
collaboration, kolaborasi, multi-session, session sharing, workspace sharing,
permission, izin akses, sub-agent coordination, agent swarm, task routing,
conflict detection, deteksi konflik

### LOW WEIGHT (1pt)
shared context, context propagation, team management, manajemen tim,
billing attribution, atribusi billing, operator group, grup operator

---

## OVERVIEW

sk49 governs multi-user team orchestration within SUPERAGENT. It manages a tiered
access hierarchy (Level 0–3), task assignment and routing across multiple human
operators, billing attribution per client/operator, conflict detection between
concurrent sessions, and a unified client dashboard.

Unlike single-operator workflows (default sk0–sk39), sk49 activates when >1 human
operator shares the SUPERAGENT runtime or when a single operator manages multiple
clients with segregated contexts.

---

## HIERARCHY: Level 0–3 ACCESS MODEL

### Level 0 — Owner / Root
- Single identity (the "Sovereign Operator")
- Full unrestricted access to ALL workspaces, all operators, all clients
- Can create/delete Level 1–3 operators
- Can override any agent decision, kill any session, read any log
- Controls billing aggregation and treasury (links to sk50)
- CANNOT be demoted or locked out (hard-coded fail-safe)
- Identity verified by: primary Telegram ID + pre-shared recovery phrase

### Level 1 — Admin Operator
- Full access to assigned workspaces only
- Can create/delete Level 2–3 operators within their domain
- Can assign tasks to any Level 2–3 operator
- Can view billing for assigned clients
- Cannot access Owner-level configs (API key rotation, treasury withdrawal)
- Can override Level 2–3 decisions but audit trail logged

### Level 2 — Trusted Operator
- Full task execution within assigned workspaces
- Can spawn sub-agents within resource caps
- Can read/write files in assigned directories
- Cannot create/delete other operators
- Cannot access billing or treasury
- All actions logged with operator ID
- Session isolation by default (no cross-client data leak)

### Level 3 — Guest / Limited Operator
- Read-only access to assigned workspaces (by default; write toggle available)
- Can query but cannot mutate (configurable per workspace)
- Cannot spawn sub-agents
- Cannot access any billing, treasury, or admin panels
- All queries logged
- Session auto-expires after configurable TTL (default: 4 hours)

---

## WORKFLOW: TASK ASSIGNMENT & ROUTING

### Step 1 — Task Intake
```
Operator sends task → agent classifies urgency + domain
  ├─ Critical (funds, security, downtime)  → auto-escalate to Level 0
  ├─ High (revenue, client deliverable)    → route to best-fit Level 1–2
  ├─ Normal (standard ops)                 → queue, assign available operator
  └─ Low (research, nice-to-have)          → backlog, assign when idle
```

### Step 2 — Operator Availability Check
```
Query operator status table:
  - online / idle / busy / away
  - current task count (max concurrent per operator)
  - workload score (weighted by task urgency)
  - timezone offset (respect working hours if configured)
```

### Step 3 — Skill-Based Routing
```
Match task domain → operator skill tags:
  Example tags: web3, solidity, frontend, backend, devops, content, research
  Fallback: round-robin among available operators at same level
  Override: Level 0–1 can force-assign to specific operator
```

### Step 4 — Task Lifecycle
```
ASSIGNED → operator notified (Telegram DM + dashboard)
         ↓
ACCEPTED → timer starts, context loaded, billing attribution set
         ↓
IN_PROGRESS → agent provides execution environment
         ↓   (on timeout: auto-return to queue with notification)
BLOCKED → operator flags, reason logged
         ↓
COMPLETED → result delivered, billing invoice generated
         ↓
REVIEWED → Level 0–1 can accept/request revision
```

### Step 5 — Context Propagation
```
When Operator A hands off to Operator B:
  1. Session snapshot: all state, env vars, open files, agent memory
  2. Handoff note: reasoning, decisions made, next steps
  3. Operator B receives full context on accept
  4. Audit trail: A→B handoff timestamped
```

---

## CONFLICT DETECTION & RESOLUTION

### Conflict Types
```
FILE CONFLICT: Two operators modify same file concurrently
  → Last-write-wins with versioning (git-style merge if possible)
  → Both operators notified of conflict
  → Level 0–1 resolves manually if merge conflict

RESOURCE CONFLICT: Two operators target same resource (API key, wallet, DB)
  → Lock mechanism: first operator gets exclusive lock
  → Second operator queued or offered alternative resource
  → Lock TTL: configurable (default 5 min, extendable)

TASK CONFLICT: Two operators assigned overlapping tasks
  → Agent detects before execution
  → Merges if redundant; splits if complementary
  → Notifies Level 0–1 if resolution ambiguous

STATE CONFLICT: Two operators make contradictory decisions
  → Agent flags logical inconsistency
  → Both operators notified with diff of decisions
  → Level 0 overrides or operators negotiate
```

### Conflict Prevention Rules
- Each workspace: max 1 active writer (readers unlimited)
- Wallet operations: serialized globally (one tx at a time across all operators)
- Config changes: require Level 1+ and are serialized
- Agent memory writes: operator-scoped by default, shared only on explicit share

---

## BILLING ATTRIBUTION

### Per-Client Tracking
```
Session metadata tagged with:
  - client_id (if multi-client)
  - operator_id
  - task_category
  - duration (wall clock + compute)
  - model_tokens_consumed
  - external_api_calls (count + estimated cost)
  - on_chain_gas_spent
  - success_metric (completed / failed / blocked)
```

### Invoice Generation
```
Daily/weekly/monthly aggregation:
  ├─ Per client: total cost, breakdown by operator, category
  ├─ Per operator: productivity, billable hours, cost efficiency
  └─ Per category: cost center analysis

Output format: CSV, PDF (via sk8), or dashboard widget
Integration: Midtrans (ID), crypto invoices (USDC/USDT on Polygon)
```

### Cost Allocation Rules
- Shared resources (model tokens, API calls): split by usage %
- Operator-specific costs: attributed directly
- Idle overhead: amortized across all active clients
- Treasury management links → sk50 for profit optimization

---

## CLIENT DASHBOARD

### Widgets
```
1. Active Sessions: live operators online + current tasks
2. Task Queue: pending / in-progress / blocked / completed
3. Cost Tracker: real-time spend (model, API, gas, infra)
4. Productivity: tasks completed per operator per day
5. Alerts: SLA breaches, cost spikes, conflict flags
6. Client Health: satisfaction score, churn risk indicator
```

### Access Control
- Level 0: full dashboard, all clients
- Level 1: dashboard for assigned clients only
- Level 2: task-level view only (own tasks)
- Level 3: read-only summary (no financials)
- Client-facing: optionally expose curated dashboard (white-label)

---

## MULTI-SESSION AWARENESS

### Cross-Session State
```
Shared Memory Pool:
  - operator_preferences: per-operator persisted
  - client_context: per-client (brand voice, stack, contacts)
  - workspace_state: shared files, env vars, running services
  - agent_knowledge: cross-operator learnings (opt-in sharing)

Session Isolation:
  - Each operator session has private memory (not exposed to others)
  - Explicit "share" command to publish learnings to shared pool
  - Level 0 can read all private memories (audit mode)
```

### Operator Presence
```
Status broadcast:
  - Telegram presence (if bot has access)
  - Custom status: "working on X", "available", "do not disturb"
  - Auto-idle detection: no interaction > N minutes

Handoff protocol:
  - Operator going offline → auto-reassign queued tasks
  - Emergency escalation: critical tasks auto-route to next available Level 1+
```

---

## SECURITY & AUDIT

### Cryptographic Authentication (tools/team_auth.py)
- Ethereum wallet signatures (personal_sign / EIP-191) verify operator identity
- Challenge-response with 5-minute expiry prevents replay attacks
- Level 0 (Sovereign) treasury ops require FRESH cryptographic proof
- Spend Governor (governor.py) calls team_auth for pre-flight authorization
- Multiple crypto library fallbacks: eth-account → web3.py → coincurve → ecdsa (pure Python)

### Audit Trail
- Every action logged with: operator_id, timestamp, action, result
- Immutable append-only log (daily rotation, 90-day retention)
- Level 0 can export full audit trail
- Anomaly detection: unusual patterns (time, volume, category) flagged

### Access Revocation
- Instant kill-switch per operator (Level 0 only)
- Session token rotation on Level change
- API key access: Level 1+ can view masked keys, Level 0 full keys
- Wallet access: Level 0 only for private keys; Level 1 can sign with approval

### Breach Response
```
Suspected compromise:
  1. Level 0 alerted immediately (Telegram + email if configured)
  2. Suspect operator session frozen (read-only)
  3. All active sessions of suspect operator killed
  4. Last N actions replayed for audit
  5. API keys in suspect's scope auto-rotated
  6. Full incident report generated
```

---

## TOOLS USED

| Tool | Purpose |
|------|---------|
| Telegram Bot API | Operator notification, task assignment, status |
| PostgreSQL | Operator registry, task queue, audit log |
| Redis | Session state, locks, presence |
| Midtrans/Crypto | Billing & invoicing |
| sk50 | Treasury & P&L integration |
| sk15 | Session logging & persistence |
| sk4 | Cron-based task scheduling & reminders |
| tools/team_auth.py | Cryptographic auth via Ethereum wallet signature (SIWE/EIP-712); Level 0-3 enforcement, challenge-response, treasury op gate |

---

## SAFETY RAILS

1. **No cross-client data leak**: Client A's data never visible to Client B's operators
2. **Wallet serialization**: Only one on-chain operation globally at any moment
3. **Config change audit**: All Level 0 config changes logged immutably
4. **Rate limiting**: Max N tasks per operator (prevents overload)
5. **Session TTL**: Guest sessions auto-expire
6. **Revocation over network**: Kill-switch works even if operator is offline
7. **Recovery phrase**: Level 0 identity verified by pre-shared recovery phrase
8. **Conflict freeze**: Unresolved conflicts freeze affected workspace until resolved

---

## DELEGATION INFO

```
Sub-agent spawning:
  Level 0–2 can spawn sub-agents (depth limit: 3)
  Sub-agents inherit operator's access level
  Sub-agent resource usage billed to spawning operator's client
  Sub-agent session visible in parent's dashboard

Cross-operator delegation:
  Operator A → Operator B: "delegate task_id to @operatorB"
  B receives notification + context snapshot
  Billing: credited to B, attributed to A's client
  Audit: A→B delegation logged
```

---

## CONFIGURATION REFERENCE

```yaml
team:
  owner:
    telegram_id: "323461038"  # Sovereign Operator
    recovery_phrase_hash: "sha256..."
  
  operators:
    - id: "op_001"
      level: 1
      telegram_id: "..."
      workspaces: ["crypto", "content"]
      skill_tags: ["web3", "solidity", "defi"]
      max_concurrent_tasks: 3
      
    - id: "op_002"
      level: 2
      telegram_id: "..."
      workspaces: ["content"]
      skill_tags: ["writing", "social-media"]
      max_concurrent_tasks: 5
  
  settings:
    guest_session_ttl_minutes: 240
    conflict_detection: strict  # strict | lenient | off
    auto_handoff_on_idle_minutes: 30
    billing_currency: "USDC"  # USDC | IDR | USD
    audit_retention_days: 90
```

---

## QUICK REFERENCE: COMMANDS

```
/team status                    → show all operators + current tasks
/team assign @op task_id        → force-assign task
/team revoke @op                → revoke operator access
/team promote @op level         → change operator level
/team dashboard                 → client dashboard snapshot
/team invoice client_id period  → generate billing invoice
/team handoff @op               → handoff current session
/team audit [@op] [date]        → view audit trail
/team lock workspace            → freeze workspace (conflict resolution)
/team share memory_key          → publish private memory to shared pool
```

---

## VERSION

1.0.0 — 2026-07-08 — Initial release for SUPERAGENT V7
