# HEARTBEAT.md — Session Heartbeat Checklist (v7)
# SUPERAGENT V7 IRONCLAW SUPREME
# Runs every session start. Keep token cost low.

---

## On Every Heartbeat

```
1. Read memory/[today YYYY-MM-DD].md  → if exists, scan last 5 entries
2. Read MEMORY.md tail                → active projects + locked decisions + team context
3. Re-confirm USER.md preferences     → name, language, tone, level, team members active
4. Skill registry already in m0.md    → do not re-read until intent matched
5. (crypto-enabled) skill integrity   → tools/skill_integrity.py verify  [v4.0]
   exit 0 → proceed | exit != 0 → hold on-chain ops, surface [MODIFIED]/[NEW]/[MISSING]
6. (self-improve, x4) reflection cycle → tools/reflection.py daily_cycle  [v4.0]
   learn from recent memory + report pending proposals; auto-fix only SAFE_AUTO_ACTIONS
7. (m14) daily briefing if due         → tools/briefing.py push_briefing (once/day guard)  [v4.0]
   alert engine runs as background poll (tools/alerts.py run)
8. (m15) watchdog.touch_heartbeat()    → mark bot alive; watchdog/triage run as services  [v4.0]
9. (v7) autonomous scan               → scan memory + projects for pending autonomous actions
   auto-execute SAFE_AUTO_ACTIONS; flag others with priority+context to operator
10. (v7) profit ledger init           → tools/profit_ledger.py init_session
    track P&L from session start (gas, costs, revenue, profit)
    reference MEMORY.md REVENUE LOG for running aggregates
```

---

## Session Continuity Triggers

If memory shows pending task > 24h without update → flag at first opportunity:
> `Catatan: [task X] dari [date] masih open. Lanjutin atau drop?`

If memory shows incomplete deployment → offer resume:
> `Sesi terakhir: deploy [project] di tahap [step]. Lanjut dari sini?`

If operator returns after gap > 7 days → quick context recap (3 lines max):
> `Last session: [project] | Last decision: [X] | Open: [Y]`

### Team Session Continuity (v7)

If team member detected in USER.md → check last interaction per member:
> `Team: [name] last active [date] — [open tasks / none]`

If team member has active session context from prior session → load their relevant context:
> `[name]'s last context: [1-line summary] | Open: [task]`

If multi-member session detected → track per-member state internally, surface conflicts.

If Level 2+ member issues command conflicting with another active member → flag:
> `⚠️ Conflict: [member A] wants [X] vs [member B] wants [Y]. Priority: Level [A] vs Level [B]. Resolve?`

---

## Per-Session State Tracking

Track internally (only output if asked):
```
session_start:      timestamp
goal:               stated objective
active_skill:       [m4, m6, ...]  // loaded modules
decisions:          []
blockers:           []
files_touched:      []
tokens_used:        approximate count
time_source:        layer_1 | layer_2 | layer_3 | layer_4 | layer_5
last_time_check:    timestamp
active_team_members: [name, name]  // who's in this session
session_p&l:        { revenue: 0, costs: 0, net: 0 }  // v7 profit ledger
```

### Time refresh during heartbeat
On each heartbeat:
1. Check if `[RUNTIME CONTEXT]` present in current message → update `last_time_check`
2. If absent and last check >30min ago → call `get_current_time` tool
3. If time-sensitive task incoming (cron, deadline, vesting, claim) → force refresh regardless of cache age

---

## Token Discipline

- Skill files: load on demand, never preemptively (58 skills available, zero preload)
- After heavy operation (large code dump, file gen) → suggest:
  `Lanjut di sesi baru biar context fresh?` (only if obviously bloated)
- Memory writes: append only — never re-dump entire memory
- Hard ceiling: 12k context spent on system. If higher, run sk52 audit.
- Always-on bootstrap: ~3.5k (AGENTS + SOUL + IDENTITY + USER + TOOLS + MEMORY + HEARTBEAT)
- V7 additions (team context + revenue log) add ~0.5k to always-on budget vs v4.2
