# Opt-in GEPA training search (experimental)

`zeline.gepa_training.search_training` is a **training-only, report-only** bridge
for an optional installed `gepa` package (locally exercised with `0.1.4`).
Importing Zeline does not load GEPA or change the active agent. There is no
provider/model invocation by this bridge: proposals are operator-supplied strings.

Use `search_training_bounded(...)` for runs that need a hard parent-side wall
deadline and bounded JSON report bytes. On this Unix/Android host it forks a
separate process group; a hanging checker and its ordinary descendants are
killed and the worker reaped at timeout. `search_training(...)` remains an
in-process primitive for trusted local tests, with only cooperative timeout.
Both enforce the per-case metric and proposal caps.

```python
from zeline.gepa_training import search_training_bounded

report = search_training_bounded(
    seed_text="initial candidate",
    training=[{"id": "train-1", "input": {"case": "example"}}],
    score_case=lambda text, case: text == "candidate A",  # trusted training checker
    proposals=["candidate A"],
    max_metric_calls=16,
    max_proposals=1,
    timeout=5,
    max_output_bytes=65536,
)
# Inspect report['candidates']; do not promote solely on training scores.
```

Only `training` is given to GEPA for its trainset and valset; this valset is
**not held-out**. `search_training_bounded` returns training scores and a
deterministic winning index (first index wins ties). These scores are not
held-out evidence. For the opt-in host handoff use
`zeline.gepa_host_evaluation.evaluate_training_search(...)`: it requires a seed
matching the active skill and its entire companion-tree digest before **and**
after search, validates the disjoint train/held-out manifest and pinned adapter,
runs bounded training with only training IDs/inputs, then stages **one** strict
training winner through `ForgeStore.propose`. It verifies the proposal's base,
scope, and source-tree digest against the pre-search snapshot. The separate
`offline_evaluation.freeze_evaluation` / `evaluate` path checks baseline and
candidate on the pinned manifest and records paired held-out evidence. A held-out
failure remains a failed comparison; no review or apply happens. This handoff
writes a pending/tested **proposal record only**, never the active skill. The
optimizer's fork shares trusted host memory, so the API separation is not a
security isolation boundary against a malicious callback; never claim labels
are inaccessible to arbitrary operator Python code.

GEPA's `max_metric_calls` is cooperative (a local smoke observed 8 with a limit
of 6). The bridge counts per-case checker calls and refuses to return a report
if GEPA tries to exceed the host limit, including swallowed engine errors.
The proposal count is enforced similarly; inputs and candidate lengths are
bounded. The in-process primitive's timeout is cooperative. The bounded
entrypoint has a parent-side wall deadline and kills its worker process group
on timeout; it also rejects oversized result bytes. It is Unix/fork-specific,
not a sandbox: detached child sessions, filesystem/network use, RAM and CPU
resource ceilings, provider calls and tokens are **not** contained or metered.
Never run untrusted callbacks. Before provider-backed optimization, add strict
provider/token accounting and isolated data provenance; do not claim production
readiness from this training-only bridge. `gepa==0.1.4` remains optional via
`pip install '.[optimizer]'`; the default CI suite skips optimizer tests if it
is unavailable, and the Ubuntu `gepa-opt-in` job installs that extra and runs
both GEPA bridge and host-handoff tests. Unix/fork tests skip on Windows. A local
run does not prove the remote matrix has passed.
