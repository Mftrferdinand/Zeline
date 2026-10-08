"""Datadog connector (API key + application key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.datadoghq.com"
_TIMEOUT = 30


class DatadogConnector(BaseConnector):
    id = "datadog"
    name = "Datadog"
    description = "List Datadog monitors and their current state."
    auth_kind = "pat"

    def connect(self, api_key: str = "", app_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        app_key = (app_key or kwargs.get("app_key") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        if not app_key:
            return "ERROR: no application key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/api/v1/validate",
                headers={
                    "DD-API-KEY": api_key,
                    "DD-APPLICATION-KEY": app_key,
                },
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.datadoghq.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Datadog rejected the keys (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: Datadog returned an unreadable response."
        if not body.get("valid"):
            return "ERROR: Datadog key validation failed."
        store.save(self.id, {"api_key": api_key, "app_key": app_key})
        return "Connected to Datadog."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Datadog disconnected."
        return "Datadog was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key") or not data.get("app_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        api_key = data.get("api_key", "")
        app_key = data.get("app_key", "")
        if not api_key or not app_key:
            raise RuntimeError("ERROR: Datadog is not connected. Run 'zeline connect datadog' first.")
        return {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Datadog API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Datadog API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_monitors(self, limit: int = 10) -> str:
        """List monitors; one "name [overall_state]" line each."""
        monitors = self._api("GET", "/api/v1/monitor", params={"limit": max(1, min(limit, 100))})
        monitors = monitors if isinstance(monitors, list) else []
        lines = [
            f"{mon.get('name', '?')} [{mon.get('overall_state', '?')}]"
            for mon in monitors[:limit]
        ]
        return "\n".join(lines) if lines else "No monitors found."


def _register() -> DatadogConnector:
    from zeline.connectors import register

    return register(DatadogConnector())


_register()
