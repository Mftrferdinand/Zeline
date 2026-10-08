"""Fathom connector (API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.usefathom.com/v1"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class FathomConnector(BaseConnector):
    id = "fathom"
    name = "Fathom"
    description = "Read Fathom Analytics sites."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/sites",
                params={"limit": 1},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.usefathom.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Fathom rejected the API key (HTTP {resp.status_code})."
        try:
            resp.json()
        except ValueError:
            pass
        store.save(self.id, {"api_key": api_key})
        return "Connected to Fathom."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Fathom disconnected."
        return "Fathom was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Fathom is not connected. Run: zeline connect fathom")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_key', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Fathom API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Fathom API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Fathom returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_sites(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/sites", params={"limit": limit})
        items = data.get("data", []) if isinstance(data, dict) else []
        lines = []
        for site in items[:limit]:
            site_id = site.get("id", "?")
            name = site.get("name", "(no name)")
            timezone = site.get("timezone") or site.get("default_tracking_timezone") or "?"
            lines.append(f"{site_id}: {name} ({timezone})")
        return "\n".join(lines) if lines else "No sites found."


def _register() -> FathomConnector:
    from zeline.connectors import register

    return register(FathomConnector())


_register()
