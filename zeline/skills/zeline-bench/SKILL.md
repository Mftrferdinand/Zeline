---
name: zeline-bench
description: "Benchmark AI agent PERFORMANCE — latency, tokens/second, cost, tool-call overhead, memory, and concurrency (1/5/10 agents) with p50/p95/p99 latency. Use to measure how fast/cheap an agent runs or to load-test a deployment. This is performance, NOT quality — for correctness/task-completion/tool-accuracy scoring use zeline-eval."
version: 1.0.0
---

# Zeline-Bench: Agent Performance Benchmarking

Measure how an agent *performs*, distinct from how *good* its answers are.

- **Zeline-Bench = performance**: latency, tokens/second, cost, tool-call
  overhead, peak memory, throughput, concurrency, percentiles.
- **Zeline-Eval = quality**: correctness, task completion, tool accuracy,
  quality score.

Keep the two apart. A benchmark result must never carry a `quality_score`, and
an eval result must never carry latency percentiles. Mixing them produces
meaningless composite numbers.

## When to use

- "How fast / how cheap is this agent?"
- "What is p95 latency under 10 concurrent agents?"
- Regression-guarding performance before a release.
- Comparing two providers/models on the same prompt.

## Metrics

Per run: `latency_ms`, `input_tokens`, `output_tokens`, `total_tokens`,
`tokens_per_second`, `cost_usd`, `tool_calls`, `tool_duration_ms`,
`tool_call_overhead_ms`, `peak_memory_mb`.

Per aggregate/summary: means of the above, plus `latency_p50_ms`,
`latency_p95_ms`, `latency_p99_ms`, `wall_time_ms`, `requests_per_second`,
`successful_requests_per_second`, `total_requests`, `successful_requests`,
`failed_requests`, `failure_rate`.

## Concurrency load testing

Run the same prompt at concurrency levels (e.g. 1, 5, 10). `iterations` is per
agent, so level N with I iterations issues N×I requests. Report one performance
profile per level so you can see how latency/throughput degrade under load.

- Percentiles use **nearest-rank** and count only successful requests.
- A failed request never aborts the load profile — isolate it and record its
  sanitized error category.

## Design rules (learned)

- **Thread-safe adapters only for concurrency.** Sharing one adapter instance
  across worker threads is unsafe for stateful clients. Require an adapter that
  explicitly declares itself concurrency-safe, or pass a per-worker
  `adapter_factory()` so each thread builds its own instance.
- **Measure memory without global tracing state.** Reading a per-process peak
  under a global tracer serializes threads and corrupts the concurrency you are
  trying to measure. Use a per-process RSS read that does not gate parallelism.
- **Small costs must not round to zero.** Cost means need higher precision than
  latency (e.g. 8 decimals for `cost_usd`, 3 for milliseconds).
- **Throughput is completion throughput.** `requests_per_second` counts all
  attempts including failures; also report `successful_requests_per_second`.
- **Redact provider errors.** Persist only a category/type
  (`{"status":"error","error":"request failed","error_type":"RuntimeError"}`),
  never raw exception text that may contain URLs, request bodies, or tokens.
- **Bound the inputs.** Cap concurrency (e.g. ≤100) and iterations; reject
  empty, duplicate, or malformed level lists like `1,,5`.

## Result schema & versioning

Every result carries a `schema_version` and validates against a strict JSON
schema: numeric types, non-negative ranges, `failure_rate` in `[0,1]`,
timestamps, request ids, and success/error-specific fields. Evolve the schema
**additively** — introduce a new version (e.g. 1.1) for concurrency/percentile
fields and keep the previous version and its samples valid. Deliberately
nonsensical results (negative counts, string latency, failure_rate > 1) must be
rejected.

## Workflow (TDD)

1. Write failing tests for metric math, nearest-rank percentiles, concurrency
   request counts, error isolation, and schema validation of both a single run
   and a concurrency suite.
2. Implement the runner (thread pool), adapters (offline deterministic mock +
   callable integration), metrics, CLI, and schema.
3. Run lint + tests to green. Generate a real benchmark artifact via the CLI and
   validate it against the schema — proof, not a description.
4. If published as a repo/CI, watch the pipeline to success across supported
   Python versions before claiming done. Verify the live artifact, not just the
   local run.

## Pitfalls

- Do not put quality metrics in a performance result.
- Do not share a stateful adapter across threads without a factory.
- Do not let a partial/failed request abort the whole load profile.
- Do not report fabricated numbers — every metric must come from a real run.
