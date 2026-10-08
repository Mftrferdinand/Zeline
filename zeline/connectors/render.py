"""Render connector (API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.render.com/v1"
_TIMEOUT = 30


class RenderConnector(BaseConnector):
    id = "render"
    name = "Render"
    description = "List Render services and deploys."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        if not api_key:
            return "ERROR: api_key is required."
        try:
            resp = requests.get(
                f"{API_BASE}/services",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"limit": 1},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.render.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Render rejected the API key (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key})
        return "Connected to Render."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Render disconnected."
        return "Render was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "api key linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_key', '')}"}

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Render is not connected. Run: zeline connect render")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Render API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Render API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_services(self, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        payload = self._api("GET", "/services", params={"limit": limit})
        items = payload if isinstance(payload, list) else payload.get("services", [])
        lines = []
        for item in items[:limit]:
            service = item.get("service") if isinstance(item, dict) else None
            if not isinstance(service, dict):
                service = item if isinstance(item, dict) else {}
            name = service.get("name") or "-"
            service_type = service.get("type") or "-"
            state = "suspended" if service.get("suspended") else "active"
            lines.append(f"{name} [{service_type}] ({state})")
        return "\n".join(lines) if lines else "No services found."

    def list_deploys(self, service_id: str, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        payload = self._api("GET", f"/services/{service_id}/deploys", params={"limit": limit})
        items = payload if isinstance(payload, list) else payload.get("deploys", [])
        lines = []
        for item in items[:limit]:
            deploy = item.get("deploy") if isinstance(item, dict) else None
            if not isinstance(deploy, dict):
                deploy = item if isinstance(item, dict) else {}
            deploy_id = (deploy.get("id") or "")[:8]
            status = deploy.get("status") or "-"
            commit = deploy.get("commit") or {}
            message = (commit.get("message") or "")[:60]
            lines.append(f"{deploy_id} {status} ({message})")
        return "\n".join(lines) if lines else "No deploys found."


def _register() -> RenderConnector:
    from zeline.connectors import register

    return register(RenderConnector())


_register()
