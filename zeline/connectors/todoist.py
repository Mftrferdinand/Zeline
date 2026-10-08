"""Todoist connector (API token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.todoist.com/api/v1"
_TIMEOUT = 30


class TodoistConnector(BaseConnector):
    id = "todoist"
    name = "Todoist"
    description = "List and create Todoist tasks."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.todoist.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Todoist rejected the token (HTTP {resp.status_code})."
        try:
            user = resp.json() or {}
        except ValueError:
            return "ERROR: Todoist returned an unreadable response."
        who = user.get("email") or user.get("name") or "?"
        store.save(self.id, {"token": token, "user": who})
        return f"Connected to Todoist as {who}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Todoist disconnected."
        return "Todoist was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("user", "?")}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('token', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Todoist API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Todoist API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_tasks(self, limit: int = 10) -> str:
        payload = self._api("GET", "/tasks", params={"limit": max(1, min(limit, 100))})
        tasks = payload.get("results") if isinstance(payload, dict) else payload
        lines = []
        for task in (tasks or [])[:limit]:
            content = str(task.get("content", "")).strip().replace("\n", " ")
            priority = task.get("priority", 1)
            lines.append(f"• {content} [P{priority}] ({task.get('id', '?')})")
        return "\n".join(lines) if lines else "No tasks found."

    def add_task(self, content: str, description: str = "", priority: int = 1) -> str:
        content = (content or "").strip()
        if not content:
            return "ERROR: task content is required."
        task = self._api(
            "POST",
            "/tasks",
            json={
                "content": content,
                "description": description or "",
                "priority": max(1, min(priority, 4)),
            },
        )
        return f"Task dibuat: {task.get('id', '?')}."


def _register() -> TodoistConnector:
    from zeline.connectors import register

    return register(TodoistConnector())


_register()
