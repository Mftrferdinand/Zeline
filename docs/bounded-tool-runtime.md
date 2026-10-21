# Bounded tool execution

Zeline's tool runtime applies resource limits **during execution**, rather than only formatting a small response after unbounded work has already happened.

## Execution boundaries

| Path | Contract |
| --- | --- |
| Content search | Native `rg` sorted streaming with a Python literal-search fallback. `limit` + zero-based match `offset`, explicit `next_offset` and `more_results` at page boundaries. The 50,000-byte response budget does not cap total scanned bytes, files or total matches. Hidden/dependency/binary/symlink exclusions and cooperative cancellation. |
| File windows | Fixed-size input reads and bounded rendered windows, one-based line `offset` + `limit`. Byte cursors resume inside oversized physical lines without losing UTF-8 characters. Invalid UTF-8 pages return an explicit error rather than a lossy cursor. No cumulative 2 MiB scan cap. Offset/limit do not require materializing the complete file or counting its remaining lines. |
| Foreground commands | Drain subprocess pipes in fixed-size chunks while retaining a bounded amount of output. Truncation is explicit. Cancellation/timeout tears down the command's isolated process group. |
| Background log inspection | Poll advances through 50,000-byte windows without discarding unread bytes, preserving UTF-8 boundaries. Log previews seek directly into a bounded tail rather than loading the whole log. |
| Interrupted command handoff | A foreground command deliberately promoted to a background job transfers output ownership without duplicate consumers. Retained spool history has a finite disk budget and explicitly marks any omitted history. |
| Read-only batches | Bounded in-flight submissions, periodic interrupt/deadline checks, completed evidence preserved before abort. A cancelled batch cannot accumulate unlimited abandoned workers. |
| Invocation controls | Cancellation, interruption reason, dispatch budget and telemetry belong to the originating invocation, not whichever turn happens to be active later. |
| Operational trace | Private, rotating start/end records exist independently of final transcript persistence and mutation provenance. No arguments, command bodies or output payloads are accepted by the trace API. |

Tool names and normal small-file/command behavior remain compatible. Search adds `limit`, `offset`, `target` and opt-in native `regex`; reads add `byte_offset`. Pagination assumes unchanged files and the same backend between pages. Search and read results explicitly identify page boundaries; a bounded preview must never imply the entire source was searched or retained when it was not.

## Cancellation and retained evidence

Python threads cannot be forcibly terminated safely. Local file work cooperatively checks cancellation; subprocesses have their own process-group lifecycle. A foreign callback may still ignore cancellation. The batch scheduler must bound such work across subsequent batches instead of spawning an unbounded replacement population.

Completed tool results are recorded before UI result callbacks and checkpoint callbacks. Returning from a cancelled batch must not discard known results or allow late worker completions to mutate a new turn's transcript. The default per-agent capacity is five active workers, retained across turns. A failing batch admits no further wave and gives already-admitted peers up to 250 ms to publish evidence; cancellation or the turn deadline shortens that grace. Unknown outcomes are explicitly repaired in the tool protocol rather than invented.

File reads retain/render at most 50,000 bytes, including metadata. Pagination totals are exact only at observed EOF or for a small bounded metadata tail. When `next_byte_offset` is present, continue with both `offset=next_offset` and `byte_offset=next_byte_offset`; this avoids rescanning earlier lines. Fully retained unwindowed text above 20,000 characters keeps the existing offload-pointer behavior. Search consumes only the requested match page plus lookahead, without retaining skipped matches; getting a later page may rescan earlier matches. Native regex search requires `rg`; the fallback is deliberately literal to avoid uninterruptible Python backtracking. Native matching-line previews are bounded and may be shortened by `rg`.

Compaction retains bounded goal, finding/decision/frontier and tool-evidence records, merges earlier digests without recursive nesting, and does not label interrupted work completed. Archive output is written message-by-message rather than constructing the entire discarded transcript in memory. Persistence keeps existing digest/task anchors and complete tool/result groups; oversized current output is explicitly shortened rather than leaving an orphan assistant tail. The checkpoint/provider-request integration test uses fixed offline provider responses: it verifies evidence transport, not a real model's decision never to re-read.

Foreground capture uses 8 KiB drains, a 50,000-byte head/tail window, and a 16 MiB disk-prefix spool. Once that history budget is exceeded, handoff explicitly marks missing history and replays a non-overlapping recent tail. After the direct child exits, the collector gives inherited output pipes 250 ms before abandoning descendant-only output. Promoted collector errors remain visible in background process status without exposing exception payloads.

## Diagnostics

`DATA_DIR/logs/runtime-trace.jsonl` records the tool name, random call ID, hashed owner, timestamps, duration, process/thread identifiers, and bounded local resource samples when supported. The active file rotates at approximately 1 MiB with two backups. Files are owner-only on POSIX. Trace failure cannot prevent tool execution. `events.db` remains a separate mutation-only provenance log.

A start without an end narrows the incident boundary; it **does not prove** Android low-memory killing, an OS phantom-process policy, a signal, or a particular command caused the process to disappear. RSS samples are boundary samples, not continuous peak telemetry.

## What this does not do

- It does not cap the CPU/RAM consumed by arbitrary user commands, compiler processes, browser engines, plugins or remote services.
- It does not impose a disk quota on explicitly launched background jobs after handoff. Their inspection is bounded, not their lifetime output volume.
- Cooperative deadlines cannot forcibly interrupt an OS filesystem operation that blocks inside the kernel; temporary spool writes and regular-file reads still depend on filesystem responsiveness.
- Windows-specific pipe behavior has compatibility tests but has not been exercised on native Windows in this Android environment.
- It does not introduce a watchdog, automatic gateway restart, infrastructure service, or OS configuration change.
- It does not prove the exact cause of a historical phone-wide freeze or application-group termination.
- Unit and bounded-fixture tests are not a claim that an unobserved production workload cannot exhaust device resources.

## Rollout

Validate both the checkout and the exact installed-package overlay. Preserve intentional local package extensions, rather than replacing the entire installed tree with a different checkout. Gateway activation is a separate operator-approved action; installing files does not authorize starting or restarting a service.
