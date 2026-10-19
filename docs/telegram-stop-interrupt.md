# Telegram stop and busy-message interruption

## Contract

- Admission happens synchronously before a reply worker starts. Every turn receives its own irreversible cancellation event; a waiting worker never clears another turn's event.
- A message received while work is active interrupts the current turn and joins a FIFO queue. The first acknowledgment in a burst is immediate, includes measured elapsed time and iteration, and uses:
  `⚡ Interrupting current task (11 min elapsed, iteration 18/150). I'll respond to your message shortly.`
- Acknowledgments are coalesced for 30 seconds per store/identity. Every admitted message is still queued; coalescing is not message loss.
- `/stop` invalidates current and admitted pending turns, cancels questions, and terminates owned foreground/background processes, including subagent identities. It does not shut down the gateway or affect another identity.
- A subsequent message is a fresh turn, with a fresh cancellation event. Stopped work never resumes merely because another message arrives.
- Soft interruption promotes a running foreground shell/Python command to a managed background job. Its real job ID and log path are recorded in tool history. A subsequent hard stop kills owned promoted jobs as well.
- Cancelled turns cannot send new narration, progress callbacks, attachments, or final replies. Already completed tool results and visible progress are preserved; stop is not rollback.
- Telegram question delivery registers a renderer before execution, not in a pre-execution callback that runs before the question exists.

## Engine and resource handling

Provider requests run in isolated IO handoff workers. Cancellation releases the agent/history owner even before headers arrive, during a stalled non-stream body, or during a stalled SSE read. Late responses are disposed; abandoned workers do not parse messages, update usage/history, or execute tools. Response closing is asynchronous because `requests.Response.close()` itself can block.

Streaming uses one bounded reader per response, not a thread per token. Cancellation gates exist before tool execution, after operator plugin hooks, before parallel dispatch, at subprocess registration, and during foreground process waits. Child agents inherit the parent cancellation predicate; a verifier is not started after cancellation. Completed tool results are appended before repairing unfinished tool-call protocol tails.

## Limits

- Closing a local HTTP request cannot prove that a remote provider stopped its server-side generation. A blocking request/stream reader may remain as a daemon until its transport timeout/peer release; it cannot continue the cancelled model/tool turn.
- Arbitrary plugin/native Python callbacks that are already executing cannot safely be force-killed as threads. They remain cooperative. Subprocesses are tracked separately and can be terminated.
- Remote mutations or file writes already committed cannot be undone by stop.
- Telegram API requests already in flight may arrive after cancellation. New callbacks/results are suppressed once the ticket is cancelled.
- Telegram media download/extraction is admitted before preprocessing and runs off the poll thread. A cancelled preparation cannot start a later model turn. The underlying abandoned download can finish locally; its result is not delivered or used after cancellation.
- Automatic reflection reserves a cancellable turn when idle, is visible in progress/stop, and never overtakes admitted user messages. Cancelled reflection summaries are suppressed.

## Offline verification

```sh
python -m unittest discover -s tests -p test_turn_admission.py -v
python -m unittest discover -s tests -p test_cancellation_core.py -v
python -m unittest discover -s tests -p test_ask_user.py -v
python -m unittest discover -s tests -q
python -m ruff check zeline tests
git diff --check
```

`test_turn_admission.py` covers pre-worker stop, FIFO despite reversed worker scheduling, stop clearing queued work, exact acknowledgment text, stale output suppression, subsequent fresh messages, reset, and reflection not overtaking admitted work. `test_cancellation_core.py` exercises real localhost stalled HTTP, stalled SSE, plugin/parallel cancellation gates, launch-registration races, real subprocess ownership, soft foreground promotion then hard stop, cancellation during question delivery, child inheritance, and retention of completed tool results. Tests only own fixture processes and temporary state; no production gateway restart or provider requests are required.
