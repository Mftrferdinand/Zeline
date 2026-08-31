# Zeline App Gateway — architecture

The first-party gateway for the Zeline mobile app. It exposes the existing agent
runtime over HTTP; it does not reimplement any of it.

```
Zeline App (Android/iOS)
  │  REST + SSE over /api/v1
  ▼
zeline/gateways/zeline_app.py          HTTP surface: routing, auth, envelopes
  ├── zeline/app_auth.py               JWT sign/verify, sanitize_for_client
  ├── zeline/app_data.py               agents, sessions, provider refs (JSON on disk)
  └── zeline/gateways/zeline_app_runtime.py
        │  cancel/steer registry, event fan-out, history persistence
        ▼
      zeline.agent.Zeline              the same agent the CLI and Telegram use
        └── ToolExecutor → tools / skills / memory
        └── provider (OpenAI-compatible or Anthropic protocol)
```

Splitting the HTTP surface from the runtime is what keeps a wire-format change
from touching agent behaviour, and vice versa. `zeline_app.py` never talks to a
provider; `zeline_app_runtime.py` never writes an HTTP header.

## Storage

`~/.zeline/app/` — `agents.json`, `sessions.json`, per-session message and
history files. Overridable with `ZELINE_APP_DATA_DIR`, which is how tests stay
out of a real user's roster. Provider API keys are **not** stored here; they live
in `zeline/config` and are referenced by `provider_id`.

## Streaming and cancellation

The gateway forces `stream_responses=True` on its agent instances. This is a
protocol requirement, not a preference: `assistant.delta` only exists if tokens
arrive incrementally, and `/cancel` is a flag checked inside the SSE read loop.
With the global `agent.stream` CLI preference off, the gateway would emit one
delta at the end and cancel could not land until the blocking provider request
returned — measured at 113–211s versus 2.7s with streaming on.

Event types and fields: `docs/SSE_EVENT_SCHEMA.md`.

## Security

- Provider keys never appear in any response. `app_auth.sanitize_for_client`
  strips `api_key`, `token`, `credential`; `/providers` returns only a
  `••••XXXX` hint.
- Every endpoint except `/health` and `/auth/login` requires a Bearer JWT.
- `/system` deliberately omits IP addresses; a test asserts no dotted quad
  appears anywhere in its payload.
- `tool_profile` (`safe`, `workspace`, `full`) bounds what tools a session can
  reach, same as the CLI.
- The gateway binds `127.0.0.1` by default. Binding a routable interface exposes
  an agent that can run shell commands to your network — put it behind a TLS
  terminator with its own auth, and rotate the gateway token if it leaks.

## Compatibility

Telegram, WhatsApp, and webhook gateways are untouched. `gateways/__init__.py`
registers `zeline_app` optionally, so an install without the module still
imports. Agent-side changes are limited to a per-instance `stream_responses`
override that defaults to the previous global behaviour.

## Verification

- `tests/test_zeline_app.py`, `tests/test_zeline_app_contract.py` — contract,
  auth, sanitization, cancel idempotency, streaming override
- `verify_zeline_app_real.py` — end-to-end against a live gateway and a real
  provider: real tool execution, non-scripted answers, mid-stream cancel,
  history persistence
- `examples/zeline_app_client.py` — reference consumer of every SSE event
