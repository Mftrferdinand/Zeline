"""X API connector (bearer token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.twitter.com/2"
_TIMEOUT = 30


class XApiConnector(BaseConnector):
    id = "x_api"
    name = "X API"
    description = "Post tweets and read timelines via X API v2."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/users/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.twitter.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: X API rejected the token (HTTP {resp.status_code})."
        try:
            username = (resp.json().get("data") or {}).get("username", "?")
        except ValueError:
            return "ERROR: X API returned an unreadable response."
        store.save(self.id, {"token": token, "username": username})
        return f"Connected to X as @{username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "X API disconnected."
        return "X API was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('username', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('token', '')}"}

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: X API is not connected. Run: zeline connect x_api")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: X API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: X API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def post_tweet(self, text: str) -> str:
        tweet = self._api("POST", "/tweets", json={"text": text})
        tweet_id = (tweet.get("data") or {}).get("id", "?")
        return f"Tweet posted: https://x.com/i/status/{tweet_id}"

    def read_timeline(self, username: str, limit: int = 10) -> str:
        user = self._api("GET", f"/users/by/username/{username}")
        user_id = (user.get("data") or {}).get("id")
        if not user_id:
            raise RuntimeError(f"ERROR: X API could not find user @{username}.")
        payload = self._api(
            "GET",
            f"/users/{user_id}/tweets",
            params={"max_results": max(1, min(limit, 100)), "tweet.fields": "created_at"},
        )
        tweets = (payload.get("data") or [])[:limit]
        lines = [f"{tweet.get('created_at', '?')} — {tweet.get('text', '')}" for tweet in tweets]
        return "\n".join(lines) if lines else "No tweets found."


def _register() -> XApiConnector:
    from zeline.connectors import register

    return register(XApiConnector())


_register()
