"""Teamwork connector (API token + subdomain)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _normalize_subdomain(subdomain: str) -> str:
    """Strip whitespace and trailing slashes from a Teamwork subdomain."""
    return (subdomain or "").strip().rstrip("/")


class TeamworkConnector(BaseConnector):
    id = "teamwork"
    name = "Teamwork"
    description = "List projects and tasks in Teamwork."
    auth_kind = "pat"

    def connect(self, api_token: str = "", subdomain: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("api_token") or "").strip()
        subdomain = _normalize_subdomain(subdomain or kwargs.get("subdomain") or "")
        if not api_token or not subdomain:
            return "ERROR: api_token and subdomain are required."
        base = f"https://{subdomain}.teamwork.com"
        try:
            resp = requests.get(
                f"{base}/projects/api/v3/me.json",
                auth=(api_token, ""),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {subdomain}.teamwork.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Teamwork rejected the credentials (HTTP {resp.status_code})."
        store.save(self.id, {"api_token": api_token, "subdomain": subdomain})
        return f"Connected to Teamwork ({subdomain})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Teamwork disconnected."
        return "Teamwork was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("subdomain", "?")}

    # -- API helpers -----------------------------------------------------

    def _base(self) -> str:
        data = store.load(self.id) or {}
        return f"https://{data.get('subdomain', '')}.teamwork.com"

    def _auth(self) -> tuple:
        data = store.load(self.id) or {}
        return (data.get("api_token", ""), "")

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Teamwork is not connected. Run: zeline connect teamwork")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        kwargs.setdefault("auth", self._auth())
        try:
            resp = requests.request(method, f"{self._base()}{path}", **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Teamwork API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Teamwork API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_projects(self, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        payload = self._api("GET", "/projects/api/v3/projects.json", params={"pageSize": limit})
        projects = payload.get("projects", []) if isinstance(payload, dict) else []
        lines = []
        for project in projects[:limit]:
            lines.append(f"{project.get('name', '?')} [{project.get('status', '?')}]")
        return "\n".join(lines) if lines else "No projects found."

    def list_tasks(self, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        payload = self._api("GET", "/projects/api/v3/tasks.json", params={"pageSize": limit})
        tasks = payload.get("tasks", []) if isinstance(payload, dict) else []
        lines = []
        for task in tasks[:limit]:
            project = task.get("project") or task.get("project_name") or "-"
            if isinstance(project, dict):
                project = project.get("name", "-")
            lines.append(f"{task.get('name', '?')} (project: {project})")
        return "\n".join(lines) if lines else "No tasks found."


def _register() -> TeamworkConnector:
    from zeline.connectors import register

    return register(TeamworkConnector())


_register()
