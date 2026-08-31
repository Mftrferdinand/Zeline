#!/usr/bin/env python3
"""Zeline App client — reference implementation for the Android/iOS front-end.

Runs the full contract against a live gateway: login, roster, session, streamed
turn with tool events, cancel, history. Everything here is a real HTTP call; if
the gateway is not running the script says so and exits instead of printing a
simulated transcript.

    python3 examples/zeline_app_client.py [BASE_URL] [GATEWAY_TOKEN]

Start a gateway first:

    python3 run_zeline_app.py --profile safe
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8082") + "/api/v1"
GATEWAY_TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""


def call(path: str, method: str = "GET", body: dict | None = None,
         token: str | None = None, timeout: int = 60) -> tuple[int, dict]:
    request = urllib.request.Request(BASE + path, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(request, data=data, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def stream_turn(session_id: str, content: str, token: str,
                on_event, timeout: int = 300) -> None:
    """Consume one SSE turn. Frames are `event:`/`data:` pairs split by a blank line."""
    request = urllib.request.Request(
        f"{BASE}/sessions/{session_id}/messages?stream=true", method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", "Bearer " + token)
    payload = json.dumps({"content": content}).encode()
    with urllib.request.urlopen(request, data=payload, timeout=timeout) as response:
        buffer = ""
        for raw in response:
            buffer += raw.decode("utf-8", "replace")
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                for line in frame.split("\n"):
                    if line.startswith("data: "):
                        try:
                            on_event(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass


def render(event: dict) -> None:
    """How a mobile client would render each event type."""
    kind = event.get("type")
    if kind == "stream.started":
        print(f"[stream {event.get('stream_id')}] model={event.get('model')}", flush=True)
    elif kind == "assistant.thinking":
        print("… thinking", flush=True)
    elif kind == "assistant.delta":
        sys.stdout.write(event.get("content", ""))
        sys.stdout.flush()
    elif kind == "tool.started":
        print(f"\n▸ {event.get('title')}: {event.get('input_summary')}", flush=True)
    elif kind == "tool.output":
        sys.stdout.write(event.get("content", ""))
        sys.stdout.flush()
    elif kind in ("tool.completed", "tool.failed"):
        print(f"◂ {event.get('status')} ({event.get('duration_ms')}ms)", flush=True)
    elif kind == "assistant.completed":
        meta = event.get("metadata", {})
        print(f"\n[done] {meta.get('chars')} chars, {meta.get('tool_calls')} tool calls", flush=True)
    elif kind == "stream.cancelled":
        print(f"\n[cancelled] {len(event.get('partial_content', ''))} chars kept", flush=True)
    elif kind == "stream.error":
        print(f"\n[error {event.get('error_code')}] {event.get('message')}", flush=True)


def main() -> int:
    status, payload = call("/health")
    if status != 200:
        print(f"Gateway not reachable at {BASE} (HTTP {status}). "
              "Start it with: python3 run_zeline_app.py", flush=True)
        return 1
    print("health:", json.dumps(payload.get("data", {}))[:200], flush=True)

    if not GATEWAY_TOKEN:
        print("\nPass the gateway token to continue: "
              "python3 examples/zeline_app_client.py <base_url> <gateway_token>", flush=True)
        return 1

    status, payload = call("/auth/login", "POST", {"gateway_token": GATEWAY_TOKEN})
    token = payload.get("data", {}).get("access_token", "")
    if not token:
        print(f"login failed: HTTP {status} {json.dumps(payload)[:200]}", flush=True)
        return 1
    print("logged in, JWT acquired", flush=True)

    agents = call("/agents", token=token)[1].get("data", {}).get("agents", [])
    if not agents:
        print("no agents configured", flush=True)
        return 1
    agent = agents[0]
    print(f"agent: {agent.get('name')} ({agent.get('model')})", flush=True)

    session_id = call("/sessions", "POST",
                      {"agent_id": agent["id"], "title": "client example"},
                      token=token)[1].get("data", {}).get("id", "")
    print(f"session: {session_id}\n", flush=True)

    stream_turn(session_id, "Jalankan `echo hello-from-zeline` dan laporkan outputnya.",
                token, render)

    # Cancel: the app's stop button. Fire a long turn, stop it mid-flight.
    long_session = call("/sessions", "POST", {"agent_id": agent["id"], "title": "cancel demo"},
                        token=token)[1].get("data", {}).get("id", "")
    print("\n--- cancel demo ---", flush=True)
    worker = threading.Thread(
        target=lambda: stream_turn(
            long_session, "Tulis penjelasan sangat panjang tentang HTTP, minimal 1500 kata.",
            token, render),
        daemon=True)
    worker.start()
    time.sleep(8)
    print("\n[sending cancel]", flush=True)
    print(json.dumps(call(f"/sessions/{long_session}/cancel", "POST", {},
                          token=token)[1].get("data", {})), flush=True)
    worker.join(timeout=120)

    messages = call(f"/sessions/{session_id}/messages", token=token)[1].get("data", {}).get("messages", [])
    print(f"\nhistory: {len(messages)} messages, roles="
          f"{[m.get('role') for m in messages]}", flush=True)
    for message in messages:
        events = message.get("metadata", {}).get("tool_events") or []
        for event in events:
            print(f"  tool_event: {event.get('tool')} {event.get('status')} "
                  f"{event.get('duration_ms')}ms", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
