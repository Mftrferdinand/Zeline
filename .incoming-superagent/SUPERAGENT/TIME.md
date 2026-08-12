# TIME.md — Time Awareness Architecture (v7)
# Auto-injected every session. Always-on. NOT a skill — runtime infrastructure.

---

## Problem Statement

LLM tidak punya jam internal. Tanpa source eksternal → fallback ke training cutoff atau halusinasi tanggal. Critical issue untuk crypto ops:
- Mint window timing
- Airdrop claim deadlines
- Cron scheduling
- Block timestamp validation
- Vesting unlock dates
- Token listing times

This module enforces source-aware, drift-aware, honest-when-uncertain time handling.

Knowledge cutoff: **2026-01-31**. Past this date, time MUST come from external source.

---

## 5-Layer Architecture

Ordered by reliability — Hermes checks Layer 1 first, falls through.

```
┌──────────────────────────────────────────────┐
│ LAYER 1: SYSTEM-INJECTED TIMESTAMP            │  most reliable
│ Source: host wrapper injects [RUNTIME CONTEXT]│
├──────────────────────────────────────────────┤
│ LAYER 2: REALTIME TOOL CALL                   │  reliable
│ Source: get_current_time tool / public API    │
├──────────────────────────────────────────────┤
│ LAYER 3: SESSION-SCOPED CONTEXT VAR           │  cached
│ Source: last_time_check from earlier turn     │
├──────────────────────────────────────────────┤
│ LAYER 4: USER-DERIVED INFERENCE               │  fallback
│ Source: clues from user's message             │
├──────────────────────────────────────────────┤
│ LAYER 5: EXPLICIT DISCLOSURE                  │  last resort
│ Source: none — disclose honestly              │
└──────────────────────────────────────────────┘
```

---

## Layer 1 — System Prompt Injection (PRIMARY)

Host wrapper prepends this block before every message (or at session start for short sessions):

```
[RUNTIME CONTEXT - INJECTED BY HOST]
Current datetime  : 2026-05-25T11:54:00+07:00
Timezone          : Asia/Jakarta (WIB)
Weekday           : Monday
Unix epoch        : 1748145240
Session ID        : <uuid>
Knowledge cutoff  : 2026-01-31
Days since cutoff : 114
```

### Host-side generator (Python)
```python
from datetime import datetime, timezone
import zoneinfo, uuid

def build_runtime_context(user_tz: str = "Asia/Jakarta") -> str:
    tz = zoneinfo.ZoneInfo(user_tz)
    now = datetime.now(tz)
    cutoff = datetime(2026, 1, 31, tzinfo=timezone.utc)
    delta = (now - cutoff).days
    return f"""[RUNTIME CONTEXT - INJECTED BY HOST]
Current datetime  : {now.isoformat()}
Timezone          : {user_tz}
Weekday           : {now.strftime('%A')}
Unix epoch        : {int(now.timestamp())}
Session ID        : {uuid.uuid4()}
Knowledge cutoff  : 2026-01-31
Days since cutoff : {delta}
"""

# Prepend to system prompt
system_prompt = build_runtime_context() + open("AGENTS.md").read()
```

### Host-side generator (Node.js)
```javascript
function buildRuntimeContext(userTz = 'Asia/Jakarta') {
  const now = new Date();
  const cutoff = new Date('2026-01-31T00:00:00Z');
  const delta = Math.floor((now - cutoff) / 86400000);
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: userTz, weekday: 'long'
  });
  return `[RUNTIME CONTEXT - INJECTED BY HOST]
Current datetime  : ${now.toISOString()}
Timezone          : ${userTz}
Weekday           : ${fmt.format(now)}
Unix epoch        : ${Math.floor(now.getTime() / 1000)}
Session ID        : ${crypto.randomUUID()}
Knowledge cutoff  : 2026-01-31
Days since cutoff : ${delta}
`;
}
```

### Refresh strategy
- **Per-message**: regenerate every user turn (best for long sessions)
- **Per-session**: inject once at start (cheap, fine for <1h)
- **Hybrid**: inject at start, refresh if last_check >30min ago

---

## Layer 2 — Realtime Tool Call

If host didn't inject, or precision needed, call a tool.

### Tool definition
```json
{
  "name": "get_current_time",
  "description": "Get current datetime. Call when user asks about current time, makes time-sensitive query, or [RUNTIME CONTEXT] is missing.",
  "input_schema": {
    "type": "object",
    "properties": {
      "timezone": { "type": "string", "default": "Asia/Jakarta" },
      "format": { "type": "string", "enum": ["iso8601", "human", "unix", "all"], "default": "all" }
    }
  }
}
```

### Implementation (Python)
```python
def get_current_time(timezone: str = "Asia/Jakarta", format: str = "all"):
    import zoneinfo
    from datetime import datetime
    tz = zoneinfo.ZoneInfo(timezone)
    now = datetime.now(tz)
    return {
        "iso8601": now.isoformat(),
        "human": now.strftime("%A, %d %B %Y, %H:%M:%S %Z"),
        "unix": int(now.timestamp()),
        "timezone": timezone,
        "weekday": now.strftime("%A"),
        "year": now.year, "month": now.month, "day": now.day
    }
```

### Public API fallback (no custom tool needed)
- `https://worldtimeapi.org/api/timezone/Asia/Jakarta` — free, no auth
- `https://timeapi.io/api/Time/current/zone?timeZone=Asia/Jakarta`

