"""Asana connector (personal access token, Bearer auth).

Talks to the Asana REST API v1 at ``https://app.asana.com/api/1.0``.
"""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://app.asana.com/api/1.0"
_TIMEOUT = 30


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), 100))


class AsanaConnector(BaseConnector):
    id = "asana"
    name = "Asana"
    description = "List and create Asana tasks."
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
            return f"ERROR: could not reach app.asana.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Asana rejected the token (HTTP {resp.status_code})."
        try:
            name = resp.json().get("data", {}).get("name", "?")
        except ValueError:
            return "ERROR: Asana returned an unreadable response."
        store.save(self.id, {"token": token, "user": name})
        return f"Connected to Asana as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Asana disconnected."
        return "Asana was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("user", "linked")}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        token = data.get("token", "")
        if not token:
            raise RuntimeError("ERROR: Asana is not connected. Run 'zeline connect asana' first.")
        return {"Authorization": f"Bearer {token}"}

    def _api(self, method: str, path: str, **kwargs) -> dict:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Asana API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Asana API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_tasks(self, limit: int = 10, assignee: str = "me") -> str:
        """READ. List tasks as ``name [due] (done/open)`` lines."""
        assignee = (assignee or "me").strip() or "me"
        data = self._api(
            "GET",
            "/tasks",
            params={
                "assignee": assignee,
                "limit": _clamp_limit(limit),
                "opt_fields": "name,due_on,completed",
            },
        )
        tasks = data.get("data", []) if isinstance(data, dict) else []
        lines = []
        for task in tasks[:limit]:
            due = task.get("due_on") or "no due date"
            state = "done" if task.get("completed") else "open"
            lines.append(f"{task.get('name', '?')} [{due}] ({state})")
        return "\n".join(lines) if lines else "No tasks found."

    def create_task(self, name: str, notes: str = "", workspace: str = "") -> str:
        """NETWORK. Create a task. Returns the created task's gid."""
        name = (name or "").strip()
        notes = (notes or "").strip()
        workspace = (workspace or "").strip()
        if not name:
            return "ERROR: no task name provided."
        fields = {"name": name}
        if notes:
            fields["notes"] = notes
        if workspace:
            fields["workspace"] = workspace
        task = self._api("POST", "/tasks", json={"data": fields})
        data = task.get("data", {}) if isinstance(task, dict) else {}
        return f"Created task {data.get('gid', '?')}."


def _register() -> AsanaConnector:
    from zeline.connectors import register

    return register(AsanaConnector())


_register()
