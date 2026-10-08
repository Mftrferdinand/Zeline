"""Wrike connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://www.wrike.com/api/v4"
_TIMEOUT = 30


class WrikeConnector(BaseConnector):
    id = "wrike"
    name = "Wrike"
    description = "List and create tasks in Wrike."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: token is required."
        try:
            resp = requests.get(
                f"{API_BASE}/account",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach www.wrike.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Wrike rejected the token (HTTP {resp.status_code})."
        try:
            name = resp.json()["data"][0].get("name", "?")
        except (ValueError, KeyError, IndexError, AttributeError):
            return "ERROR: Wrike returned an unreadable response."
        store.save(self.id, {"token": token, "account": name})
        return f"Connected to Wrike (account {name})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Wrike disconnected."
        return "Wrike was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"account {data.get('account', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('token', '')}"}

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Wrike is not connected. Run: zeline connect wrike")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Wrike API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Wrike API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_tasks(self, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        payload = self._api("GET", "/tasks", params={"pageSize": limit})
        tasks = payload.get("data", []) if isinstance(payload, dict) else []
        lines = []
        for task in tasks[:limit]:
            lines.append(f"{task.get('title', '?')} [{task.get('status', '?')}]")
        return "\n".join(lines) if lines else "No tasks found."

    def create_task(self, title: str, folder_id: str, description: str = "") -> str:
        result = self._api(
            "POST",
            f"/folders/{folder_id}/tasks",
            json={"title": title, "description": description},
        )
        task_id = result["data"][0]["id"]
        return f"Task created: {task_id}"


def _register() -> WrikeConnector:
    from zeline.connectors import register

    return register(WrikeConnector())


_register()
