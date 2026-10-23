"""Gateway HTTP webhook generik Zeline.

Endpoint:

- ``GET /health`` → status tanpa rahasia
- ``POST /message`` → JSON ``{"chat_id":"abc", "text":"halo"}``

Autentikasi ``POST /message`` wajib menggunakan salah satu:

- ``Authorization: Bearer <webhook-token>``
- ``X-Zeline-Token: <webhook-token>``

Default bind hanya ``127.0.0.1``. Untuk mengekspos ke internet, letakkan
reverse proxy/tunnel yang HTTPS di depannya dan gunakan token yang kuat.
"""
from __future__ import annotations

from zeline.command_ui import emit as print

import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from zeline.agent import ZelineError

MAX_BODY_BYTES = 32_000
#: Sliding-window rate limit per client identity. A public webhook without a
#: rate limit lets one noisy client flood the agent session queue and starve
#: every other chat; 30 requests / minute per identity is generous for a
#: conversational endpoint but stops naive floods.
RATE_LIMIT_MAX_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60.0


class _RateLimiter:
    """Per-identity sliding-window limiter, safe under ThreadingHTTPServer."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, identity: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(identity, []) if now - t < self._window]
            if len(hits) >= self._max:
                self._hits[identity] = hits
                return False
            hits.append(now)
            self._hits[identity] = hits
            # opportunistic cleanup so the map cannot grow without bound
            if len(self._hits) > 1024:
                self._hits = {
                    key: keep for key, keep in self._hits.items()
                    if keep and now - keep[-1] < self._window
                }
            return True


def info() -> dict[str, str]:
    return {
        "label": "Webhook HTTP",
        "hint": "POST /message endpoint for your own app integration.",
    }


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    token = str(cfg.get("token", ""))
    if len(token) < 16:
        errors.append("webhook token empty/too short (minimum 16 characters)")
    try:
        port = int(cfg.get("port", 8765))
        if not 1 <= port <= 65535:
            errors.append("webhook port must be 1–65535")
    except (TypeError, ValueError):
        errors.append("invalid webhook port")
    if str(cfg.get("tool_profile", "safe")) not in {"safe", "workspace", "full"}:
        errors.append("invalid webhook tool_profile")
    return errors


def _is_authorized(headers, token: str) -> bool:
    supplied = headers.get("X-Zeline-Token", "")
    authorization = headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied, token)


def start(
    sessions,
    cfg: dict[str, Any],
    stop_event,
    ready: Callable[[int], None] | None = None,
) -> None:
    host = str(cfg.get("host", "127.0.0.1"))
    port = int(cfg.get("port", 8765))
    token = str(cfg["token"])
    tool_profile = str(cfg.get("tool_profile", "safe"))

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZelineWebhook/0.1"

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/health":
                self._json(200, {"ok": True, "service": "zeline-webhook"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/message":
                self._json(404, {"error": "not found"})
                return
            client_ip = self.client_address[0] if self.client_address else "unknown"
            if not rate_limiter.allow(client_ip):
                self._json(429, {"error": "rate limited; slow down and retry later"})
                return
            if not _is_authorized(self.headers, token):
                self._json(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(400, {"error": "invalid content length"})
                return
            if not 0 < length <= MAX_BODY_BYTES:
                self._json(413, {"error": f"body must be 1–{MAX_BODY_BYTES} bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(400, {"error": "invalid JSON"})
                return
            if not isinstance(body, dict):
                self._json(400, {"error": "JSON body must be an object"})
                return
            chat_id = str(body.get("chat_id", "default")).strip()
            text = str(body.get("text", "")).strip()
            if not chat_id or len(chat_id) > 256:
                self._json(400, {"error": "invalid chat_id"})
                return
            if not text:
                self._json(400, {"error": "text is required"})
                return
            try:
                reply = sessions.send(
                    identity=f"webhook:{chat_id}",
                    text=text,
                    tool_profile=tool_profile,
                )
                self._json(200, {"reply": reply})
            except ZelineError as exc:
                self._json(502, {"error": str(exc)})
            except Exception:
                print("  [webhook] unhandled agent error", flush=True)
                self._json(500, {"error": "internal agent error"})

        def log_message(self, _format: str, *_args: Any) -> None:
            # Jangan log payload atau Authorization token.
            return

    server = ThreadingHTTPServer((host, port), Handler)
    rate_limiter = _RateLimiter(RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)
    server.timeout = 0.5
    bound_port = int(server.server_address[1])
    if ready is not None:
        ready(bound_port)
    print(f"  [webhook] listening http://{host}:{bound_port} (/health, /message)", flush=True)
    try:
        while not stop_event.is_set():
            server.handle_request()
    finally:
        server.server_close()
        print("  [webhook] stopped", flush=True)
