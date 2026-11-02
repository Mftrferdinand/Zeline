# Durable task continuity

## Scope

Task routing is separate from transcript retention. The current operator request controls focus; archived messages and checkpoints are historical data, not permission to replay tools. This implementation does not claim general semantic task classification or model parity.

### Admission and focus

`TaskContextStore` uses a private SQLite database under the configured data directory, independent of trimmed chat history. Owner identities are hashed; task records, focus and Telegram message anchors are owner-scoped. Read/modify/write operations use short SQLite transactions and close their connections.

- A substantive request creates a task and parks the previously active task.
- A bare continuation resumes only the focused unfinished task. Completing B does not implicitly resume older A.
- A same-chat Telegram reply selects its recorded task. Quote text alone cannot select a task or cross an identity boundary.
- Explicit named continuations use conservative token matching. Ambiguous matches use the existing native question channel before provider execution.
- Missing or ambiguous selection blocks mutating tools; bounded history lookup and clarification remain available.
- `/new` detaches focus while preserving explicit task records and anchors. An explicit old reply can still resume an older task.

### Operational checkpoints

The `task_checkpoint` tool stores task summary, blocker, next step, artifact references and lifecycle status. It is separate from the visible task board and permanent user memory, and is included in core tool discovery.

The host also captures bounded actual tool-return envelopes and pending/unknown outcomes independently of model summaries. Repeated checkpoint captures replace the same tool event rather than duplicating it. Artifact paths are references, not proof that a file was created. A tool success envelope is not proof that the overall task is complete. Unknown outcomes require checking actual state before replaying side effects.

Cancellation preserves existing checkpoint fields and captured evidence. Task-finalization failure cannot prevent release of turn controls. A normally completed turn whose task state cannot be finalized reports an explicit error instead of silently claiming a durable save.

When focus switches back to A, the provider receives A's checkpoint and the current turn rather than B's unrelated recent transcript. Full history remains on disk. This provider-payload filter does not delete or rewrite the transcript.

### Telegram associations

Incoming text and attachment admission carry native message and same-chat reply IDs as routing metadata. Successful outgoing narration, progress, final replies, file uploads, question cards and stop acknowledgements can be used as task anchors. A turn ticket pins the task ID; status messages created before task admission are queued for association rather than assigned to the previous task.

Associations require an actual successful Telegram message response. They are best-effort if local persistence becomes unavailable. Historical messages predating this feature, evicted anchors, inaccessible messages and external replies may have no mapping. These cases fall back to bounded retrieval/clarification, never a random prior task.

### Bounded history fallback

`recall_history(query="...")` returns discovery previews with stable `message_id` references. `recall_history(message_id=..., offset=..., limit=...)` reads a bounded page using SQL `substr` and `length`, with character offsets, total length, next offset and same-owner previous/next message IDs. This allows retrieving a nearby answer that does not repeat the query keyword.

Discovery combines bounded relevance and newest-match candidate pools and reserves the newest matching result, so a newer checkpoint cannot be excluded solely by many old keyword repetitions. Identical query/page requests in one active turn return an already-requested receipt instead of querying SQLite again. Different pages and queries remain accessible; the per-turn cache is reset for a new turn.

### Private session-recovery snapshots

The independent completed-turn recovery journal requires POSIX permission checks and directory fsync (including supported Linux/Termux/macOS filesystems). It is not a Windows ACL implementation. Unsupported platforms refuse snapshot creation before making a recovery directory or writing plaintext; ordinary healthy SQLite persistence remains available. A journal copied from another platform is not silently ignored, because it may contain newer side effects than SQLite. If both primary persistence and private recovery fail, the failure is reported explicitly rather than claiming a durable save. Native Windows private-journal support remains unavailable until ACL/privacy and durability validation are implemented.

### Limits and failure behavior

Defaults bound task records per identity, owner count, message anchors, event count, field sizes and rendered context. At task capacity, only the oldest explicitly completed/cancelled checkpoints and their anchors are eligible for eviction to admit new work. Parked, blocked and active work is never evicted; if all slots contain unfinished work, admission fails visibly until an existing task is completed/cancelled. Evicted task anchors return missing-anchor rather than selecting unrelated work. Conversation archive retention is independent and does not promise retention of every raw tool envelope. The store rejects symlink paths before creating directories, uses private database file permissions and redacts recognized/configured credentials in recorded text. This is not a filesystem sandbox or a guarantee of detecting arbitrary secret formats.

Checkpoints do not recover actions that happened before any checkpoint was written, prove an external side effect, or guarantee model instruction-following. No active provider configuration, persona, installation or gateway process is changed by these source edits.

## Verification

New tests use isolated temporary homes, real SQLite and actual tool dispatch. Provider responses and Telegram transport are controlled fixtures; this is not a live-provider or deployed-bot result.

The complete integration scenario exercises A reading a real fixture file, stop, B completion, transcript compaction, session reconstruction and reply-to-A resume. It verifies that only A's next file is read, A's previous read is not repeated, B stays completed, and B's transcript does not enter A's resumed provider payload. Other tests cover question-based disambiguation, `/new`, unresolved mutation prevention, upload/question/progress/stop anchors, owner isolation, concurrent writes, capacity refusal, privacy, symlinks, Unicode detail paging, latest-checkpoint discovery and no-progress recall caching.

Run the new unittest files with the application Python, separately if necessary for module isolation:

```sh
python -m unittest discover -s tests -p test_task_context.py -q
python -m unittest discover -s tests -p test_task_routing_integration.py -q
python -m unittest discover -s tests -p test_history_detail_recall.py -q
```

Source validation also covers existing agent, stop, persistence recovery, session transfer, execution budget and Telegram contracts. The broader local run exposed a separate branding-bundle assertion involving untracked `agentid-sniper` upstream assets. Those assets and that assertion were not changed as part of task continuity.

Activation requires a separate installation/restart decision. Live provider behavior and full CI/release readiness remain separate verification gates.
