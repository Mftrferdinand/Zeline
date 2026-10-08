"""Better Stack connector (API token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://uptime.betterstack.com/api/v2"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class BetterStackConnector(BaseConnector):
    id = "betterstack"
    name = "Better Stack"
    description = "Read Better Stack uptime monitors."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("token") or "").strip()
        if not api_token:
            return "ERROR: no API token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/monitors",
                params={"per_page": 1},
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach uptime.betterstack.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Better Stack rejected the API token (HTTP {resp.status_code})."
        try:
            resp.json()
        except ValueError:
            pass  # validated by status; body shape checked lazily on use
        store.save(self.id, {"api_token": api_token})
        return "Connected to Better Stack."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Better Stack disconnected."
        return "Better Stack was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API token stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Better Stack is not connected. Run: zeline connect betterstack")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_token', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Better Stack API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Better Stack API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Better Stack returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_monitors(self, limit: int = 10) -> str:
        """List uptime monitors (url + status). [READ]"""
        limit = _clamp(limit)
        data = self._api("GET", "/monitors", params={"per_page": limit})
        items = data.get("data", []) if isinstance(data, dict) else []
        lines = []
        for monitor in items[:limit]:
            attrs = monitor.get("attributes") or {} if isinstance(monitor, dict) else {}
            url = attrs.get("url", "?")
            status = attrs.get("status", "?")
            lines.append(f"{monitor.get('id', '?')}: {url} [{status}]")
        return "\n".join(lines) if lines else "No monitors found."


def _register() -> BetterStackConnector:
    from zeline.connectors import register

    return register(BetterStackConnector())


_register()
