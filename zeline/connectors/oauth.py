"""OAuth2 authorization-code helpers for connectors.

Phase 1 does not ship an OAuth2 connector yet (GitHub uses a PAT), but the
Google connector in phase 2 builds on exactly these helpers, so they are
written and tested now.
"""
from __future__ import annotations

import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


def build_auth_url(
    auth_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scope: list[str],
    state: str,
) -> str:
    """Build the authorization URL the operator opens in a browser."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scope),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{auth_endpoint}?{urllib.parse.urlencode(params)}"


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self):  # noqa: N802 (http.server naming)
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        codes = params.get("code")
        if codes:
            _CallbackHandler.code = codes[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Connected.</h2><p>You can close this tab.</p></body></html>"
        )

    def log_message(self, *args):  # silence request logging
        pass


def run_local_callback(port: int, timeout: float = 120) -> str | None:
    """Run a one-shot HTTP server and return the ``?code=`` it receives.

    Returns None when nothing arrives before *timeout* seconds.
    """
    _CallbackHandler.code = None
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 0.5
    deadline = time.monotonic() + timeout

    def _serve():
        while time.monotonic() < deadline and _CallbackHandler.code is None:
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    thread.join(timeout + 5)
    server.server_close()
    return _CallbackHandler.code


def exchange_code(
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    """Exchange an authorization code for tokens. Raises on HTTP error."""
    resp = requests.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "expires_in" in data and "expires_at" not in data:
        data["expires_at"] = int(time.time()) + int(data["expires_in"])
    return data


def refresh_access_token(
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """Use a refresh token to get a new access token. Raises on HTTP error."""
    resp = requests.post(
        token_endpoint,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "expires_in" in data and "expires_at" not in data:
        data["expires_at"] = int(time.time()) + int(data["expires_in"])
    # Some providers omit the refresh token on refresh; keep the old one.
    data.setdefault("refresh_token", refresh_token)
    return data


class OAuth2Session:
    """Small session wrapper: holds client config + tokens, auto-refreshes."""

    def __init__(
        self,
        token_endpoint: str,
        client_id: str,
        client_secret: str,
        token: dict | None = None,
    ) -> None:
        self.token_endpoint = token_endpoint
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = dict(token or {})

    def valid_token(self) -> str | None:
        """Return a usable access token, refreshing first when expired.

        Returns None when there is no token or the refresh fails.
        """
        access = self.token.get("access_token")
        if not access:
            return None
        expires_at = self.token.get("expires_at")
        if expires_at and int(expires_at) <= int(time.time()) + 30:
            refresh = self.token.get("refresh_token")
            if not refresh:
                return None
            try:
                self.token = refresh_access_token(
                    self.token_endpoint, self.client_id, self.client_secret, refresh
                )
            except Exception:
                return None
            access = self.token.get("access_token")
        return access

    def auth_header(self) -> dict:
        """``{"Authorization": "Bearer ..."}`` when a token is available."""
        token = self.valid_token()
        return {"Authorization": f"Bearer {token}"} if token else {}
