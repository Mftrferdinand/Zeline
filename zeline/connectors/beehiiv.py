"""Beehiiv connector (API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.beehiiv.com/v2"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class BeehiivConnector(BaseConnector):
    id = "beehiiv"
    name = "Beehiiv"
    description = "Read Beehiiv publication posts."
    auth_kind = "pat"

    def connect(self, api_key: str = "", publication_id: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        publication_id = (publication_id or kwargs.get("publication_id") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        if not publication_id:
            return "ERROR: no publication ID provided."
        try:
            resp = requests.get(
                f"{API_BASE}/publications/{publication_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.beehiiv.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Beehiiv rejected the API key or publication ID (HTTP {resp.status_code})."
        try:
            name = resp.json().get("data", {}).get("name", "?")
        except ValueError:
            name = "?"
        store.save(self.id, {"api_key": api_key, "publication_id": publication_id})
        return f"Connected to Beehiiv publication {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Beehiiv disconnected."
        return "Beehiiv was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        publication_id = data.get("publication_id", "?")
        return {
            "connected": True,
            "detail": f"API key stored (publication {publication_id})",
        }

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Beehiiv is not connected. Run: zeline connect beehiiv")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_key', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Beehiiv API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Beehiiv API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Beehiiv returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_posts(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        self._require_connected()
        data = store.load(self.id) or {}
        publication_id = data.get("publication_id", "")
        payload = self._api(
            "GET",
            f"/publications/{publication_id}/posts",
            params={"limit": limit},
        )
        items = payload.get("data", []) if isinstance(payload, dict) else []
        lines = []
        for post in items[:limit]:
            title = post.get("title", "(no title)")
            status = post.get("status", "?")
            published = post.get("published_at") or post.get("created_at") or "?"
            lines.append(f"{title} [{status}] ({published})")
        return "\n".join(lines) if lines else "No posts found."


def _register() -> BeehiivConnector:
    from zeline.connectors import register

    return register(BeehiivConnector())


_register()
