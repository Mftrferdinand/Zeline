---
name: zeline-eval
description: "Evaluate AI agent QUALITY — correctness, task completion, tool-call accuracy, and an overall quality score. Use to judge whether an agent's answers/actions are good. This is quality, NOT speed — for latency/tokens/cost/memory/concurrency use zeline-bench."
version: 1.0.0
---

# Zeline-Eval: Agent Quality Evaluation

Judge how *good* an agent's output is, distinct from how *fast/cheap* it runs.

- **Zeline-Eval = quality**: correctness, task completion, tool accuracy,
  quality score.
- **Zeline-Bench = performance**: latency, tokens/second, cost, tool overhead,
  memory, concurrency.

Never merge them. An eval result must not carry latency percentiles; a benchmark
result must not carry a `quality_score`.

## When to use

- "Did the agent actually answer correctly / finish the task?"
- "Did it call the right tools with the right arguments?"
- Regression-guarding answer quality across prompt/model changes.

## Dimensions

- **correctness** — is the answer factually/semantically right against the
  expected result.
- **task_completion** — did it accomplish the full requested task, not a
  fragment.
- **tool_accuracy** — were the right tools invoked with valid arguments, no
  spurious or missing calls.
- **quality_score** — an overall aggregate combining the above (document the
  weighting; keep it deterministic).

## Design rules

- **Independence contract.** The evaluator must not share code paths or hidden
  state with the agent under test; scoring runs in isolation so a bug in the
  agent cannot silently pass its own eval.
- **Deterministic offline reference.** Ship a MockAdapter and a versioned result
  schema so tests run offline and results are reproducible.
- **Redaction.** Never persist raw credentials or raw provider errors into eval
  output; store sanitized categories only.
- **Visible framework integration.** Show how to wire a real agent in, not only
  the mock.
- **Match CI locally.** Run the same lint + tests locally that CI runs, before
  pushing.

## Result schema

Every eval result carries a `schema_version` and validates against a strict
schema: per-case scores in their valid ranges, an aggregate `quality_score`,
the expected-vs-actual record, and pass/fail. Evolve additively.

## Workflow (TDD)

1. Write failing tests for each dimension's scoring, the aggregate quality
   score, and schema validation of a single case and a suite.
2. Implement scorers, adapters (offline mock + callable), the CLI, and schema.
3. Lint + tests to green. Generate a real eval artifact and validate it against
   the schema.
4. If published with CI, watch it to success and verify the live artifact.

## Pitfalls

- Do not put latency/cost/memory in a quality result — that belongs to
  zeline-bench.
- Do not let the evaluator import the agent's mutable state.
- Do not fabricate scores; every number must come from a real scored case.
