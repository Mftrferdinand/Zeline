"""Gateway HTTP webhook generik Zeline.

Endpoint:

- ``GET /health`` → status tanpa rahasia
- ``POST /message`` → JSON ``{"chat_id":"abc", "text":"halo"}``

Autentikasi ``POST /message`` wajib menggunakan salah satu:

- ``Authorization: Bearer <webhook-token>``
- ``X-Aesora-Token: <webhook-token>``

Default bind hanya ``127.0.0.1``. Untuk mengekspos ke internet, letakkan
reverse proxy/tunnel yang HTTPS di depannya dan gunakan token yang kuat.
"""
from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from aesora.agent import ZelineError

MAX_BODY_BYTES = 32_000


def info() -> dict[str, str]:
    return {
        "label": "Webhook HTTP",
        "hint": "Endpoint POST /message untuk integrasi aplikasi sendiri.",
    }


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    token = str(cfg.get("token", ""))
    if len(token) < 16:
        errors.append("token webhook kosong/terlalu pendek (minimum 16 karakter)")
    try:
        port = int(cfg.get("port", 8765))
        if not 1 <= port <= 65535:
            errors.append("port webhook harus 1–65535")
    except (TypeError, ValueError):
        errors.append("port webhook tidak valid")
    if str(cfg.get("tool_profile", "safe")) not in {"safe", "workspace", "full"}:
        errors.append("tool_profile webhook tidak valid")
    return errors


def _is_authorized(headers, token: str) -> bool:
    supplied = headers.get("X-Aesora-Token", "")
    authorization = headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied, token)


def start(sessions, cfg: dict[str, Any], stop_event) -> None:
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
    server.timeout = 0.5
    print(f"  [webhook] listening http://{host}:{port} (/health, /message)", flush=True)
    try:
        while not stop_event.is_set():
            server.handle_request()
    finally:
        server.server_close()
        print("  [webhook] berhenti", flush=True)
