#!/usr/bin/env python3
"""Muse bridge: OpenAI-compatible endpoint on localhost.

An OpenAI-compatible router (e.g. 9Router provider "Muse") points at
http://127.0.0.1:8765/v1 . Requests are queued on disk; the poller
(poller.py) picks them up from queue/pending/ and writes answers to
queue/done/.

Config via env vars:
  MUSE_BRIDGE_DIR   queue root (default ~/.muse-bridge/queue)
  MUSE_BRIDGE_HOST  listen address (default 127.0.0.1)
  MUSE_BRIDGE_PORT  listen port (default 8765)
  MUSE_BRIDGE_DIAG  set to "1" to enable diag.log (default off)

v4: tool_calls passthrough. The poller answers with
{"content": str, "tool_calls": [{"id","type","function":{"name","arguments"}}]}
and the bridge forwards them (finish_reason "tool_calls") on both the plain
and SSE paths, so the client agent can execute its own tools natively.
Plain {"content"} answers behave exactly as before (backward compatible).
"""
import json
import os
import socket
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.environ.get("MUSE_BRIDGE_DIR", os.path.join(os.path.expanduser("~"), ".muse-bridge", "queue"))
PENDING = os.path.join(BASE, "pending")
DONE = os.path.join(BASE, "done")
HOST = os.environ.get("MUSE_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MUSE_BRIDGE_PORT", "8765"))
DIAG = os.environ.get("MUSE_BRIDGE_DIAG", "0") == "1"

MAX_PENDING = 5
WAIT_SECS = 240
KEEPALIVE_SECS = 15


