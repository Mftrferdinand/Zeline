"""Ghost connector (Admin API key + site URL).

Authenticates against the Ghost Admin API with a short-lived HS256 JWT
built from the ``id:secret`` Admin API key, using the standard library
only (``hmac``/``hashlib``/``base64``/``time``/``json``). The signed JWT is
never persisted; only the URL, key id and secret are stored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


def _b64url(data: bytes) -> str:
    """Base64url-encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_jwt(key_id: str, secret_hex: str) -> str:
    """Build a Ghost Admin API JWT (HS256, 5-minute expiry), stdlib only."""
    header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
    now = int(time.time())
    payload = {"iat": now, "exp": now + 300, "aud": "/admin/"}
    head = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    try:
        key = bytes.fromhex(secret_hex)
    except ValueError:
        raise RuntimeError("ERROR: Ghost API secret is not valid hex.") from None
    if not key:
        raise RuntimeError("ERROR: Ghost API secret is empty.")
    signing_input = f"{head}.{body}".encode("ascii")
    signature = _b64url(hmac.new(key, signing_input, hashlib.sha256).digest())
    return f"{head}.{body}.{signature}"


def _normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if "://" not in url:
        url = "https://" + url
    return url


class GhostConnector(BaseConnector):
    id = "ghost"
    name = "Ghost"
    description = "List posts and create drafts on a Ghost publication via the Admin API."
    auth_kind = "pat"

    def connect(self, url: str = "", api_key: str = "", **kwargs) -> str:
        url = _normalize_url(url or kwargs.get("url") or "")
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        if not url or url == "https://":
            return "ERROR: no site URL provided."
        if not api_key:
            return "ERROR: no API key provided."
        if ":" not in api_key:
            return "ERROR: API key must be in id:secret format."
        key_id, secret_hex = (part.strip() for part in api_key.split(":", 1))
        if not key_id or not secret_hex:
            return "ERROR: API key must be in id:secret format."
        try:
            token = _make_jwt(key_id, secret_hex)
        except RuntimeError as exc:
            return str(exc)
        try:
            resp = requests.get(
                f"{url}/ghost/api/admin/posts/?limit=1",
                headers={"Authorization": f"Ghost {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {url} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Ghost rejected the Admin API key (HTTP {resp.status_code})."
        # Never persist the signed JWT itself.
        store.save(self.id, {"url": url, "key_id": key_id, "secret_hex": secret_hex})
        return f"Connected to Ghost at {url}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Ghost disconnected."
        return "Ghost was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("key_id") or not data.get("url"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("url", "?")}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        data = store.load(self.id) or {}
        if not data.get("url") or not data.get("key_id") or not data.get("secret_hex"):
            raise RuntimeError("ERROR: Ghost is not connected. Run: zeline connect ghost")

    def _auth_headers(self) -> dict:
        data = store.load(self.id) or {}
        token = _make_jwt(data.get("key_id", ""), data.get("secret_hex", ""))
        return {"Authorization": f"Ghost {token}"}

    def _api(self, method: str, path: str, **kwargs):
        self._require_connected()
        data = store.load(self.id) or {}
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._auth_headers())
        try:
            resp = requests.request(
                method, f"{data['url']}/ghost/api/admin{path}", headers=headers, **kwargs
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Ghost API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Ghost API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Ghost returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_posts(self, limit: int = 10) -> str:
        """READ: list posts (title, slug, status) on the Ghost site."""
        limit = _clamp(limit)
        body = self._api(
            "GET", "/posts/", params={"limit": limit, "fields": "title,slug,status"}
        )
        posts = (body.get("posts") or []) if isinstance(body, dict) else []
        lines = [
            f"{post.get('slug', '?')}: {post.get('title', '(untitled)')} [{post.get('status', '?')}]"
            for post in posts[:limit]
        ]
        return "\n".join(lines) if lines else "No posts found."

    def create_post(self, title: str, html: str = "") -> str:
        """NETWORK: create a draft post on the Ghost site."""
        created = self._api(
            "POST",
            "/posts/",
            json={"posts": [{"title": title, "html": html, "status": "draft"}]},
        )
        posts = (created.get("posts") or []) if isinstance(created, dict) else []
        slug = posts[0].get("slug", "?") if posts else "?"
        return f"Draft created: {slug}"


def _register() -> GhostConnector:
    from zeline.connectors import register

    return register(GhostConnector())


_register()
