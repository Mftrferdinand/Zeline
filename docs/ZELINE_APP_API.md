# Zeline App API — contract v1

Namespace `/api/v1/`. All responses use one envelope.

Success: `{"status":"ok","data":{...},"request_id":"req_..."}`
Error: `{"error":{"code":"SESSION_NOT_FOUND","message":"...","request_id":"req_..."}}`

## Auth

`POST /auth/login` with `{"gateway_token":"..."}` → `{"access_token":"<JWT>"}`.
Every other endpoint requires `Authorization: Bearer <JWT>`; a missing or invalid
token gets `401 UNAUTHORIZED`. The signing secret comes from
`ZELINE_APP_SECRET`. Refresh is not implemented — a client re-runs login when a
token stops working.

## Endpoints

Health and system
- `GET /health` → liveness + `api_version`, `sse`
- `GET /system` → host OS/arch/python/zeline version. No IP address is ever
  included, so the response is safe to show in-app.

Providers
- `GET /providers` → refs with `api_key_hint` (`"••••7F2A"`); the full key is
  never returned by any endpoint
- `POST /providers` → `{name, base_url, api_key}`; models are auto-detected from
  the provider's `/models` unless `models` is supplied
- `GET|DELETE /providers/{id}`
- `POST /providers/{id}/test` → reachability + latency, key not echoed
- `POST /providers/detect` → probe a base URL before saving

Agents
- `GET /agents` → full roster; `connected` is true only for profiles whose Agent
  Token this gateway stores
- `POST /agents` → `201` with the profile, or `409 LIMIT_REACHED`: one account
  holds one agent, and separate chat rooms are separate *sessions*
- `GET|PUT|DELETE /agents/{id}`
- `POST /agent-connections` → bind an Agent Token to this gateway

Sessions and messages
- `GET|POST /sessions`, `GET|DELETE /sessions/{sid}`
- `GET /sessions/{sid}/messages` → persisted history
- `POST /sessions/{sid}/messages` → JSON turn; add `?stream=true` for SSE
- `POST /sessions/{sid}/cancel` → `{session_id, stream_id, cancelled}`
- `POST /sessions/{sid}/steer` → inject guidance into a *running* turn;
  `accepted:false` when no stream is active
- `POST /attachments` → metadata only (`filename`, `size_bytes`, `mime_type`,
  `url_preview`); bytes belong to the storage layer

## Schemas

Message: `id`, `session_id`, `agent_id`, `role` (`user|assistant|system|tool`),
`content`, `created_at`, `status`, `attachments`, `metadata`. Tool activity is in
`metadata.tool_events[]` as `{tool_call_id, tool, title, status, duration_ms}`,
so history renders the same collapsible cards as a live stream.

Agent: `id`, `name`, `avatar`, `description`, `provider_id`, `model`,
`system_instructions`, `enabled_tools`, `enabled_skills`, `memory_enabled`,
`created_at`, `updated_at`. Timestamps are stamped in the storage layer, so
profiles created from the CLI or a migration carry them too.

Provider: `id`, `name`, `type`, `base_url`, `credential_status`, `api_key_hint`.

## Error codes

`UNAUTHORIZED`, `INVALID_TOKEN`, `VALIDATION_ERROR`, `AGENT_NOT_FOUND`,
`SESSION_NOT_FOUND`, `LIMIT_REACHED`, `PROVIDER_UNAVAILABLE`,
`MODEL_UNAVAILABLE`, `TOOL_FAILURE`, `RATE_LIMITED`, `PROVIDER_ERROR`,
`INTERNAL_ERROR`.

## Streaming

See `SSE_EVENT_SCHEMA.md` for every event type and field. Reference client:
`examples/zeline_app_client.py`. End-to-end check against a live gateway:
`verify_zeline_app_real.py`.

## Versioning

Breaking changes bump the `/api/vN` prefix. New endpoints and new optional
fields are additive within v1.
