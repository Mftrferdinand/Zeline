"""Webflow connector (API token, Bearer auth)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.webflow.com/v2"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class WebflowConnector(BaseConnector):
    id = "webflow"
    name = "Webflow"
    description = "Read Webflow sites and CMS collections."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("api_token") or kwargs.get("token") or "").strip()
        if not api_token:
            return "ERROR: api_token is required."
        try:
            resp = requests.get(
                f"{API_BASE}/token/authorized_by",
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Webflow ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Webflow rejected the API token (HTTP {resp.status_code})."
        store.save(self.id, {"api_token": api_token})
        return "Connected to Webflow."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Webflow disconnected."
        return "Webflow was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "Webflow API v2"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Webflow is not connected. Run: zeline connect webflow")

    def _headers(self) -> dict:
        self._require_connected()
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_token', '')}"}

    def _get(self, path: str, params: dict | None = None):
        try:
            resp = requests.get(
                f"{API_BASE}{path}", params=params, headers=self._headers(), timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Webflow API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Webflow API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_sites(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        payload = self._get("/sites")
        sites = payload.get("sites", []) if isinstance(payload, dict) else []
        lines = [f"{site.get('id', '?')}: {site.get('displayName', '?')}" for site in sites[:limit]]
        return "\n".join(lines) if lines else "No sites found."

    def list_collections(self, site_id: str, limit: int = 10) -> str:
        limit = _clamp(limit)
        payload = self._get(f"/sites/{site_id}/collections")
        collections = payload.get("collections", []) if isinstance(payload, dict) else []
        lines = []
        for coll in collections[:limit]:
            n_fields = len(coll.get("fields") or [])
            lines.append(f"{coll.get('id', '?')}: {coll.get('displayName', '?')} ({n_fields} fields)")
        return "\n".join(lines) if lines else f"No collections found on site {site_id}."


def _register() -> WebflowConnector:
    from zeline.connectors import register

    return register(WebflowConnector())


_register()
