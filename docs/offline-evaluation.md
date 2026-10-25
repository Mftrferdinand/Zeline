# Offline evaluation readiness

`zeline.offline_evaluation` provides a deterministic, bounded bridge from frozen
task cases to `ForgeStore.test_comparison`. It is a prerequisite for an optimizer,
not a GEPA implementation and not proof of general model improvement.

## Trust boundary

- The adapter is operator-authored Python and is pinned by full SHA-256. Every
  task starts from the same snapshotted bytes, so adapter self-modification cannot
  alter later baseline/candidate cases.
- Candidate `SKILL.md` text and case input are passed to the adapter as data. Zeline
  never executes scripts supplied by the candidate.
- Expected outputs remain in the host checker. The adapter receives only candidate
  text and the individual task input; it cannot submit verdict booleans.
- The subprocess uses an argument vector, never a shell string. It is isolated from
  the parent interpreter with `-I`, but it is **not an OS sandbox**. Only trusted
  adapters are allowed.

## Frozen receipt

`freeze_evaluation(...)` binds:

- proposal candidate and base revisions;
- canonical training/held-out manifests and per-input digests;
- trusted adapter digest;
- host equality-checker revision;
- exact paired case budget and timeout budgets.

Both partitions contain 1–16 unique stable case IDs. Identical canonical task inputs
across training and held-out are rejected even when IDs differ. A moved active skill,
candidate, manifest, checker, or adapter fails closed.

## Execution

```python
frozen = freeze_evaluation(
    store,
    proposal_id,
    manifest,
    adapter_path="/absolute/path/to/trusted_adapter.py",
    adapter_sha256="...",
    task_timeout=5.0,
    total_timeout=60.0,
)
report = evaluate(store, frozen)
```

Every baseline/candidate case runs in a fresh subprocess. The host compares canonical
JSON bytes (so `true`, `1`, and `1.0` remain distinct) against the hidden expected
value and passes exact boolean case maps to `ForgeStore.test_comparison`. The existing
held-out gain, no-regression, candidate/evidence CAS review, and explicit apply gates
remain authoritative. Evaluation never reviews, applies,
publishes, calls a provider, or searches for a candidate.

## Hard limits and honest limitations

- adapter source: 128 KiB;
- candidate: 256 KiB;
- canonical manifest: 256 KiB;
- worker stdout/stderr: 64 KiB each;
- task timeout: 0.05–30 seconds; total deadline: at most 300 seconds;
- maximum: 16 training plus 16 held-out cases, evaluated as exact pairs.

The trusted worker runs in a dedicated process group. stdout/stderr are drained through
bounded pipes and the group is killed/reaped on timeout or overflow; no portable
CPU/RAM/filesystem sandbox is claimed. A trusted adapter can still consume resources or access its process
environment. Provider/token budgets do not exist in this offline layer. A future GEPA
adapter must keep held-out labels outside its search loop, freeze optimizer/provider
configuration, retain every failed run, and require independent review plus explicit
promotion.
