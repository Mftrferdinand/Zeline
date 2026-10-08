"""Peer bot-to-bot communication for Zeline.

Two Zeline instances can exchange messages and delegate tasks to each other.

Server (this instance receives):
    zeline peer serve --port 8788

Client (send to another instance):
    zeline peer send http://other-host:8788 "kerjakan X"

Protocol:
    POST /peer/message
    Headers: X-Zeline-Token: <shared-secret>  (or Authorization: Bearer <secret>)
    Body: {"from": "<peer-name>", "text": "<message>", "reply_to": "<optional-msg-id>"}
    Response: {"ok": true, "response": "<agent reply>", "from": "<this-name>"}

    GET /peer/health  (no auth — liveness only, no sensitive data)

Security model:
- Shared secret required on every /peer/message call (hmac.compare_digest).
- A peer holding the secret is trusted like the operator: incoming messages
  run through the full agent loop (same tools as CLI). Only share the secret
  with instances you operate.
- Basic per-IP rate limiting on POST (applied before auth, so 401 floods
  are throttled too).
- No redirects are followed when sending (fail-closed).
- SSRF guard on send: non-public target hosts (RFC1918, link-local /
  cloud metadata, multicast, unspecified) are refused; loopback stays
  allowed because local peers are the primary use case.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

DEFAULT_PORT = 8788
_RATE_MAX = 30          # max POST /peer/message per window per IP
_RATE_WINDOW_S = 60.0
_MAX_KEYS = 1024        # max tracked IPs (memory cap)


class _RateLimiter:
    """Simple per-key sliding-window rate limiter (thread-safe)."""

    def __init__(self, max_hits: int, window_s: float, max_keys: int = _MAX_KEYS):
        self._max_hits = max(1, int(max_hits))
        self._window_s = float(window_s)
        self._max_keys = max(1, int(max_keys))
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window_s
        with self._lock:
            hits = self._hits.get(key)
            if hits is not None:
                while hits and hits[0] <= cutoff:
                    hits.popleft()
                if not hits:
                    del self._hits[key]
                    hits = None
            if hits is None:
                if len(self._hits) >= self._max_keys:
                    oldest = min(self._hits, key=lambda k: self._hits[k][-1])
                    del self._hits[oldest]
                hits = self._hits[key] = deque()
            if len(hits) >= self._max_hits:
                return False
            hits.append(now)
            return True


_POST_LIMITER = _RateLimiter(_RATE_MAX, _RATE_WINDOW_S)


def generate_secret() -> str:
    """Generate a new cryptographically secure peer shared secret."""
    return secrets.token_urlsafe(32)


def _addr_is_internal(addr: ipaddress._BaseAddress) -> bool:
    """True for non-routable / non-public addresses (SSRF guard).

    Loopback is deliberately NOT treated as internal here: the primary
    peer use case is two instances on the same machine talking over
    localhost. Cloud-metadata (169.254.169.254), RFC1918, and other
    non-public ranges are still blocked.
    """
    if addr.is_loopback:
        return False
    return (
        addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _host_is_blocked(host: str) -> bool:
    """True if ``host`` resolves only to SSRF-unsafe addresses.

    DNS failures are treated as blocked (fail closed).
    """
    import socket

    host = host.strip().strip("[]")
    if not host:
        return True
    try:
        addr = ipaddress.ip_address(host)
        addrs = [addr]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return True  # DNS failed = do not attempt
        if not infos:
            return True
        addrs = []
        for info in infos:
            try:
                addrs.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                return True
    # Block if ANY resolved address is non-public (fail closed on mixed
    # DNS answers — requests may connect to any of them).
    return any(_addr_is_internal(a) for a in addrs) if addrs else True


def _is_authorized(headers, secret: str) -> bool:
    """True if the request carries the correct shared secret."""
    if not secret:
        return False
    supplied = headers.get("X-Zeline-Token", "") or ""
    authorization = headers.get("Authorization", "") or ""
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    supplied = supplied.strip()
    return bool(supplied) and hmac.compare_digest(supplied, secret)


def _run_peer_turn(from_name: str, text: str, reply_to: str = "") -> str:
    """Run one agent turn for an incoming peer message."""
    from zeline.agent import Zeline
    from zeline import config as _cfg

    if not _cfg.MODEL or not _cfg.API_KEY:
        return (
            "[peer] No model configured on this instance. "
            "Run `zeline model` to set up a provider first."
        )
    agent = Zeline(identity=f"peer:{from_name or 'unknown'}")
    context = f"[Peer message from {from_name or 'unknown'}"
    if reply_to:
        context += f" (replying to {reply_to})"
    context += f"]\n{text}"
    try:
        return agent.send(context)
    except Exception as exc:
        return f"[peer] Error processing message: {exc}"


class PeerServer:
    """HTTP server that receives messages from peer Zeline instances."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        secret: str = "",
        name: str = "",
    ):
        if not secret:
            raise ValueError(
                "peer secret is empty — refusing to serve without auth. "
                "Set peer.secret in config or generate one with "
                "`zeline peer keygen`."
            )
        if not host.strip():
            raise ValueError("peer host is empty — refusing to bind implicitly")
        self.host = host
        self.port = int(port)
        self.secret = secret
        self.name = name or "zeline"
        try:
            from zeline.gateways.webhook import MAX_BODY_BYTES
        except Exception:
            MAX_BODY_BYTES = 32_000
        self._max_body = MAX_BODY_BYTES
        self._server: ThreadingHTTPServer | None = None
        # A2-L1: bound concurrent peer turns — each turn runs the full
        # agent loop and is expensive. 4 concurrent max.
        self._turn_semaphore = threading.Semaphore(4)
        self._turn_acquire_timeout = 30.0

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ZelinePeer/1.0"

            def log_message(self, fmt, *args):  # quiet
                pass

            def _json(self, code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _client_ip(self) -> str:
                return (self.client_address[0] if self.client_address else "unknown")

            def do_GET(self):
                if self.path == "/peer/health":
                    self._json(200, {"ok": True, "service": "zeline-peer"})
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                if self.path != "/peer/message":
                    self._json(404, {"error": "not found"})
                    return
                # Rate limit BEFORE auth: unauthenticated 401 floods must
                # also consume budget, otherwise an attacker can hammer
                # the endpoint with bad secrets indefinitely.
                if not _POST_LIMITER.allow(self._client_ip()):
                    self._json(429, {"error": "rate limited"})
                    return
                if not _is_authorized(self.headers, server.secret):
                    self._json(401, {"error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json(400, {"error": "invalid content length"})
                    return
                if length <= 0:
                    self._json(400, {"error": "empty body"})
                    return
                if length > server._max_body:
                    self._json(413, {"error": "body too large"})
                    return
                try:
                    raw = self.rfile.read(length)
                    body = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._json(400, {"error": "invalid JSON"})
                    return
                if not isinstance(body, dict):
                    self._json(400, {"error": "JSON body must be an object"})
                    return
                text = str(body.get("text", "")).strip()
                from_name = str(body.get("from", ""))[:128]
                reply_to = str(body.get("reply_to", ""))[:128]
                if not text:
                    self._json(400, {"error": "missing 'text'"})
                    return
                if len(text) > 16_000:
                    self._json(400, {"error": "text too long (max 16000 chars)"})
                    return
                # A2-L1: limit concurrent turns; 503 if saturated.
                if not server._turn_semaphore.acquire(timeout=server._turn_acquire_timeout):
                    self._json(503, {"error": "server busy — too many concurrent peer turns"})
                    return
                try:
                    response = _run_peer_turn(from_name, text, reply_to)
                finally:
                    server._turn_semaphore.release()
                self._json(200, {
                    "ok": True,
                    "from": server.name,
                    "response": response,
                })

        return Handler

    def serve_forever(self, ready: Callable[[int], None] | None = None) -> None:
        handler = self._make_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        # If port was 0, report the real one.
        self.port = self._server.server_address[1]
        if ready:
            ready(self.port)
        self._server.serve_forever()

    def shutdown(self) -> None:
        if self._server:
            self._server.shutdown()


def send_to_peer(
    url: str,
    secret: str,
    text: str,
    from_name: str = "",
    reply_to: str = "",
    timeout: int = 120,
) -> dict[str, Any]:
    """Send a message to a peer Zeline instance.

    Returns the parsed JSON response ({"ok": True, "response": ...}).
    Never follows redirects (fail-closed). Raises on transport/auth errors.
    """
    import requests
    from urllib.parse import urlparse

    if not secret:
        raise ValueError("peer secret is empty")
    if not text or not text.strip():
        raise ValueError("text is empty")
    base = url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(
            f"Peer URL must use http(s), got scheme {parsed.scheme!r}."
        )
    host = parsed.hostname or ""
    if _host_is_blocked(host):
        raise ValueError(
            f"Blocked non-public peer host: {host!r} "
            "(SSRF guard — loopback is allowed, RFC1918/link-local/metadata are not)."
        )
    # Rebuild endpoint from scheme+authority only: a caller-supplied path/query
    # (e.g. "http://h:1/foo?bar=1") must not leak into the request target.
    endpoint = f"{parsed.scheme.lower()}://{parsed.netloc}/peer/message"
    headers = {
        "Content-Type": "application/json",
        "X-Zeline-Token": secret,
        "User-Agent": "ZelinePeer/1.0",
    }
    payload = {
        "from": (from_name or "")[:128],
        "text": text[:16_000],
        "reply_to": (reply_to or "")[:128],
    }
    resp = requests.post(
        endpoint, json=payload, headers=headers,
        timeout=timeout, allow_redirects=False,
    )
    if resp.status_code in (301, 302, 303, 307, 308):
        raise ValueError(
            f"Peer at {base} redirected — refusing to follow (fail-closed)."
        )
    if resp.status_code == 401:
        raise ValueError(f"Peer at {base} rejected our secret (401 unauthorized).")
    if resp.status_code == 429:
        raise ValueError(f"Peer at {base} rate-limited us (429).")
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"Peer at {base} returned non-object JSON.")
    return data


def check_peer_health(url: str, timeout: int = 10) -> bool:
    """True if the peer's /peer/health responds OK (no auth needed)."""
    import requests
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url.rstrip("/"))
        if parsed.scheme.lower() not in ("http", "https"):
            return False
        if _host_is_blocked(parsed.hostname or ""):
            return False
        resp = requests.get(
            url.rstrip("/") + "/peer/health",
            timeout=timeout, allow_redirects=False,
        )
        return resp.status_code == 200 and resp.json().get("ok") is True
    except Exception:
        return False


def main_serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> int:
    """CLI entry: start the peer server (blocking)."""
    from zeline import config as _cfg

    secret = str(getattr(_cfg, "PEER_SECRET", "") or "")
    name = str(getattr(_cfg, "NAME", "") or "zeline")
    if not secret:
        print(
            "Peer secret is not configured.\n"
            "Generate one with:  zeline peer keygen\n"
            "Then put it in ~/.zeline/config.json under "
            '{"peer": {"secret": "<secret>"}} and share it with your peer.'
        )
        return 1
    server = PeerServer(host=host, port=port, secret=secret, name=name)
    print(f"[peer] listening on http://{server.host}:{server.port} "
          f"as {server.name!r} (shared-secret auth)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[peer] stopped")
    return 0


def main_send(url: str, message: str) -> int:
    """CLI entry: send one message to a peer and print its reply."""
    from zeline import config as _cfg

    secret = str(getattr(_cfg, "PEER_SECRET", "") or "")
    name = str(getattr(_cfg, "NAME", "") or "zeline")
    if not secret:
        print("Peer secret is not configured. Run: zeline peer keygen")
        return 1
    try:
        data = send_to_peer(url, secret, message, from_name=name)
    except Exception as exc:
        print(f"[peer] send failed: {exc}")
        return 1
    if data.get("ok"):
        print(f"[peer reply from {data.get('from', '?')}]")
        print(data.get("response", ""))
        return 0
    print(f"[peer] peer returned error: {data}")
    return 1
