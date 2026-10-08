"""GitBook connector (API token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.gitbook.com/v1"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class GitBookConnector(BaseConnector):
    id = "gitbook"
    name = "GitBook"
    description = "List GitBook spaces and browse their content."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("api_token") or kwargs.get("token") or "").strip()
        if not api_token:
            return "ERROR: no API token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user",
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.gitbook.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: GitBook rejected the token (HTTP {resp.status_code})."
        try:
            user = resp.json()
        except ValueError:
            return "ERROR: GitBook returned an unreadable response."
        username = user.get("displayName") or user.get("name", "?")
        store.save(self.id, {"api_token": api_token, "user": username})
        return f"Connected to GitBook as {username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "GitBook disconnected."
        return "GitBook was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("user", "?")}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        data = store.load(self.id) or {}
        if not data.get("api_token"):
            raise RuntimeError("ERROR: GitBook is not connected. Run: zeline connect gitbook")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_token', '')}"}

    def _api(self, method: str, path: str, **kwargs):
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: GitBook API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: GitBook API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: GitBook returned an unreadable response.") from None

    @staticmethod
    def _items(body) -> list:
        if isinstance(body, dict):
            return body.get("items") or []
        return body or []

    # -- user-facing operations -------------------------------------------

    def list_spaces(self, limit: int = 10) -> str:
        """READ: list the GitBook spaces visible to the token."""
        limit = _clamp(limit)
        body = self._api("GET", "/spaces", params={"limit": limit})
        lines = [
            f"{space.get('id', '?')}: {space.get('title', '(untitled)')}"
            for space in self._items(body)[:limit]
        ]
        return "\n".join(lines) if lines else "No spaces found."

    def list_content(self, space_id: str, limit: int = 10) -> str:
        """READ: list pages inside one GitBook space."""
        limit = _clamp(limit)
        body = self._api("GET", f"/spaces/{space_id}/content", params={"limit": limit})
        lines = [
            f"{page.get('id', '?')}: {page.get('title', '(untitled)')} ({page.get('type', '?')})"
            for page in self._items(body)[:limit]
        ]
        return "\n".join(lines) if lines else f"No content found in space {space_id}."


def _register() -> GitBookConnector:
    from zeline.connectors import register

    return register(GitBookConnector())


_register()
