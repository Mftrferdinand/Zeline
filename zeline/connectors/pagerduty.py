"""PagerDuty connector (API token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.pagerduty.com"
_TIMEOUT = 30
_ACCEPT = "application/vnd.pagerduty+json;2"


class PagerDutyConnector(BaseConnector):
    id = "pagerduty"
    name = "PagerDuty"
    description = "List PagerDuty incidents."
    auth_kind = "pat"

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Token token={token}", "Accept": _ACCEPT}

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/users/me",
                headers=self._headers(token),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.pagerduty.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: PagerDuty rejected the token (HTTP {resp.status_code})."
        try:
            user = resp.json().get("user", {})
            name = user.get("name") or user.get("email") or "?"
        except ValueError:
            return "ERROR: PagerDuty returned an unreadable response."
        store.save(self.id, {"token": token, "user": name})
        return f"Connected to PagerDuty as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "PagerDuty disconnected."
        return "PagerDuty was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"{data.get('user', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> dict:
        data = store.load(self.id) or {}
        if not data.get("token"):
            raise RuntimeError("ERROR: PagerDuty is not connected. Run 'zeline connect pagerduty' first.")
        return data

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers(self._require_connected()["token"]))
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: PagerDuty API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: PagerDuty API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_incidents(self, limit: int = 10, status: str = "triggered") -> str:
        status = (status or "").strip() or "triggered"
        data = self._api(
            "GET", "/incidents",
            params={"limit": max(1, min(limit, 100)), "statuses[]": status},
        )
        lines = []
        for incident in data.get("incidents", [])[:limit]:
            lines.append(
                f"#{incident.get('incident_number', '?')} {incident.get('title', '?')} "
                f"[{incident.get('urgency', '?')}]"
            )
        return "\n".join(lines) if lines else f"No {status} incidents."


def _register() -> PagerDutyConnector:
    from zeline.connectors import register

    return register(PagerDutyConnector())


_register()
