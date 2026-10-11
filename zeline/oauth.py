"""One-click OAuth2 framework for Zeline connectors.

Generic authorization-code + PKCE (S256) flow. Provider-specific details live
in :data:`OAUTH_PROVIDERS`; the flow itself is provider-agnostic, so adding a
new provider is a config entry, not new code.

Flows
-----
CLI (``zeline connect github``):

1. prints the provider authorization URL — the operator opens it in a browser
2. runs a one-shot localhost callback server and waits for the redirect
3. exchanges the code for tokens and stores them (0600) via
   ``zeline.connectors.store``

Webchat: ``GET /oauth/callback?code=...&state=...`` is routed by the webchat
gateway into :func:`handle_oauth_callback`.

Security notes
--------------
- ``state`` is a fresh random token per flow, validated on callback (CSRF).
- PKCE S256 is always used; the verifier never leaves this process except
  in the token exchange over TLS.
- App credentials (client_id/secret) live in
  ``~/.zeline/oauth-apps/<provider>.json`` (0600) or the
  ``ZELINE_OAUTH_<PROVIDER>_CLIENT_ID`` / ``_CLIENT_SECRET`` env vars.
  They are never logged; token values are never printed.
- Access/refresh tokens are stored via ``connectors.store`` (0600).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import requests

from zeline.connectors import store

_TIMEOUT = 30
_STATE_TTL = 600  # seconds a pending flow stays valid
_MAX_TOKEN_BYTES = 1024 * 1024  # sanity cap on token responses

# ---------------------------------------------------------------------------
# Provider registry — config, not code. Add a provider by adding an entry.
# ---------------------------------------------------------------------------

OAUTH_PROVIDERS: dict[str, dict[str, Any]] = {
    "github": {
        "auth_endpoint": "https://github.com/login/oauth/authorize",
        "token_endpoint": "https://github.com/login/oauth/access_token",
        "scopes": ["read:user", "repo"],
        "needs_secret": True,
        # GitHub returns urlencoded unless asked for JSON.
        "token_headers": {"Accept": "application/json"},
    },
    "google": {
        "auth_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "scopes": ["openid", "email", "profile"],
        "needs_secret": True,
        "extra_auth_params": {"access_type": "offline", "prompt": "consent"},
    },
    "slack": {
        "auth_endpoint": "https://slack.com/oauth/v2/authorize",
        "token_endpoint": "https://slack.com/api/oauth.v2.access",
        "scopes": ["chat:write", "channels:read"],
        "needs_secret": True,
    },
    "discord": {
        "auth_endpoint": "https://discord.com/oauth2/authorize",
        "token_endpoint": "https://discord.com/api/oauth2/token",
        "scopes": ["identify"],
        "needs_secret": True,
    },
    "notion": {
        # Notion has no granular OAuth scopes — permissions are configured on
        # the integration itself; keep scopes empty and omit the param.
        "auth_endpoint": "https://api.notion.com/v1/oauth/authorize",
        "token_endpoint": "https://api.notion.com/v1/oauth/token",
        "scopes": [],
        "needs_secret": True,
        # Notion requires HTTP Basic auth (client_id:client_secret) plus a
        # JSON body, and owner=user on the authorization URL.
        "token_basic_auth": True,
        "token_json_body": True,
        "extra_auth_params": {"owner": "user"},
    },
    "linear": {
        # Token endpoint per official docs (api.linear.app, bukan linear.app).
        "auth_endpoint": "https://linear.app/oauth/authorize",
        "token_endpoint": "https://api.linear.app/oauth/token",
        "scopes": ["read"],
        "needs_secret": True,
    },
    "gitlab": {
        "auth_endpoint": "https://gitlab.com/oauth/authorize",
        "token_endpoint": "https://gitlab.com/oauth/token",
        "scopes": ["read_user", "read_api"],
        "needs_secret": True,
    },
    "spotify": {
        # Spotify requires HTTP Basic auth on the token endpoint.
        "auth_endpoint": "https://accounts.spotify.com/authorize",
        "token_endpoint": "https://accounts.spotify.com/api/token",
        "scopes": ["user-read-private"],
        "needs_secret": True,
        "token_basic_auth": True,
    },
    "x": {
        # X (Twitter) OAuth 2.0 mandates PKCE S256 — always sent by the flow.
        "auth_endpoint": "https://twitter.com/i/oauth2/authorize",
        "token_endpoint": "https://api.twitter.com/2/oauth2/token",
        "scopes": ["tweet.read", "users.read"],
        "needs_secret": True,
        "token_basic_auth": True,
    },
}


def supports_oauth(connector_id: str) -> bool:
    """True when *connector_id* has an OAuth provider config."""
    return connector_id in OAUTH_PROVIDERS


# ---------------------------------------------------------------------------
# App credentials (the OAuth "client" this Zeline instance registers)
# ---------------------------------------------------------------------------


def _apps_dir() -> Path:
    path = Path.home() / ".zeline" / "oauth-apps"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


# Cache kredensial app (60s TTL) — app_credentials() dipanggil di setiap
# exchange/refresh; tanpa cache tiap panggilan = mkdir + read + JSON parse.
# Fingerprint (path + mtime file + env) bikin cache otomatis invalid kalau
# HOME/env/file berubah di antara panggilan (penting untuk test isolation).
_CREDS_TTL = 60.0
_creds_cache: dict = {"data": {}}


def _creds_fingerprint(provider: str) -> tuple:
    path = _apps_dir() / f"{provider}.json"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    env_cid = os.environ.get(f"ZELINE_OAUTH_{provider.upper()}_CLIENT_ID", "")
    return (str(path), mtime, env_cid)


def app_credentials(provider: str) -> tuple[str, str | None]:
    """Return ``(client_id, client_secret)`` for *provider*.

    Lookup order: ``~/.zeline/oauth-apps/<provider>.json`` then
    ``ZELINE_OAUTH_<PROVIDER>_CLIENT_ID`` / ``_CLIENT_SECRET`` env vars.
    Returns ``("", None)`` when not configured.

    Hasil di-cache 60 detik — fungsi ini dipanggil di setiap exchange/refresh.
    """
    now = time.monotonic()
    fp = _creds_fingerprint(provider)
    cached = _creds_cache["data"].get(provider)
    if (
        cached is not None
        and now - cached[2] < _CREDS_TTL
        and cached[0] == fp
    ):
        return cached[1]
    result = _read_app_credentials(provider)
    _creds_cache["data"][provider] = (fp, result, now)
    return result


def _invalidate_app_credentials(provider: str) -> None:
    _creds_cache["data"].pop(provider, None)


def _read_app_credentials(provider: str) -> tuple[str, str | None]:
    upper = provider.upper()
    try:
        data = json.loads((_apps_dir() / f"{provider}.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("credentials file is not a JSON object")
        cid = str(data.get("client_id", "")).strip()
        csec = data.get("client_secret")
        if cid:
            return cid, (str(csec) if csec else None)
    except (OSError, ValueError):
        pass
    cid = os.environ.get(f"ZELINE_OAUTH_{upper}_CLIENT_ID", "").strip()
    csec = os.environ.get(f"ZELINE_OAUTH_{upper}_CLIENT_SECRET")
    return cid, (csec if csec else None)


def save_app_credentials(provider: str, client_id: str, client_secret: str | None) -> Path:
    """Persist app credentials for *provider* with mode 0600.

    The tmp file is created with O_CREAT|O_EXCL and mode 0600 from the very
    first byte — no chmod-after-write window where secrets are world-readable.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", provider or ""):
        raise ValueError(f"nama provider tidak valid: {provider!r}")
    path = _apps_dir() / f"{provider}.json"
    payload: dict[str, Any] = {"client_id": client_id}
    if client_secret:
        payload["client_secret"] = client_secret
    data = json.dumps(payload, indent=2).encode("utf-8")
    tmp = path.with_suffix(".json.tmp")
    # O_EXCL: jangan pernah menimpa file tmp yang sudah ada (race safety).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    # Pertahankan 0600 juga kalau umask/file-system mengabaikan mode di open.
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    # Kredensial berubah — jangan pakai cache lama.
    _invalidate_app_credentials(provider)
    return path


