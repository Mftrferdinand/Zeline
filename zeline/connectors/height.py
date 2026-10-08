"""Height connector (personal API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.height.app"
_TIMEOUT = 30


class HeightConnector(BaseConnector):
    id = "height"
    name = "Height"
    description = "List tasks in Height."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        if not api_key:
            return "ERROR: api_key is required."
        try:
            resp = requests.post(
                f"{API_BASE}/tasks/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {API_BASE} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Height rejected the key (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key})
        return "Connected to Height."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Height disconnected."
        return "Height was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_key', '')}"}

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Height is not connected. Run: zeline connect height")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Height API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Height API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_tasks(self, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        data = self._api("POST", "/tasks/search", json={"filters": {}})
        tasks = data.get("list", []) if isinstance(data, dict) else []
        lines = []
        for task in tasks[:limit]:
            name = task.get("name", "-")
            status = task.get("status", "-")
            lines.append(f"{name} [{status}]")
        return "\n".join(lines) if lines else "No tasks found."


def _register() -> HeightConnector:
    from zeline.connectors import register

    return register(HeightConnector())


_register()
