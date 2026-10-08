"""ClickUp connector (personal API token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.clickup.com/api/v2"
_TIMEOUT = 30


class ClickUpConnector(BaseConnector):
    id = "clickup"
    name = "ClickUp"
    description = "List tasks and create tasks in ClickUp lists."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user",
                headers={"Authorization": token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.clickup.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: ClickUp rejected the token (HTTP {resp.status_code})."
        try:
            username = resp.json().get("user", {}).get("username", "?")
        except ValueError:
            return "ERROR: ClickUp returned an unreadable response."
        store.save(self.id, {"token": token, "username": username})
        return f"Connected to ClickUp as @{username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "ClickUp disconnected."
        return "ClickUp was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('username', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> dict:
        data = store.load(self.id) or {}
        if not data.get("token"):
            raise RuntimeError("ERROR: ClickUp is not connected. Run 'zeline connect clickup' first.")
        return data

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update({"Authorization": self._require_connected()["token"]})
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: ClickUp API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: ClickUp API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_tasks(self, list_id: str, limit: int = 10) -> str:
        list_id = (list_id or "").strip()
        if not list_id:
            return "ERROR: no list_id provided."
        data = self._api("GET", f"/list/{list_id}/task", params={"limit": max(1, min(limit, 100))})
        lines = []
        for task in data.get("tasks", [])[:limit]:
            name = task.get("name", "?")
            status = task.get("status", {}).get("status", "?")
            lines.append(f"{name} [{status}]")
        return "\n".join(lines) if lines else f"No tasks in list {list_id}."

    def create_task(self, list_id: str, name: str, description: str = "") -> str:
        list_id = (list_id or "").strip()
        name = (name or "").strip()
        if not list_id:
            return "ERROR: no list_id provided."
        if not name:
            return "ERROR: no task name provided."
        payload: dict = {"name": name}
        if description:
            payload["description"] = description
        task = self._api("POST", f"/list/{list_id}/task", json=payload)
        return f"Task created: {task.get('id', '?')}"


def _register() -> ClickUpConnector:
    from zeline.connectors import register

    return register(ClickUpConnector())


_register()
