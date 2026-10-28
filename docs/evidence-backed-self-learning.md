# Evidence-backed self-learning

Zeline learns here by retaining scoped experience and changing future context or reviewed procedures. This is **not model-weight training**, and a receipt is not proof of better answers.

## What changed

- Reflection eagerly exposes only its five permitted native schemas: `manage_skill`, `load_skill`, `list_memory`, `add_memory`, and `resolve_lesson`. The normal lazy tool index is unchanged. Profile and operator disables still apply. Reflection cannot depend on `tool_search`, which it is forbidden to execute.
- Each real memory/proposal/resolution mutation returns a versioned receipt with its actual record ID and revision. Duplicate memory, reused proposals, and unchanged skill patches do not announce new learning. Skill proposals remain inactive until testing and explicit operator review/apply.
- A single explicit correction establishing a reusable requirement or stable project convention can qualify for reflection memory; repeated failures are not required. Disposable outputs and credentials do not qualify. Reflection memory retains the existing low-confidence and expiry policy.

## Memory provenance and feedback

`MemoryStore.add_receipt(...)` derives created/duplicate/failed status inside the same transaction as the write. Its review origin identifies the review turn, not an independently verified source message.

A turn records selected memory IDs/revisions with `record_retrieval`. A completed answer is recorded only as operational completion. It does not establish actual use or task acceptance. Records are scoped to the hashed identity and bounded to 500 turns per identity; audit operations have a short busy timeout and must not block the user's answer.

`assess_retrieval(turn_id, record_id, revision, checker=..., case_id=..., baseline=..., candidate=..., used=...)` is a **trusted host-checker API**, not a model tool. A lone positive assessment is now shown by `/self` as `assessed`, not as a verified gain. `zeline.memory_gain_checker.evaluate_memory_gain` is the explicit paired host path: provide bounded host-owned cases with disjoint train/heldout prompts, expected answers, a separate fresh-session baseline runner without the new memory, and a candidate runner with it. The candidate must return its actual answer and turn ID; every turn must show the exact owner/revision retrieval. The checker first runs all baselines, then all candidates; only if all candidate answers pass, none regress and at least one held-out case improves does it attach a qualified held-out assessment. The assessment now persists bounded per-case verdicts, candidate retrieval turn IDs, and digests of prompts/expected/actual answers (not raw answer text); the store recomputes counts and checks each candidate retrieval before committing. A digest records provenance within this trusted host; it does not independently prove runner isolation or a human-approved oracle. `/self` then reports bounded paired and held-out gains for that memory revision. A normal-turn `add_memory` write, including an atomic batch correction, projects its real changed live revision into the identity-scoped journal; a duplicate/removal does not create a gain. No checker runs on every ordinary chat turn: only an explicitly registered, exact-fact case pack can enqueue a bounded automatic evaluation after a memory mutation. Caller-provided runners/expected answers remain trusted host code—not independently certified model behavior. A direct single-case `assess_retrieval(..., comparison=...)` cannot issue a qualified held-out claim: only the paired evaluator's internal capability can attach comparison counts. Distinct runner functions alone do not prove fresh sessions or baseline memory absence; the host harness must enforce those with separate processes and isolated storage. `/self` keeps the latest pointer's stage and reports separate bounded, canonical-store-checked active-history counts; stale/removed records are not counted as active gain. Do not infer application from an assistant claim, a retrieved record alone, or a successful shell exit. Prefer a digest-pinned checker and real fresh processes for baseline/candidate.

- Evidence is attached to the exact retrieved historical revision.
- Repeating the same assessment is idempotent; different evidence cannot overwrite it.
- Only a live, unchanged reflection record can have its confidence adjusted. Operator facts and intervening revisions are not rewritten.
- Confidence is a bounded ranking heuristic, not a calibrated probability. Observed gains strengthen it; regressions weaken it. It participates in recall tie-breaking.
- The next review receives measured feedback joined to the original historical memory text, rather than presenting a later revision as the original experience.

## Opt-in automatic paired evaluation