# ---------------------------------------------------------------------------
# PKCE + state
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` using S256."""
    verifier = secrets.token_urlsafe(64)  # ~86 chars, within 43..128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# state -> {"connector_id", "provider", "verifier", "redirect_uri", "created"}
_PENDING: dict[str, dict[str, Any]] = {}
_PENDING_LOCK = threading.Lock()
_PENDING_MAX = 1000  # hard cap — evict tertua kalau penuh (DoS guard)


def _prune_pending() -> None:
    now = time.time()
    expired = [s for s, p in _PENDING.items() if now - p["created"] > _STATE_TTL]
    for s in expired:
        _PENDING.pop(s, None)


def _new_state(connector_id: str, provider: str, verifier: str, redirect_uri: str) -> str:
    state = secrets.token_urlsafe(32)
    with _PENDING_LOCK:
        _prune_pending()
        if len(_PENDING) >= _PENDING_MAX:
            # Evict yang tertua dulu (state 10-menit, jadi ini sangat jarang).
            for s in sorted(_PENDING, key=lambda k: _PENDING[k]["created"])[: len(_PENDING) - _PENDING_MAX + 1]:
                _PENDING.pop(s, None)
        _PENDING[state] = {
            "connector_id": connector_id,
            "provider": provider,
            "verifier": verifier,
            "redirect_uri": redirect_uri,
            "created": time.time(),
        }
    return state


def _take_pending(state: str | None) -> dict[str, Any] | None:
    """Pop and return the pending flow for *state* (None if unknown/expired)."""
    if not state:
        return None
    with _PENDING_LOCK:
        _prune_pending()
        return _PENDING.pop(state, None)


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


def _token_request_kwargs(
    cfg: dict[str, Any],
    client_id: str,
    client_secret: str | None,
    fields: dict[str, str],
) -> dict[str, Any]:
    """Build ``requests.post`` kwargs for token-endpoint calls.

    ``token_basic_auth``: authenticate with HTTP Basic
    ``client_id:client_secret`` instead of the body (Notion, Spotify, X).
    When Basic is used, the credentials are removed from the body so the
    secret is only sent in the ``Authorization`` header.
    ``token_json_body``: send the body as JSON (Notion) instead of form.
    """
    fields = dict(fields)
    kwargs: dict[str, Any] = {}
    if cfg.get("token_basic_auth"):
        # Drop client_secret dan client_id dari body juga — Basic header
        # sudah membawanya (satu metode auth per request, per RFC 6749 2.3.1).
        fields.pop("client_secret", None)
        fields.pop("client_id", None)
        kwargs["auth"] = (client_id, client_secret) if client_secret else None
    if cfg.get("token_json_body"):
        kwargs["json"] = fields
    else:
        kwargs["data"] = fields
    return kwargs


def start_oauth_flow(
    connector_id: str,
    redirect_uri: str,
    scopes: list[str] | None = None,
) -> str:
    """Build the provider authorization URL and register the pending flow.

    Raises ``ValueError`` for unknown providers or missing app credentials.
    The returned URL is safe to show to the operator.
    """
    provider = connector_id if connector_id in OAUTH_PROVIDERS else None
    if provider is None:
        raise ValueError(f"connector '{connector_id}' has no OAuth provider config")
    # redirect_uri hanya boleh localhost — jangan biarkan token code dikirim
    # ke host eksternal.
    if not (
        redirect_uri.startswith("http://127.0.0.1:")
        or redirect_uri.startswith("http://localhost:")
    ):
        raise ValueError(
            f"redirect_uri must be a localhost URL, got: {redirect_uri!r}"
        )
    cfg = OAUTH_PROVIDERS[provider]
    client_id, _ = app_credentials(provider)
    if not client_id:
        raise ValueError(
            f"OAuth app not configured for '{provider}'. "
            f"Run 'zeline connect {connector_id}' and enter the client ID, "
            f"or set ZELINE_OAUTH_{provider.upper()}_CLIENT_ID."
        )
    verifier, challenge = _pkce_pair()
    state = _new_state(connector_id, provider, verifier, redirect_uri)
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    # Provider tanpa scope (Notion): jangan kirim param scope kosong.
    scope = " ".join(scopes or cfg["scopes"])
    if scope:
        params["scope"] = scope
    for k, v in cfg.get("extra_auth_params", {}).items():
        params.setdefault(k, v)
    return f"{cfg['auth_endpoint']}?{urllib.parse.urlencode(params)}"


def _exchange_code(
    provider: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """POST the code (+ PKCE verifier) to the token endpoint. Raises on error."""
    cfg = OAUTH_PROVIDERS[provider]
    client_id, client_secret = app_credentials(provider)
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    headers = {"Accept": "application/json"}
    headers.update(cfg.get("token_headers", {}))
    kwargs = _token_request_kwargs(cfg, client_id, client_secret, data)
    try:
        resp = requests.post(cfg["token_endpoint"], headers=headers, timeout=_TIMEOUT, stream=True, **kwargs)
    except Exception as exc:
        # Network error -> ValueError agar konsisten dengan error lain
        # di fungsi ini (caller menangkap Exception anyway).
        raise ValueError(f"token exchange failed: {exc.__class__.__name__}: {exc}")
    try:
        # Baca incremental dengan cap — jangan download semua dulu baru cek.
        body = b""
        for chunk in resp.iter_content(chunk_size=65536):
            body += chunk
            if len(body) > _MAX_TOKEN_BYTES:
                raise ValueError("token endpoint returned an oversized response")
    finally:
        resp.close()
    if resp.status_code >= 400:
        raise ValueError(f"token exchange failed (HTTP {resp.status_code})")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("token endpoint did not return JSON") from exc
    if not isinstance(payload, dict) or "access_token" not in payload:
        err = payload.get("error_description") or payload.get("error") if isinstance(payload, dict) else None
        raise ValueError(f"token exchange failed: {err or 'no access_token'}")
    return payload


def handle_oauth_callback(
    code: str | None,
    state: str | None,
    error: str | None = None,
) -> tuple[bool, str]:
    """Validate *state*, exchange *code*, store tokens.

    Returns ``(ok, message)`` — the message is safe to show (no secrets).
    """
    if error:
        return False, f"Provider refused authorization ({error})."
    pending = _take_pending(state)
    if pending is None:
        return False, "Unknown or expired login session. Please start over with 'zeline connect'."
    if not code:
        return False, "No authorization code received."
    provider = pending["provider"]
    connector_id = pending["connector_id"]
    try:
        payload = _exchange_code(provider, code, pending["verifier"], pending["redirect_uri"])
    except Exception as exc:  # network / provider error -> clean message
        return False, f"Could not complete login: {exc}"
    stored: dict[str, Any] = {
        "auth_kind": "oauth2",
        "provider": provider,
        "access_token": payload["access_token"],
        "token_type": payload.get("token_type", "Bearer"),
        "scope": payload.get("scope", ""),
        "obtained_at": int(time.time()),
    }
    if payload.get("refresh_token"):
        stored["refresh_token"] = payload["refresh_token"]
    if payload.get("expires_in"):
        try:
            stored["expires_at"] = int(time.time()) + int(payload["expires_in"])
        except (TypeError, ValueError):
            pass
    try:
        store.save(connector_id, stored)
    except OSError as exc:
        return False, f"Could not save tokens: {exc}"
    return True, f"Connected {connector_id} via OAuth."


def refresh_if_needed(connector_id: str) -> bool:
    """Refresh the access token when expired and a refresh token exists.

    Returns True when the stored token is usable (fresh or refreshed).
    """
    data = store.load(connector_id) or {}
    if data.get("auth_kind") != "oauth2" or not data.get("access_token"):
        return False
    expires_at = data.get("expires_at")
    if expires_at and int(time.time()) < int(expires_at) - 60:
        return True  # still fresh
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        return not bool(expires_at)  # non-expiring token stays usable
    provider = data.get("provider", connector_id)
    cfg = OAUTH_PROVIDERS.get(provider)
    if cfg is None:
        return False
    client_id, client_secret = app_credentials(provider)
    body: dict[str, str] = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if client_secret:
        body["client_secret"] = client_secret
    headers = {"Accept": "application/json", **cfg.get("token_headers", {})}
    kwargs = _token_request_kwargs(cfg, client_id, client_secret, body)
    try:
        resp = requests.post(
            cfg["token_endpoint"],
            headers=headers,
            timeout=_TIMEOUT,
            stream=True,
            **kwargs,
        )
    except Exception:
        return False
    try:
        raw = b""
        for chunk in resp.iter_content(chunk_size=65536):
            raw += chunk
            if len(raw) > _MAX_TOKEN_BYTES:
                return False
        if resp.status_code >= 400:
            return False
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return False
    finally:
        # Selalu tutup — sebelumnya bocor kalau iter_content raise.
        resp.close()
    if not isinstance(payload, dict) or "access_token" not in payload:
        return False
    data["access_token"] = payload["access_token"]
    data["obtained_at"] = int(time.time())
    if payload.get("refresh_token"):
        data["refresh_token"] = payload["refresh_token"]
    if payload.get("expires_in"):
        try:
            data["expires_at"] = int(time.time()) + int(payload["expires_in"])
        except (TypeError, ValueError):
            pass
    store.save(connector_id, data)
    return True


def callback_html(ok: bool, message: str) -> str:
    """Small result page for the webchat ``/oauth/callback`` route."""
    safe = html.escape(message)
    title = "Connected" if ok else "Login failed"
    color = "#1a8a5b" if ok else "#b3261e"
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{title}</title></head>"
        "<body style=\"font-family:system-ui,sans-serif;display:flex;align-items:center;"
        "justify-content:center;min-height:90vh;margin:0;background:#fff;color:#1a1a2e\">"
        "<div style=\"text-align:center;max-width:420px;padding:24px\">"
        f"<h2 style=\"color:{color}\">{title}</h2>"
        f"<p>{safe}</p>"
        "<p style=\"color:#5b5b7a;font-size:14px\">You can close this tab.</p>"
        "</div></body></html>"
    )


# ---------------------------------------------------------------------------
# One-shot localhost callback server (CLI flow)
# ---------------------------------------------------------------------------


class _OneShotHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server naming)
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        # Instance attribute di server — bukan class attribute (race).
        self.server.oauth_result = {  # type: ignore[attr-defined]
            "code": (params.get("code") or [None])[0],
            "state": (params.get("state") or [None])[0],
            "error": (params.get("error") or [None])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Connected.</h2><p>You can close this tab.</p></body></html>"
        )

    def log_message(self, *args):  # silence request logging
        pass


def _wait_for_callback(server: HTTPServer, timeout: float) -> dict[str, Any] | None:
    """Serve one redirect on an already-bound server; return params or None on timeout."""
    server.oauth_result = None  # type: ignore[attr-defined]
    server.timeout = 0.5
    deadline = time.monotonic() + timeout

    def _serve() -> None:
        while time.monotonic() < deadline and server.oauth_result is None:  # type: ignore[attr-defined]
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    thread.join(timeout + 5)
    server.server_close()
    return server.oauth_result  # type: ignore[attr-defined]


def _ensure_app_credentials(provider: str, needs_secret: bool) -> tuple[str, str | None] | None:
    """Return app credentials, prompting interactively when missing."""
    client_id, client_secret = app_credentials(provider)
    if client_id and (client_secret or not needs_secret):
        return client_id, client_secret
    print(f"OAuth app credentials for '{provider}' are not configured.")
    print("Register an OAuth app with the provider, then paste the credentials here.")
    print("(They are stored in ~/.zeline/oauth-apps/ with mode 0600.)")
    try:
        client_id = input("Client ID: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not client_id:
        print("No client ID given — aborting.")
        return None
    client_secret = None
    if needs_secret:
        try:
            import getpass

            client_secret = getpass.getpass("Client secret: ").strip() or None
        except (EOFError, KeyboardInterrupt):
            return None
    save_app_credentials(provider, client_id, client_secret)
    print("Credentials saved.")
    return client_id, client_secret


def run_oauth_flow_cli(connector_id: str, timeout: float = 180) -> int:
    """Full one-click OAuth flow for the CLI. Returns a process exit code."""
    if not supports_oauth(connector_id):
        print(f"Connector '{connector_id}' does not support OAuth.")
        return 2
    cfg = OAUTH_PROVIDERS[connector_id]
    creds = _ensure_app_credentials(connector_id, cfg["needs_secret"])
    if creds is None:
        return 1
    # Ephemeral localhost port for the redirect. Bind SEKALI dan pakai socket
    # yang sama untuk server callback — jangan close lalu re-bind (TOCTOU race:
    # port bisa direbut proses lain di antaranya).
    server = HTTPServer(("127.0.0.1", 0), _OneShotHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    try:
        auth_url = start_oauth_flow(connector_id, redirect_uri)
    except ValueError as exc:
        server.server_close()
        print(f"ERROR: {exc}")
        return 1
    print()
    print("Open this URL in your browser to connect:")
    print()
    print(f"  {auth_url}")
    print()
    print(f"Waiting for the login callback (up to {int(timeout)}s)…")
    params = _wait_for_callback(server, timeout)
    if params is None:
        print("Timed out waiting for the browser callback.")
        return 1
    ok, message = handle_oauth_callback(params.get("code"), params.get("state"), params.get("error"))
    print(message)
    return 0 if ok else 1
