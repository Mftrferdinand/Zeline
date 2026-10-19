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
3. Bounded memory context (6,000 characters for the legacy/native memory block,
   4,000 characters for per-turn recall; neither is a token-budget guarantee),
   lexical record inspection, stable record/revision citations and exact corrections
   through existing native tools.
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

Per-turn lexical retrieval with bounded cited context is implemented in the agent
boundary. Semantic/embedding retrieval, evidence-linked consolidation, complete
forget across archives/backups, GEPA/held-out optimization and optional OS execution
backends remain separately testable follow-ups. A generic plugin callback is not an
execution sandbox. Changes to live security policy and deployment require
explicit operator approval.

## Follow-up: atomic completed-turn persistence

`SessionPersistence.save_turn` commits the active conversation window and both
archive messages in a single SQLite transaction. An archive insertion error rolls
back the window and any partial archive inserts. The existing `save` and
`append_turn` APIs remain available to other callers.

SessionStore uses the transactional method for completed turns. A commit failure
preserves the in-memory answer, adds a visible warning to non-cancelled replies,
and records a redacted `persistence_error` in session status and the application
log. Successful later persistence clears that diagnostic. Exception messages,
identities and conversation contents are not included in the warning/log.

A later success saves the current history window but does not backfill missing
archive rows from a previous failed turn. This remains completed-turn persistence,
not recovery of an interrupted tool sequence, exactly-once external execution,
or a fix for every legacy best-effort read/archive API.

## Follow-up: interrupted foreground turn checkpoints

SessionStore now supplies a checkpoint callback to its agent. The agent records
its user input before a provider request, its tool-call batch before dispatch,
and each completed tool result in live history before invoking the checkpoint or
UI result callbacks. Parallel read-only batches are appended once all submitted
futures return, before their checkpoint callback runs. If checkpoint storage
fails, the in-memory evidence remains exportable rather than being discarded.
If one parallel call exhausts the shared budget, all submitted futures are drained
and their completed results are preserved by tool-call ID before the error is
returned. Only calls without recorded results are repaired as UNKNOWN. This also
covers a later successful future following an earlier failed future; no automatic
retry is performed.
Direct Zeline/one-shot CLI callers without this callback keep their existing
behavior; durable delegation has its separate job store.

Snapshots live in a separate `turn_checkpoints` SQLite table, keyed by hashed
identity and bounded with the same history-window limits as session persistence.
The last committed conversation and archive are not overwritten by partial work.
A completed turn atomically writes its window/archive and deletes its checkpoint.
A failed final commit retains the checkpoint. Explicit history save/import and
session reset clear stale pending state for that identity.

On restart, the interrupted snapshot takes precedence for that identity. Tool
protocol repair preserves completed results and labels missing results UNKNOWN:
an external side effect can occur before the process records its result. A
recovery note tells the next turn to inspect actual state before retrying a
mutation. Restoring the session never starts a provider call or replays tools.
It waits for the next user message. There is no exactly-once guarantee, automatic
resumption, or reconstruction of an interrupted stream. If a tool finishes but
its checkpoint cannot be saved, the last durable result remains uncertain.

Checkpoint write failures stop execution before further tool dispatch; completed
side effects are not rolled back. Errors/logs contain redacted diagnostics.
Unrestorable saved state refuses session construction instead of silently
starting an empty conversation. Disk failures still require operator recovery.
No cross-process ownership lease has been added to foreground sessions: run one
SessionStore owner per identity. This does not turn session snapshots into a
multi-process distributed workflow engine.

Verification includes real subprocess exit during a second mutating fixture,
identity isolation, pre-dispatch and post-effect save failures, atomic completion,
retained snapshots on final-commit failure, explicit reset/import, corrupt-state
preservation, and parallel read-result persistence before UI delivery.

## Shared foreground dispatch limits

Configure `agent.max_provider_requests_per_turn` and
`agent.max_tool_calls_per_turn` with nonnegative integer limits (maximum 100000).
Zero, the backward-compatible default, disables that particular limit. For
example, use 30 provider attempts and 100 tool calls for a bounded interactive
workload. Invalid limits stop the turn before contacting a provider.

A foreground turn shares one thread-safe counter with its synchronous children
and optional reviewer. Each provider HTTP attempt reserves its allowance before
network dispatch, including retries, fallback models, and streaming fallback.
Each ToolExecutor call also reserves before observer or native/MCP dispatch.
Concurrent children cannot each receive a fresh allowance. The next independent
foreground turn starts fresh. Exhaustion returns an explicit error and prevents
further dispatch; it does not roll back completed effects or cancel an external
request already in flight. Existing wall-clock/iteration limits still apply.

These are request/call limits, not a dollar cap or token quota. Background durable
jobs, separate cron runs and later reflection passes are independent runs, not
part of the original foreground turn's shared counters. There is no global
account-wide allowance or recovery of counters across restarts in this slice.

## Verifier outcomes

A synchronous reviewer must begin with `VERDICT: ok` or `VERDICT: problems`.
Problems produce an error envelope while preserving all worker evidence. A
missing/unrecognized verdict is explicitly inconclusive and is not accepted as
completion. Child error/cancel responses are no longer counted as successful
worker tasks merely because their Python call returned without raising.
Verification is still optional and model-based: an `ok` verdict is not a
substitute for deterministic application tests or independent evidence.
