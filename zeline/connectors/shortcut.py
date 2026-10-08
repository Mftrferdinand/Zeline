"""Shortcut connector (personal API token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.app.shortcut.com/api/v3"
_TIMEOUT = 30


class ShortcutConnector(BaseConnector):
    id = "shortcut"
    name = "Shortcut"
    description = "List and create stories in Shortcut (ex-Clubhouse)."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("api_token") or "").strip()
        if not api_token:
            return "ERROR: api_token is required."
        try:
            resp = requests.get(
                f"{API_BASE}/member",
                headers={"Shortcut-Token": api_token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {API_BASE} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Shortcut rejected the token (HTTP {resp.status_code})."
        try:
            name = resp.json().get("name", "?")
        except ValueError:
            return "ERROR: Shortcut returned an unreadable response."
        store.save(self.id, {"api_token": api_token, "name": name})
        return f"Connected to Shortcut as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Shortcut disconnected."
        return "Shortcut was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("name", "?")}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Shortcut-Token": data.get("api_token", "")}

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Shortcut is not connected. Run: zeline connect shortcut")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Shortcut API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Shortcut API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_stories(self, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        data = self._api("POST", "/stories/search", json={})
        stories = data.get("data", []) if isinstance(data, dict) else []
        lines = []
        for story in stories[:limit]:
            sid = story.get("id", "?")
            name = story.get("name", "-")
            story_type = story.get("story_type", "-")
            state = story.get("workflow_state_name", "-")
            lines.append(f"#{sid} {name} [{story_type}/{state}]")
        return "\n".join(lines) if lines else "No stories found."

    def create_story(self, name: str, description: str = "", story_type: str = "feature") -> str:
        story = self._api(
            "POST", "/stories",
            json={"name": name, "description": description, "story_type": story_type},
        )
        return f"Story created: {story.get('app_url', story.get('id', '?'))}"


def _register() -> ShortcutConnector:
    from zeline.connectors import register

    return register(ShortcutConnector())


_register()