### On-chain time (CRITICAL for crypto ops)
For time-dependent on-chain actions, block timestamp > system time:
```python
# EVM
block = w3.eth.get_block('latest')
on_chain_ts = block['timestamp']

# Solana
slot = client.get_slot().value
block_time = client.get_block_time(slot).value
```

If `abs(system_time - on_chain_ts) > 60s` → flag desync. Use on_chain for execution decisions (deadline, expiry).

---

## Layer 3 — Session-Scoped Context Variable

Once time is known, cache for the session.

### State pattern
```
[HERMES INTERNAL STATE]
session_start_time : 2026-05-25T11:54:00+07:00
session_user_tz    : Asia/Jakarta
last_time_check    : 2026-05-25T12:15:00+07:00
time_source        : layer_1_injected
```

### Refresh triggers
Re-fetch when ANY of:
- User asks for current time explicitly
- User mentions time-sensitive event (meeting, deadline, claim window)
- `>30 min` since `last_time_check`
- About to execute time-dependent action (cron, deadline check, vesting query)

---

## Layer 4 — User-Derived Inference

Last graceful fallback. Scan user message for time clues.

### Pattern detection table
| User mention | Inference |
|---|---|
| "kemarin tanggal 23" | today = 24 |
| "meeting jam 3 sore ini" | now < 15:00, same day |
| "Senin pagi gini..." | today = Monday, morning |
| "tahun depan 2027" | now = 2026 |
| Filename `report_2026-05-25.pdf` | created ~May 25, 2026 |
| "iPhone 17 baru launch" | now ≥ Sept 2025 |
| "kemarin Trump..." | recent timeframe |

### Inference rules
- ALWAYS mark as assumption: `"Asumsi sekarang ~{date} berdasarkan {clue} — koreksi kalau salah"`
- Conservative: only year known → don't claim specific date
- ALWAYS disclaim for time-sensitive output

---

## Layer 5 — Explicit Disclosure (Last Resort)

All layers failed → be honest, don't fabricate.

### Template
```
Gue gak punya akses jam real-time di session ini. Yang gue tahu:
- Training cutoff: ~Januari 2026
- Gak ada [RUNTIME CONTEXT] di-inject
- Gak ada tool waktu aktif

Untuk info time-sensitive, lo bisa:
1. Kasih tau gue tanggal/waktu sekarang
2. Aktifkan tool get_current_time di host config
3. Gue jawab dengan asumsi data per Jan 2026 (kasih disclaimer)
```

### What NEVER to do
- ❌ "Hari ini Senin, 14 Oktober 2024" — fabrication
- ❌ "Sekitar pukul 3 sore" — random guess
- ❌ Silently use cutoff date as "now"
- ❌ Claim time-sensitive fact without disclaimer

---

## Decision Flow

```
Need time?
   │
   ▼
[Layer 1] [RUNTIME CONTEXT] in system prompt?
   ├── YES → use it
   └── NO ↓
[Layer 2] get_current_time tool available?
   ├── YES → call it, cache result
   └── NO ↓
[Layer 3] Cached from earlier turn? Fresh (<30min)?
   ├── YES → use cache
   └── NO ↓
[Layer 4] Clue in user message?
   ├── YES → infer + tag as assumption
   └── NO ↓
[Layer 5] Disclose honestly + offer alternatives
```

---

## Strict Mode (recommended for crypto ops)

When `strict_mode: true` in config:
- REFUSE time-sensitive claims without verified source (Layer 1 or 2)
- For cron / deadline / vesting / claim window operations: REQUIRE Layer 1 or 2 — fail otherwise
- Block transactions that depend on time without verified `now`

Examples requiring strict mode:
- "claim airdrop X sebelum jam berapa?"
- "vesting unlock kapan?"
- "Apakah listing sudah buka?"
- Anything cron-scheduled

---

## Configuration

```yaml
time_awareness:
  enabled: true
  primary_source: system_inject       # system_inject | tool_call | hybrid
  auto_refresh: true
  refresh_interval_min: 30
  user_tz: Asia/Jakarta               # operator timezone
  cutoff_date: 2026-01-31
  strict_mode: true                   # refuse time-sensitive without source
  fallback_layers: [tool, session, inference, disclose]
  on_chain_sanity_check: true         # warn if block time vs system time differs >60s
```

---

## Integration with brain

When boot sequence runs `AGENTS.md`:
1. Check for `[RUNTIME CONTEXT]` block in system prompt
2. If absent → flag layer source as Layer 2 fallback
3. Initialize session state with `session_start_time`
4. Set `time_source` flag visible to skills that need it

When any skill needs time:
1. Read `time_source` flag
2. If Layer 4 or 5 → output includes assumption tag
3. If strict_mode + Layer 4/5 → refuse and request real source

---

## Validation tests

| Test | Setup | Expected |
|---|---|---|
| L1 active | `[RUNTIME CONTEXT]` injected | Use it directly |
| L2 fallback | No inject, tool available | Call tool, cache |
| L3 persistent | Asked once, cached, ask again <30min | Use cache |
| L3 expiry | Cache >30min old | Re-fetch |
| L4 inference | No L1/L2/L3, user says "kemarin 23" | Assume today=24, tag assumption |
| L5 disclosure | Nothing available | Honest disclosure, no fabrication |
| Strict refuse | strict_mode + crypto cron + no L1/L2 | Refuse + request source |

---

*Hermes is no longer time-blind. Source-aware. Drift-aware. Honest when uncertain.*
