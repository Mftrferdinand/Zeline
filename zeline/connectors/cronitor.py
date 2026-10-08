"""Cronitor connector (API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://cronitor.io/api/v3"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class CronitorConnector(BaseConnector):
    id = "cronitor"
    name = "Cronitor"
    description = "Read Cronitor monitors."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        # Standard auth pattern: Authorization: Bearer <key>. The Cronitor vendor
        # API may use a different scheme; adjust this header if auth fails.
        try:
            resp = requests.get(
                f"{API_BASE}/monitors",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach cronitor.io ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Cronitor rejected the API key (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            body = {}
        monitors = body.get("monitors", []) if isinstance(body, dict) else []
        if not isinstance(monitors, list):
            monitors = []
        store.save(self.id, {"api_key": api_key})
        return f"Connected to Cronitor ({len(monitors)} monitors found)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Cronitor disconnected."
        return "Cronitor was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Cronitor is not connected. Run: zeline connect cronitor")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_key', '')}"}

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        self._require_connected()
        try:
            resp = requests.get(
                f"{API_BASE}{path}",
                headers=self._headers(),
                params=params,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Cronitor API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Cronitor API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Cronitor returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_monitors(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._get("/monitors", params={"limit": limit})
        monitors = data.get("monitors", []) if isinstance(data, dict) else []
        if not isinstance(monitors, list):
            monitors = []
        lines = []
        for monitor in monitors[:limit]:
            if not isinstance(monitor, dict):
                continue
            status = monitor.get("status") or monitor.get("state") or "?"
            lines.append(f"{monitor.get('key', '?')}: {monitor.get('name', '(no name)')} [{status}]")
        return "\n".join(lines) if lines else "No monitors found."


def _register() -> CronitorConnector:
    from zeline.connectors import register

    return register(CronitorConnector())


_register()
