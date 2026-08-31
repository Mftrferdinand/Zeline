# SSE Event Schema — Zeline App Gateway (v1)

Transport: `POST /api/v1/sessions/{sid}/messages?stream=true` → `text/event-stream`.
Each frame is `event: <type>` + `data: <json>` + a blank line. Every payload
carries `version`, `type`, `session_id`, `timestamp`; the fields below are added
per type.

The client never parses assistant prose to discover what happened — tool
activity arrives as its own events.

## Lifecycle

| type | added fields | meaning |
| --- | --- | --- |
| `stream.started` | `stream_id`, `message_id`, `agent_id`, `model` | turn accepted; `stream_id` is what `/cancel` reports back |
| `assistant.thinking` | `message_id`, `state`, `round`, `max_rounds` | emitted once at round 1, before any token |
| `assistant.delta` | `message_id`, `content` | one token chunk; append in order |
| `assistant.completed` | `message_id`, `content`, `full_content`, `status`, `metadata{model,chars,tool_calls}` | `content` is the final bubble, `full_content` the whole turn |
| `stream.cancelled` | `stream_id`, `message_id`, `reason`, `partial_content`, `tail_content` | cancel landed; partial text is kept, not discarded |
| `stream.error` | `message_id`, `error_code`, `message`, `partial_content` | `error_code` ∈ `RATE_LIMITED`, `PROVIDER_ERROR` |
| `session.updated` | `session{id,title,agent_id,message_count,updated_at,last_preview}` | list-screen row refresh, sent after persistence |
| `self.improvement` | `message_id`, `detail` | only when the agent really created/updated a skill |

## Tools

| type | added fields |
| --- | --- |
| `tool.started` | `tool_call_id`, `tool`, `title`, `input_summary` |
| `tool.output` | `tool_call_id`, `content` (chunked; concatenate) |
| `tool.completed` | `tool_call_id`, `status`, `duration_ms` |
| `tool.failed` | `tool_call_id`, `status`, `error_code`, `message`, `duration_ms` |

`tool_call_id` correlates the three; text emitted after a tool belongs to a new
bubble, which is why `assistant.completed.content` can be shorter than
`full_content`.

## Example

```
event: stream.started
data: {"version":1,"type":"stream.started","session_id":"sess_ab4d","stream_id":"stream_10a7","message_id":"msg_7f2c","agent_id":"agent_jSx7","model":"Just/claude-opus-4-8","timestamp":"2026-08-31T10:41:13Z"}

event: assistant.delta
data: {"version":1,"type":"assistant.delta","session_id":"sess_ab4d","message_id":"msg_7f2c","content":"Menjalankan"}

event: tool.started
data: {"version":1,"type":"tool.started","session_id":"sess_ab4d","tool_call_id":"tool_22d5","tool":"run_shell","title":"Running terminal command","input_summary":"echo hello-from-zeline"}

event: tool.output
data: {"version":1,"type":"tool.output","session_id":"sess_ab4d","tool_call_id":"tool_22d5","content":"exit=0\nhello-from-zeline"}

event: tool.completed
data: {"version":1,"type":"tool.completed","session_id":"sess_ab4d","tool_call_id":"tool_22d5","status":"success","duration_ms":44}

event: assistant.completed
data: {"version":1,"type":"assistant.completed","session_id":"sess_ab4d","message_id":"msg_7f2c","content":"Output: `hello-from-zeline` (exit code 0).","status":"complete","metadata":{"model":"Just/claude-opus-4-8","chars":75,"tool_calls":1}}
```

## Cancellation

`POST /api/v1/sessions/{sid}/cancel` → `{session_id, stream_id, cancelled}`.
`cancelled` is true only when a stream was actually running; a second call after
the stream ended returns `cancelled:false` with HTTP 200, so a double tap on the
stop button is harmless.

Cancellation needs streaming to be on: the flag is checked inside the SSE read
loop. The gateway therefore forces `stream_responses=True` on its agent
instances regardless of the global `agent.stream` CLI preference — with
streaming off, nothing reads in a loop and cancel cannot land until the blocking
provider request returns (up to 180s).

A working consumer of every event above lives in
`examples/zeline_app_client.py`.
