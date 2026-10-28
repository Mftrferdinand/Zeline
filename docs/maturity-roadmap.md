# Zeline maturity roadmap

Status: local development, not a release declaration. Release publication requires
explicit operator approval. This roadmap does not authorize publishing, merging,
installation, gateway restarts, or model/provider configuration changes.

## Delivery rules

- Preserve the mixed working checkout; no mass restore, worktree, or unrelated staging.
- Work in bounded slices: failing contract, implementation, regression, independent review.
- Keep changes to the framework separate from Zeline App UI.
- Record source-bound measurements. Mechanism tests are not model-quality benchmarks.
- No automatic skill promotion, production learning experiments, or unbounded optimizer calls.

## 1. Evidence-backed learning

Existing slice: stable learning receipts, provenance, admitted memory revisions,
revision-aware assessments, evidence-linked lessons, and isolated paired skill checks.
The published learning-loop candidate remains separate from newer local changes.

Current local addition: `ForgeStore.test_comparison` can require explicit disjoint
training and held-out IDs, a planned case-verdict budget, exact evaluator coverage,
and at least one held-out gain. A training-only gain cannot pass this held-out gate.
The candidate must still pass all cases without regressions. Evidence is persisted
through the existing comparison CAS; review/apply remain separate operator APIs.
Legacy callers without a split retain paired-fixture behavior, not a held-out claim.

Hard-cap rejection now also has direct regression coverage for single and batch
admission under both character and fact limits. These tests verify unchanged user
facts and historical revision rows after rejection; they are coverage of the
existing transaction guards, not a new memory implementation.

Remaining acceptance work:

- Frozen train/held-out dataset manifests with input digests; disjoint IDs alone do
  not establish disjoint underlying tasks or prevent optimizer exposure to held-out labels.
- Repeated fresh-session/provider runs using matched budgets and all failed runs retained.
- Reject broad improvement claims without held-out task outcomes and regression coverage.

## 2. Native reverse engineering and REA

The development checkout already contains native static source/binary analysis,
reference and symbol navigation, and instruction decoding. The operator-facing
entry is `reverse_engineer`; external REA MCP is not required for this default path.
Read `reverse-engineering.md` for actual format/action support and parser limits.

Current verification covers the native tool, source analysis, real fixtures, and
integration-level disassembly. This is not full REA catalog parity. Optional
`expected_sha256` now binds file and ZIP continuation pages to the same captured
artifact bytes through the public schema and ToolExecutor; directories reject this
claim because they have no recursive content digest. Local scoped checks also passed
for continuation/tool/engine/disassembly/decoder/real fixtures (63 tests, 38
subtests) and source/disassembly integration/import references/tables/decoder
(46 tests, 86 subtests); these are not cross-platform CI results.

Next gates:

1. Preserve `expected_sha256` across every caller-driven page. It detects artifact
   replacement before parser/decoder work, but does not prove complete scan coverage
   or preserve the caller's ISA/selector choices; those remain explicit page inputs.
2. Reconcile docs, tool-schema actions, dispatch and actual parser results. Extend
   malformed/truncated input and bounded subprocess/output coverage as needed.
3. Verify deterministic resource-limit/cancellation handling and temporary-file cleanup.
4. Keep external REA MCP optional; test anonymous/local fixtures before any live integration.
5. Preserve read-only behavior; analysis does not authorize executing a supplied binary.

## 3. GEPA: experimental training bridge; host gates still pending

The held-out SkillForge gate is a prerequisite, **not GEPA generalization**.
An opt-in `zeline.gepa_training.search_training` bridge now exercises the
installed optimizer using only training cases and fixed operator-supplied
proposals. It returns a report, not a promoted candidate; no provider route,
model weights or production prompts change. See `docs/gepa-training.md`.

Remaining gates:

1. The local offline evaluation-readiness harness now freezes canonical train/held-out
   task manifests, rejects duplicated underlying inputs across partitions, pins the
   proposal/base, trusted adapter and host checker digests, runs fresh bounded workers,
   and feeds host-derived verdicts into the held-out SkillForge gate. It performs no
   optimizer search, provider calls, review, apply or publication.
2. The opt-in bridge uses the verified local `gepa==0.1.4` API without
   exposing any held-out cases to search. It is not enabled on the default
   agent path; package installation is optional and no provider is invoked.
3. The optional `search_training_bounded` entrypoint now applies a parent-side
   timeout and output-byte cap on this Unix/Android host. The forked worker
   runs in its own process group, killed and reaped on timeout **and** after
   report delivery. Host-side callback counters still reject metric/proposal
   overshoot (a local `gepa==0.1.4` smoke observed 8 calls with its own
   `max_metric_calls=6`). This is **not** a sandbox: detached sessions,
   CPU/RAM/filesystem/network access and provider tokens remain unbounded.
   Provider-backed search needs accounting and containment before promotion.
4. An opt-in `evaluate_training_search` handoff now stages the sole strict
   training winner and calls the host-owned frozen offline evaluator. GEPA is
   passed only training IDs/inputs; the host compares baseline and candidate
   on train plus held-out through SkillForge's evidence CAS. A forked trusted
   checker still shares host memory, so this is not isolation from malicious
   Python callbacks. No automatic review, apply, or production hot-path link.
5. Next: bind each report to an exact dataset/model/tool configuration and
   budget receipt, then run repeated fresh-session, source-bound cases; a
   local synthetic held-out gain is not proof of broader model quality.
6. Require independent review and explicit promotion. Preserve a verified
   rollback revision; no optimization result applies itself or publishes a release.

## Held-out gate usage (trusted host API)

The evaluator is operator-trusted Python, never a shell command extracted from a
candidate. It receives separate disposable baseline/candidate skill trees and
returns boolean task verdicts keyed by the exact declared case IDs.

```python
checks = store.test_comparison(
    proposal_id,
    evaluator=trusted_task_verifier,
    training_case_ids=("train:case-a",),
    held_out_case_ids=("heldout:case-b",),
    max_case_evaluations=4,  # Two declared cases, each on baseline and candidate.
)
# checks alone never applies a candidate. Explicit review/apply remain separate.
```

Both partitions must be nonempty, bounded and disjoint. Planned evaluations must
fit the strict integer budget. Training-only gains, held-out regressions, missing
IDs, malformed verdicts, modified staged trees, and moved source revisions must not
produce approvable held-out evidence. Legacy unpartitioned comparisons remain a
separate compatibility path, not a way to claim held-out validation.

## Release gate

A future release needs the exact intended source set, independent source-bound
review, full CI on that exact candidate, packaged-artifact parity, migration and
rollback checks, and explicit operator release approval. Earlier green CI does not
cover newer local edits. Passing a narrow suite does not authorize deploying it.