The gateway starts one bounded evaluation loop **only when** a trusted local `~/.zeline/learning/case-packs.json` exists. Register exact target facts in advance, not from a model response. For each pack provide `name`, `fact_sha256` (SHA-256 of the exact UTF-8 canonical fact), `worker` set to `zeline.memory_gain_automation:llm_answer`, and 2–6 cases with unique `id`, `prompt`, `expected`, and `split` (`train` and `heldout` both required). Root document: `{"version":1,"packs":[...]}`; at most eight packs and a 32 KiB file. Keep case files private (`chmod 600 ~/.zeline/learning/case-packs.json`); a group/world-readable pack is ignored. Never include credentials in facts, prompts or expected answers. No pack means no evaluation thread, queue or extra model request.

A changed live revision whose exact fact hash matches a registered pack is enqueued after the memory receipt; a duplicate is not. One job is drained per 60-second tick (max eight pending, 45 seconds per child and 240 seconds per job). On restart, running jobs are recovered under a single-process lock. Each baseline/candidate invocation forks into a fresh temporary home and SQLite snapshot. Baseline snapshots delete the target record/revisions; candidate must actually retrieve that exact record and revision. The model worker calls Zeline's configured provider with `use_tools=False`; network is enabled only if the host has an API key. The no-network default in the underlying evaluator is a Python socket guard, **not an OS sandbox**. Only reviewed host worker code is trusted. Provider calls consume tokens; the host must budget and review case packs before enabling them.

The queue does **not** write assessments into the owner's live memory database. `/self` shows opt-in sandbox job totals separately from canonical owner-memory status, with a bounded held-out count only after case evidence recomputes and the owner revision remains active. Queue totals are evidence on those registered cases, **not proof of better owner answers or general model learning**. No owner case pack is created by default.

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

## Three-self status and chat receipts

Telegram reflection receipts use compact action-only text (for example, `📗 Self-refinement review: Memory saved.`); raw facts, IDs, revisions and journal paths stay in private canonical records and the read-back-verified Markdown journal, not the chat bubble. Existing skills can receive bounded, unique, read-back-verified active patches to SKILL.md and existing Markdown references; each file is named in its 📘 receipt (`Patched references/runbook.md ... (1 replacement).`). Reference anchors are read with scoped `load_skill(name, file_path=...)`; missing, unsafe, symlinked or non-Markdown companions are not autonomously created or reported as applied. This does not promote an inactive proposal or imply a verified task benefit.

Owner-only Telegram `/self` reads the latest identity-scoped entry **and bounded active canonical history** in each of Self-Enhancement.md, Self-Improvement.md and Self-Refinement.md, checking record IDs and revisions against canonical SkillForge, skill files, memory SQLite, or linked lesson evidence. The canonical-history aggregator uses bounded read-only SQLite connections; a journal alone cannot certify an active proposal or a measured result. Pending proposals remain inactive. Skill loads and memory recalls are operational observations, not task-quality proof. Only memory assessments from the trusted host checker (with `used=true` and paired outcomes) count as verified live gain/regression; linked lesson success proves tool execution only. SkillForge proposals may additionally display bounded paired candidate gain counts and declared held-out gains after `test_comparison` passes; these are offline fixture results, never live task improvement or permission to auto-apply. Failure/regression suggests manual review, never automatic rollback. An expired or changed revision cannot be presented as the current one. Empty/unavailable statuses are distinct. Installed behavior still requires verification against its own runtime package and gateway after deployment; checkout tests alone do not prove delivery to Telegram.

## Verification and limits

Regression coverage includes duplicate/no-op receipts, durable fresh-process recall, cross-identity isolation, immutable assessment provenance, lower recall priority after measured regression, explicit success linkage, long-signature collisions, ambiguous selectors, linked-evidence retention, paired skill contracts, review/apply and scoped post-apply observations.

A bounded real-provider replay also exercised a private synthetic project convention: unknown baseline answer, actual local-validator feedback, model-selected reflection memory write, fresh agent session without the previous transcript, recalled correction, and a checked answer. Earlier unsuccessful runs are retained alongside the successful run. This narrow episodic result is not a general learning benchmark or proof of autonomous task improvement in production.

All development probes use disposable account state. Source changes do not update an installed runtime. Publishing, installing and restarting require separate authorization. Independent review and remote CI are distinct gates; local test success does not substitute for them.
