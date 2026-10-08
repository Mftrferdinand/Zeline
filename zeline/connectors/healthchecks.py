"""Healthchecks.io connector (API key via X-Api-Key header)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://healthchecks.io/api/v1"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class HealthchecksConnector(BaseConnector):
    id = "healthchecks"
    name = "Healthchecks"
    description = "Read Healthchecks.io checks."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/checks/",
                headers={"X-Api-Key": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach healthchecks.io ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Healthchecks rejected the API key (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            return "ERROR: Healthchecks returned an unreadable response."
        checks = payload.get("checks", []) if isinstance(payload, dict) else []
        store.save(self.id, {"api_key": api_key})
        return f"Connected to Healthchecks ({len(checks)} checks found)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Healthchecks disconnected."
        return "Healthchecks was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Healthchecks is not connected. Run: zeline connect healthchecks")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"X-Api-Key": data.get("api_key", "")}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Healthchecks API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Healthchecks API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Healthchecks returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_checks(self, limit: int = 10) -> str:
        """List checks with name, status and last ping (client-side limit). [READ]"""
        limit = _clamp(limit)
        data = self._api("GET", "/checks/")
        checks = data.get("checks", []) if isinstance(data, dict) else []
        lines = []
        for check in checks[:limit]:
            if not isinstance(check, dict):
                continue
            name = check.get("name") or check.get("slug") or "(no name)"
            status = check.get("status", "?")
            last_ping = check.get("last_ping") or "never"
            lines.append(f"{name}: {status} (last ping: {last_ping})")
        return "\n".join(lines) if lines else "No checks found."


def _register() -> HealthchecksConnector:
    from zeline.connectors import register

    return register(HealthchecksConnector())


_register()
