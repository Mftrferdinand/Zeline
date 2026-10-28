# Quiet scheduled briefings

Quiet delivery is an opt-in addition to the existing scheduler, not a restoration
of the legacy proactive engine. It creates no daemon, automatic job, new memory
store, connector service, or model-routing policy.

## Create a job explicitly

```sh
zeline cron add '09:00' \
  'Read the selected project status and an explicitly supplied baseline. Report verified actionable changes. If the baseline or source is unavailable, report that limitation; do not claim there were no changes.' \
  --workdir /absolute/path/to/project \
  --profile safe \
  --quiet-if-unchanged \
  --deliver telegram:YOUR_CHAT_ID
```

Replace the workspace and delivery target with your own authorized values.
Use `--timezone Asia/Jakarta` if the schedule must use that timezone rather than
host-local time. Keep the provider/model default unless a per-job override is
explicitly wanted. The gateway must already be running for scheduled jobs to fire.

Manage only the returned job ID with `zeline cron show JOB_ID`,
`zeline cron pause JOB_ID`, or `zeline cron remove JOB_ID`.
There is no chat-slug ID or global "disable every briefing" operation.

## Delivery contract

- Default **off**: existing jobs and literal output keep their old behavior.
- With `--quiet-if-unchanged`, the scheduled agent is told to return exactly
  `__ZELINE_NO_UPDATE__` only when its evidence supports no meaningful update.
- Only that complete result, allowing surrounding whitespace, suppresses final
  text delivery. A substring, quoted marker, old `__SILENT__` marker, or error
  message is not suppressed.
- The raw quiet result remains in the normal local output audit. The run count,
  next scheduled run, and successful quiet status are recorded normally.
- A quiet control marker is **not** a successful evidence payload for
  `context_from`. Any previous output pointer is cleared; consumers fail closed
  rather than treating an old result or a control marker as fresh evidence.
- Script-only jobs cannot opt in: they already use empty stdout for silence.
  Execution exceptions and agent `ERROR` results still follow failure reporting.
- This controls **final text delivery only**, not notifications or files sent by
  tools during execution. Prefer the least-privileged suitable tool profile.

## Isolation and limitations

A quiet agent job uses a generation-scoped cron identity. It does not impersonate
the destination user or inherit that user's chat history or personal memory.
No global provider/model settings are changed.

This option does not itself compare files, discover updates, deduplicate messages,
or maintain a baseline. The task must supply authorized evidence and a baseline;
the agent's no-update decision remains an agent decision, not a deterministic
change detector. No unbounded background watcher or legacy `run_as` access is added.

The option is available through the Python scheduler API and `zeline cron add`.
The `schedule_task` tool schema is unchanged in this increment.
