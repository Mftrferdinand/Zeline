# Richer local cron execution

## Scope

This increment extends the existing recurring scheduler on the v0.3.4-derived
`feat/runtime-maturity` worktree. It does not install anything, run a live job,
change global model/provider configuration, restart services, or publish a release.
No new service or third-party dependency is required.

## Script-only jobs

```bash
zeline cron add 'every 1h' --script /absolute/path/check.py --no-agent \
  --workdir /absolute/project --timeout 60 --deliver local
```

- `script` must resolve to an existing `.py`, `.sh`, or `.bash` file. Python uses
  the running interpreter. POSIX shell scripts require `bash` on PATH; Windows
  prefers native Git for Windows Bash (installed Git location or PATH), never
  a `System32`/`WindowsApps` WSL launcher. Script paths are separate argv entries,
  not shell fragments. Shell sources should use LF; Python stdout retains the
  platform's original newline bytes (including CRLF on Windows).
- A relative script requires an explicit absolute workdir. Workdir must exist.
- `--no-agent` never constructs or calls an agent/model. Successful stdout is
  delivered verbatim; exactly empty stdout is saved but not sent to a channel.
- Stdin is closed. Each pipe has a 64,000-byte memory cap. Overflow is an error,
  not a silently truncated success. Nonzero exit and timeout are saved and an
  error alert is attempted. Delivery transport failures remain observable.
- `--timeout` is a positive finite script deadline, at most 3,600 seconds;
  cleanup may take additional time. It is not a hard deadline for an agent turn.
- Shutdown checks prevent starting a cancelled script and interrupt owned running
  scripts. POSIX execution owns a new process group and kills it on interruption.
  Windows uses the existing best-effort process-tree termination helper. Real
  Windows kernel/process behavior was not exercised on the Android host.
- Scripts are trusted local code and inherit the host environment. This is NOT an
  OS sandbox; escaping process groups is outside the cleanup guarantee.

## Agent jobs

```bash
zeline cron add 'daily 09:00' 'Write the finished daily report.' \
  --script /absolute/project/collect.py --workdir /absolute/project \
  --model configured-model-id --profile workspace --skill report-procedure \
  --context-from job1 --deliver local
```

The model ID selects a model on the already configured provider. Provider URLs,
keys and global defaults are unchanged. The native `runtime_info` tool reports
this instance's effective model. Profiles are `safe`, `workspace`, or `full`.

An optional script runs before constructing the agent. Its stdout and explicit
upstream reports are encoded under `cron_input_data`, with `<` escaped, and are
labelled untrusted evidence rather than instructions. A failed preprocessor
prevents a model invocation. Combined serialized input is limited to 24,000
characters; oversized input fails instead of being silently clipped.

Up to five unique skill names are loaded at execution time, before the turn;
missing/inaccessible skills or a combined skill body over 64,000 characters fail
before the model. Private skills are available only to the full profile. Skills
are explicit trusted procedures, not untrusted result data.

Jobs using new agent options own fresh agent turns and generation-scoped
identities, avoiding reused job-ID/session histories. Their MCP/browser/LSP
cleanup is attempted in a finally block. Legacy agent-only jobs retain their
existing SessionStore behavior. Script-only jobs reject agent-only options.

The native `schedule_task` tool exposes the same optional fields (`script`,
`no_agent`, `workdir`, `timeout`, `model`, `profile`, `skills`, `context_from`).
It remains full-profile-only. `show` displays effective job options. Existing
create/list/show/pause/resume/run/remove actions and simple schedules remain.

## Chaining is a snapshot, not dependency scheduling

`context_from` accepts up to three distinct existing job IDs. Creation captures
producer generations. At execution, the consumer reads each producer's latest
successfully recorded report, not a running partial result. It does not trigger,
wait for, retry or acknowledge upstream work. If the latest completed run failed,
a previous success is not silently substituted.

Each report must remain inside the private cron output directory, fit within
64,000 bytes and match its recorded SHA-256. Missing/pruned/tampered reports,
missing producers or reused IDs fail before a model call. The existing output
retention limit remains; archive externally before relying on old reports.

This is separate from the durable `delegate_task` DAG and does not provide its
leases, uncertain-outcome recovery, step resume, verifiers or exactly-once side
effects. Multiple updated writers serialize JSON read/modify/replace through a
reentrant SQLite mutex; SQLite is the mutex, not the job data store. Old binaries
ignore that mutex and must not concurrently write this state after deployment.
Malformed/unsupported source bytes are preserved in private checksum-named
`jobs.recovery-*.json` files before recovery writes replace the job JSON. These
are recovery evidence, not an automatic restore or a live mirror. A metadata
write failure releases the running slot and sets the scheduler's error diagnostic.

## Execution ownership and recovery

Each running job holds an execution-lifetime OS file lock (POSIX `flock`,
Windows nonblocking byte-range lock). Ownership does not expire while a slow
agent is still running. Process exit releases the OS lock; only the exact owner
token may release the in-process claim. Claim lock files remain on disk so a
second inode cannot bypass a live lock. Run only updated scheduler binaries
against this state; older lease-based runners do not participate in this lock.

Before starting a worker, the scheduler re-reads its due snapshot under the jobs
mutex and rejects removed, replaced, paused, rescheduled or already-completed
work. Failed thread startup releases both the claim and local busy state. These
checks prevent overlapping cooperating runners; they do not guarantee exactly-once
external effects after a process crash.

Recovery backups are staged in unique temporary files, flushed and synced before
atomic replacement. An abandoned temporary file cannot block the next recovery.
Agent `ERROR` envelopes are recorded as failed results and cannot supply successful
`context_from` input; a successful script's literal stdout is not classified as an
agent failure merely because it begins with `ERROR`.

## Verification and deployment boundary

Regression coverage includes real Python/bash processes, owned child cleanup,
empty/nonzero/oversized output, CLI and native-tool options, input/skill scope,
model-locality, generation/checksum rejection, metadata failure and competing
writer processes. Model turns and channel deliveries use explicit fixtures;
passing tests are not proof of live provider access or Telegram delivery.

The frozen-source test log, wheel smoke, patch-apply proof and source checksums
are shipped with the development bundle. An independent reviewer verdict is not
available. No production approval, installation, live migration, restart,
commit, push or PR is implied. Fail-closed policy hooks, OS isolation, semantic
memory/privacy purge and GEPA remain separate work.

## Follow-up: explicit timezones and stale-worker protection

Use an IANA timezone when the operator's clock differs from the server:

```bash
zeline cron add '09:00' 'Write the daily report.' --timezone Asia/Jakarta
```

The native `schedule_task` tool accepts the same `timezone` field. The zone is
persisted per job and used for daily scheduling, resume, and displayed timestamps.
Omitting it preserves the host-local behavior of existing jobs. Fixed intervals
remain elapsed durations: `1d` means 24 hours, not the next local calendar day.
Windows installs include `tzdata`; on minimal POSIX hosts without an IANA database,
install system timezone data or the Python `tzdata` package. Invalid or unavailable
zones fail before job creation. No fallback silently changes the requested zone.

For explicit zones, a nonexistent daily time during a spring-forward transition
is skipped. An ambiguous fall-back time fires on its first occurrence only, once
per local calendar day. Five-field cron expressions are not supported by this
parser; use intervals or daily times.

Completion and overlap-skip metadata writes now check the immutable generation
of the scheduled job. Deleting a running job and creating another with its old
ID cannot attach the old result, counters, or next-run time to the replacement.
This fence protects metadata; deleting a job does not revoke an already-running
external action or guarantee that an in-flight delivery is cancelled.
