"""Opsgenie connector (API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.opsgenie.com/v2"
_TIMEOUT = 30


class OpsgenieConnector(BaseConnector):
    id = "opsgenie"
    name = "Opsgenie"
    description = "List alerts in Opsgenie."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        if not api_key:
            return "ERROR: api_key is required."
        try:
            resp = requests.get(
                f"{API_BASE}/alerts",
                headers={"Authorization": f"GenieKey {api_key}"},
                params={"limit": 1},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.opsgenie.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Opsgenie rejected the API key (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key})
        return "Connected to Opsgenie."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Opsgenie disconnected."
        return "Opsgenie was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "api key linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"GenieKey {data.get('api_key', '')}"}

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Opsgenie is not connected. Run: zeline connect opsgenie")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Opsgenie API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Opsgenie API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_alerts(self, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        payload = self._api("GET", "/alerts", params={"limit": limit})
        alerts = payload.get("data") if isinstance(payload, dict) else payload
        alerts = alerts if isinstance(alerts, list) else []
        lines = []
        for alert in alerts[:limit]:
            message = alert.get("message") or "-"
            priority = alert.get("priority") or "-"
            alert_status = alert.get("status") or "-"
            lines.append(f"{message} [{priority}] ({alert_status})")
        return "\n".join(lines) if lines else "No alerts found."


def _register() -> OpsgenieConnector:
    from zeline.connectors import register

    return register(OpsgenieConnector())


_register()
