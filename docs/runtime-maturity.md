# Local-first runtime maturity

## Scope and compatibility

This work builds incrementally on the v0.3.4-derived SkillForge/cancellation
branch. It does not reuse later release implementations, modify the App/UI,
change model configuration, install the package, restart a gateway, or publish.
No additional service or mandatory dependency is introduced.

## Acceptance slices

1. One result classifier shared by SkillForge, audit and lessons. Nonzero exit
   results cannot resolve a failure; cancelled/pending results cannot teach a fix.
2. Transactional identity-scoped facts with one-time, lossless JSON import,
   stable IDs/revisions, final-state atomic batches and stale-write rejection.
3. Bounded memory context (6,000 characters, not a token-budget guarantee),
   lexical record inspection and exact corrections through existing native tools.
4. Durable local job/dependency records, lease-token claims, expired-worker
   fencing, restart recovery and identity-scoped result handoff. Existing
   synchronous delegation remains available and unchanged by default.
5. End-to-end disposable-state integration tests plus the existing regression
   suite. No live provider credentials or paid API calls are required for tests.

## Memory storage and rollback

`memory/memory.sqlite3` becomes authoritative for an identity after its first
successful import/write. The original hashed JSON or CLI `memory.json` is never
overwritten or deleted. Invalid legacy data prevents import rather than being
interpreted as empty memory. SQLite transactions serialize cross-process writes.

Original JSON is a migration backup, not a continuously updated mirror. An old
build must not write it concurrently after migration. Downgrading after new writes
requires an explicit export of the active SQLite facts; do not simply install an
old binary and assume it can see new data. Deployment/migration is separate from
this source change and requires a private state backup first.

`add_memory` keeps its original `fact` form and accepts an optional `operations`
batch instead. `list_memory(include_metadata=true, query=...)` exposes IDs and
revisions. Batch `replace`/`remove` can use `expected_revision`; all changes commit
together and limits apply to the final active state. Reflection cannot use a batch
to overwrite explicit user facts. The substring removal API remains compatible.

Removal hides an active fact; historical revisions are retained. This is not a
privacy purge. No semantic inference, automatic contradiction detection, embeddings,
owner identity linking, or verified message-source citation is claimed by this
slice. Records stay identity-scoped; an ID from another identity cannot be changed.

## Durable jobs and graph execution

`delegate_task` stays synchronous by default. For persisted work, submit with
`background=true` (or `action="submit"`) and a stable `idempotency_key`. A graph
uses `tasks=[{key, goal, context, role, depends_on, retry_safe, max_attempts}]`.
Dependencies can name other keys in the same graph or already-owned job IDs.
Submission validates the entire graph in one transaction: cycles, unknown parents
and cross-owner dependencies roll back every inserted node.

Inspect through `action="list"`, `"status"` with `job_id`, or `"results"`.
Acknowledge each consumed terminal result using `action="ack"`. Inspection does
not acknowledge it. `action="cancel"` fences running output and blocks descendants;
it cannot reverse a shell command or external request already executed.
A rebuilt parent session discovers outstanding IDs through a metadata-only
prompt block, not an unbounded dump of worker output.

The existing gateway owns one serial worker, separate from recurring cron.
Independent worker processes can also coordinate through the same atomic claims;
this slice does not promise parallel background execution in one gateway.
No daemon is launched by a submission and no extra service is installed.
Queued work therefore stays queued until an updated gateway/worker is running.
The original task-board `update_task` interface remains separate and unchanged.

Each running attempt has an expiring, heartbeat-renewed lease and unique token.
Completion requires the current unexpired token. Expired work is failed with an
uncertain-outcome warning by default, never silently replayed. Only an explicitly
`retry_safe=true` node may be requeued, up to `max_attempts` (1-5). This restarts
the task from its stored brief and bounded dependency results; it does **not**
resume a half-executed external tool or reconstruct a provider stream. Callers
remain responsible for real-world idempotency; a lease is not exactly-once I/O.
Submission idempotency only deduplicates identical requests, within one owner.

The worker uses the host provider/model configuration at execution time and the
stored tool profile/workspace. Per-job provider/model overrides, model pinning,
step checkpoints and a durable verifier are not implemented. Worker completion
means the agent returned a non-failure response, not independent verification of
its claims. Use an explicit dependent reviewer/test node for acceptance evidence.
A blanket `verify=true` durable request is rejected rather than silently ignored.
Disabled delegation pauses execution of previously queued jobs.

Local bounds: 32 nodes/submission, 1,000 jobs/owner, 10,000 jobs/database,
20,000 goal characters, 50,000 context characters, 100,000 stored result characters
and 32,000 dependency-result characters per claimed job. History is never silently
evicted. Reaching a capacity requires operator-managed archival; no automatic
history purge is provided. No automatic Telegram result posting is added: the
parent consumes results through the native tool. The worker releases its owned
MCP/browser/LSP resources on success or failure.

## Follow-up gates, not implemented claims

The subsequent recurring-cron increment (script-only execution, scoped agent
options and snapshot chaining) is documented separately in [cron-maturity.md](cron-maturity.md).
Cron model selection does not add model pinning to the durable delegation worker.

The opt-in fail-closed tool-dispatch gate is documented separately in
[execution-policies.md](execution-policies.md); it does not sandbox policy code
or govern direct cron script processes.

Per-turn semantic retrieval, evidence-linked consolidation, complete forget across
archives/backups, GEPA/held-out optimization and optional OS execution backends
remain separately testable follow-ups. A generic plugin callback is not an
execution sandbox. Changes to live security policy and deployment require
explicit operator approval.
