# Evidence-backed self-learning

Zeline learns here by retaining scoped experience and changing future context or reviewed procedures. This is **not model-weight training**, and a receipt is not proof of better answers.

## What changed

- Reflection eagerly exposes only its five permitted native schemas: `manage_skill`, `load_skill`, `list_memory`, `add_memory`, and `resolve_lesson`. The normal lazy tool index is unchanged. Profile and operator disables still apply. Reflection cannot depend on `tool_search`, which it is forbidden to execute.
- Each real memory/proposal/resolution mutation returns a versioned receipt with its actual record ID and revision. Duplicate memory, reused proposals, and unchanged skill patches do not announce new learning. Skill proposals remain inactive until testing and explicit operator review/apply.
- A single explicit correction establishing a reusable requirement or stable project convention can qualify for reflection memory; repeated failures are not required. Disposable outputs and credentials do not qualify. Reflection memory retains the existing low-confidence and expiry policy.

## Memory provenance and feedback

`MemoryStore.add_receipt(...)` derives created/duplicate/failed status inside the same transaction as the write. Its review origin identifies the review turn, not an independently verified source message.

A turn records selected memory IDs/revisions with `record_retrieval`. A completed answer is recorded only as operational completion. It does not establish actual use or task acceptance. Records are scoped to the hashed identity and bounded to 500 turns per identity; audit operations have a short busy timeout and must not block the user's answer.

`assess_retrieval(turn_id, record_id, revision, checker=..., case_id=..., baseline=..., candidate=..., used=...)` is a **trusted host-checker API**, not a model tool. Supply actual paired task verdicts and an independently established application signal; do not infer these from an assistant's claim or a successful shell exit. Prefer a versioned or digest-pinned checker. The API trusts its caller and cannot certify the honesty of arbitrary extension code.

- Evidence is attached to the exact retrieved historical revision.
- Repeating the same assessment is idempotent; different evidence cannot overwrite it.
- Only a live, unchanged reflection record can have its confidence adjusted. Operator facts and intervening revisions are not rewritten.
- Confidence is a bounded ranking heuristic, not a calibrated probability. Observed gains strengthen it; regressions weaken it. It participates in recall tie-breaking.
- The next review receives measured feedback joined to the original historical memory text, rather than presenting a later revision as the original experience.

## Tool-success evidence and lessons

The executor classifies actual tool outcomes before capturing success evidence. `log_success` is the single capture route; do not also call `record_success` for the same invocation. Events retain a hashed identity, tool, canonical full-call fingerprint, timestamp and fingerprint-only signature, not raw arguments.

Explicit `resolve_lesson` requires `evidence_id` for a later successful event from the same identity and tool. Use `args_sig_contains="lesson:<id>"` from the review context, or a uniquely matching legacy substring. Ambiguous selectors fail closed. Different successful strategies may be linked explicitly; automatic retry resolution requires an exact full-call fingerprint, never a truncated display signature.

A linked tool success establishes **an observed operational success**, not causality or task correctness. Prompt guidance preserves this distinction. Historical rows without linked evidence remain `legacy/unlinked`; migration does not manufacture proof. Up to 1,000 recent unlinked success events per identity are retained, plus events referenced by the bounded lesson rows.

## Skill comparison and review

`ForgeStore.propose_edit_receipt(...)` reports the actual atomic insertion outcome. `propose_edit(...)` remains a text facade. `test_comparison(proposal_id, evaluator=trusted_callable)`:

1. Stages separate baseline and candidate trees; never runs commands taken from a proposal.
2. Runs the same trusted evaluator against both snapshots.
3. Requires matching stable case IDs and exact boolean verdicts: `{"cases": {"case-id": true}}`.
4. Rejects evaluator mutation, active-source movement, mismatched cases, regressions and no-gain comparisons for improvement-qualified approval.
5. Publishes the complete comparison with compare-and-swap. A concurrent review cannot approve a temporarily passing, half-published comparison.

An improvement-qualified comparison requires a gain, no regressions, and all candidate cases passing. It establishes benefit only on those cases. Ordinary `test(...)` remains available for compatible operator-tested workflows, but explicitly does not claim measured improvement. Review/apply integrity and risk acknowledgement gates remain in place.

`proposal_outcomes(proposal_id, identity=...)` separately reports post-apply operational observations for that exact private revision and identity, including unknown and cancelled results. It never reports causal improvement from usage counters.

## Verification and limits

Regression coverage includes duplicate/no-op receipts, durable fresh-process recall, cross-identity isolation, immutable assessment provenance, lower recall priority after measured regression, explicit success linkage, long-signature collisions, ambiguous selectors, linked-evidence retention, paired skill contracts, review/apply and scoped post-apply observations.

A bounded real-provider replay also exercised a private synthetic project convention: unknown baseline answer, actual local-validator feedback, model-selected reflection memory write, fresh agent session without the previous transcript, recalled correction, and a checked answer. Earlier unsuccessful runs are retained alongside the successful run. This narrow episodic result is not a general learning benchmark or proof of autonomous task improvement in production.

All development probes use disposable account state. Source changes do not update an installed runtime. Publishing, installing and restarting require separate authorization. Independent review and remote CI are distinct gates; local test success does not substitute for them.