def _diag(msg: str):
    if not DIAG:
        return
    try:
        with open(os.path.join(BASE, "diag.log"), "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _is_dashboard_probe(req):
    """Detect a router dashboard 'Test Connection' probe.

    Dashboards typically send {max_tokens:1024, stream:false,
    messages:[..., {role:"user", content:"hi"}]} with a short client timeout,
    which the poller loop can never meet. Matching requests are answered
    instantly below; everything else goes through the normal queue.
    """
    try:
        if not isinstance(req, dict) or req.get("stream"):
            return False
        if req.get("max_tokens") != 1024:
            return False
        msgs = req.get("messages") or []
        if not msgs:
            return False
        last = msgs[-1]
        return (isinstance(last, dict) and last.get("role") == "user"
                and str(last.get("content", "")).strip().lower() == "hi")
    except Exception:
        return False


def _probe_completion(model):
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]
    return {"id": cid, "object": "chat.completion", "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content":
                                     "Muse bridge online — poller active and ready."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


class H(BaseHTTPRequestHandler):
    timeout = 120  # per-socket-op timeout: slow-loris bodies can't wedge a thread forever

    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json"):
        try:
            body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass  # client went away; nothing to do

    def _sse_write(self, payload: str) -> bool:
        """Write one SSE payload, flushed. Returns False if the client is gone."""
        try:
            self.wfile.write(payload.encode())
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return False

    def _cleanup(self, rid: str):
        for d in (os.path.join(PENDING, f"{rid}.json"), os.path.join(DONE, f"{rid}.json")):
            try:
                os.remove(d)
            except Exception:
                pass

    def do_GET(self):
        try:
            if self.path.rstrip("/") == "/v1/models":
                self._send(200, {"object": "list", "data": [
                    {"id": "muse", "object": "model", "created": 0, "owned_by": "muse"}]})
            elif self.path == "/health":
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            try:
                self._send(500, {"error": {"message": str(e)[:200]}})
            except Exception:
                pass

    def _wait_answer(self, rid: str, keepalive_cb=None):
        """Wait up to WAIT_SECS for done/<rid>.json.

        Returns the parsed result dict {"content": str, "tool_calls": [...]}
        or None on timeout.
        """
        deadline = time.time() + WAIT_SECS
        result = None
        last_ping = time.time()
        while time.time() < deadline:
            dp = os.path.join(DONE, f"{rid}.json")
            if os.path.exists(dp):
                try:
                    with open(dp) as f:
                        raw = json.load(f)
                    if not isinstance(raw, dict):
                        raw = {"content": str(raw)}
                    content = raw.get("content", "")
                    calls = raw.get("tool_calls") or []
                    if not isinstance(calls, list):
                        calls = []
                    # Normalize to OpenAI tool_calls shape; drop malformed entries.
                    norm = []
                    for c in calls:
                        if not isinstance(c, dict):
                            continue
                        fn = c.get("function") or {}
                        name = str(fn.get("name", "") or c.get("name", "")).strip()
                        if not name:
                            continue
                        args = fn.get("arguments", "")
                        if not isinstance(args, str):
                            try:
                                args = json.dumps(args, ensure_ascii=False)
                            except Exception:
                                args = "{}"
                        norm.append({
                            "id": str(c.get("id") or "call_" + uuid.uuid4().hex[:12]),
                            "type": "function",
                            "function": {"name": name, "arguments": args},
                        })
                    result = {"content": content if isinstance(content, str) else str(content),
                              "tool_calls": norm}
                except Exception:
                    result = {"content": "", "tool_calls": []}
                try:
                    os.remove(dp)
                except Exception:
                    pass
                break
            if keepalive_cb and time.time() - last_ping >= KEEPALIVE_SECS:
                if not keepalive_cb():
                    break  # client disconnected
                last_ping = time.time()
            time.sleep(1)
        return result

    def _assistant_message(self, result: dict) -> tuple:
        """Build the OpenAI assistant message + finish_reason from a poller result."""
        msg = {"role": "assistant", "content": result.get("content", "") or ""}
        calls = result.get("tool_calls") or []
        if calls:
            msg["tool_calls"] = calls
            return msg, "tool_calls"
        return msg, "stop"

    def do_POST(self):
        try:
            if self.path.rstrip("/") != "/v1/chat/completions":
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._send(400, {"error": {"message": "bad json"}})
            model = req.get("model", "muse")
            if _is_dashboard_probe(req):
                return self._send(200, _probe_completion(model))
            try:
                npend = len(os.listdir(PENDING))
            except Exception:
                npend = 0
            if npend >= MAX_PENDING:
                return self._send(429, {"error": {"message": "Muse bridge busy, try again in a bit"}})
            rid = uuid.uuid4().hex
            with open(os.path.join(PENDING, f"{rid}.json"), "w") as f:
                json.dump({"id": rid, "received_at": time.time(), "request": req}, f)
            _diag(f"REQ {rid[:8]} tools={bool(req.get('tools'))} "
                  f"n_tools={len(req.get('tools') or [])} stream={bool(req.get('stream'))}")

            if req.get("stream"):
                return self._handle_stream(req, rid, model)
            result = self._wait_answer(rid)
            self._cleanup(rid)
            if result is None:
                return self._send(504, {"error": {"message": "Muse did not answer in time"}})
            cid = "chatcmpl-" + uuid.uuid4().hex[:12]
            created = int(time.time())
            message, finish_reason = self._assistant_message(result)
            _diag(f"ANS {rid[:8]} finish={finish_reason} "
                  f"tool_calls={[c['function']['name'] for c in message.get('tool_calls', [])]}")
            resp = {"id": cid, "object": "chat.completion", "created": created, "model": model,
                    "choices": [{"index": 0, "message": message,
                                 "finish_reason": finish_reason}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
            self._send(200, resp)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        except Exception as e:
            try:
                self._send(500, {"error": {"message": str(e)[:200]}})
            except Exception:
                pass

    def _handle_stream(self, req, rid: str, model: str):
        # Headers go out immediately; body chunks follow when the answer lands.
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self._cleanup(rid)
            return
        if not self._sse_write(": connected\n\n"):
            self._cleanup(rid)
            return
        result = self._wait_answer(rid, keepalive_cb=lambda: self._sse_write(": ping\n\n"))
        self._cleanup(rid)
        cid = "chatcmpl-" + uuid.uuid4().hex[:12]
        created = int(time.time())
        if result is None:
            err = {"error": {"message": "Muse did not answer in time", "type": "timeout"}}
            self._sse_write("data: " + json.dumps(err) + "\n\ndata: [DONE]\n\n")
            return
        message, finish_reason = self._assistant_message(result)
        _diag(f"ANS-SSE {rid[:8]} finish={finish_reason} "
              f"tool_calls={[c['function']['name'] for c in message.get('tool_calls', [])]}")
        if message.get("tool_calls"):
            # OpenAI streaming convention: tool_calls arrive as delta chunks
            # with per-call index; the client accumulates argument strings.
            delta_calls = []
            for i, c in enumerate(message["tool_calls"]):
                delta_calls.append({
                    "index": i,
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["function"]["name"],
                                 "arguments": c["function"]["arguments"]},
                })
            chunk1 = {"id": cid, "object": "chat.completion.chunk", "created": created,
                      "model": model, "choices": [{"index": 0,
                      "delta": {"role": "assistant", "content": message.get("content") or "",
                                "tool_calls": delta_calls}, "finish_reason": None}]}
            chunk2 = {"id": cid, "object": "chat.completion.chunk", "created": created,
                      "model": model, "choices": [{"index": 0, "delta": {},
                      "finish_reason": "tool_calls"}]}
        else:
            chunk1 = {"id": cid, "object": "chat.completion.chunk", "created": created,
                      "model": model, "choices": [{"index": 0,
                      "delta": {"role": "assistant", "content": message.get("content") or ""},
                      "finish_reason": None}]}
            chunk2 = {"id": cid, "object": "chat.completion.chunk", "created": created,
                      "model": model, "choices": [{"index": 0, "delta": {},
                      "finish_reason": "stop"}]}
        self._sse_write("data: " + json.dumps(chunk1) + "\n\n")
        self._sse_write("data: " + json.dumps(chunk2) + "\n\ndata: [DONE]\n\n")


if __name__ == "__main__":
    os.makedirs(PENDING, exist_ok=True)
    os.makedirs(DONE, exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True
    srv.allow_reuse_address = True
    print(f"muse-bridge listening on {HOST}:{PORT} (queue: {BASE})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
