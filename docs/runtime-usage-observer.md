# Opt-in per-turn usage observer

`Zeline.send(..., on_usage=callback)` accepts a new trailing optional keyword.
Existing callers need no changes. The callback receives a fresh dictionary:

```python
{"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
```

Every field is either a nonnegative reported integer or `None`. Missing usage
is not zero; a reported total alone is never relabeled as output tokens. If
both input and output are known, their sum supplies a missing total. No cost,
credentials, endpoint, model reasoning or provider payload is exposed.

The observer is invoked at the existing response-usage recording boundary,
before and independently of best-effort statistics persistence. Observer failure
must not change the answer. It is attached only during that send and cleared
in finally, including exceptional exits. `reflect()`/later sends do not inherit it.
Concurrent sends on the same instance remain unsupported; this callback is not a
thread-safe shared-agent scheduler.

OpenAI-compatible streams emit one event for the final usage chunk (or an
all-unknown event on normal completion without usage). Anthropic streams combine
message_start input usage and message_delta output usage into one event;
missing components remain unknown, including when the other component is zero.
Non-stream responses use the existing `_record_usage` path. Failed/interrupted
requests may have no final usage event, so observers must not infer zero spend.
A retry is another provider response, not a duplicate to silently discard.

Zeline-Eval's RuntimeBridge discovers this explicit callback without patching
shared methods or reading lifetime database counters. It sums a field only when
every observed response provides it, and leaves automatic aggregates unknown on
failed/interrupted turns. Older installed runtimes fall back to unknown usage.
Cost remains unknown unless separately reported through an explicit reader.

`reported_usage` is intentionally separate from the existing `extract_usage`
statistics helper. The legacy extraction/pricing API is unchanged. Streaming counters now reject
malformed/non-integer values instead of coercing them, so malformed relay usage
may be omitted from legacy stream statistics as well. Source implementation is not proof the installed package
or running gateway has been upgraded.
