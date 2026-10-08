"""Pipedrive connector (API token via query param)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.pipedrive.com/v1"
_TIMEOUT = 30


def _clamp(value: int, lo: int = 1, hi: int = 100) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = lo
    return max(lo, min(hi, value))


class PipedriveConnector(BaseConnector):
    id = "pipedrive"
    name = "Pipedrive"
    description = "List and create Pipedrive deals."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        token = (api_token or kwargs.get("api_token") or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no api token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/users/me",
                params={"api_token": token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Pipedrive ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Pipedrive rejected the token (HTTP {resp.status_code})."
        body = resp.json() or {}
        if not body.get("success"):
            return "ERROR: Pipedrive rejected the token."
        name = (body.get("data") or {}).get("name", "?")
        store.save(self.id, {"api_token": token, "name": name})
        return f"Connected to Pipedrive as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Pipedrive disconnected."
        return "Pipedrive was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("name", "?")}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> str:
        data = store.load(self.id) or {}
        token = data.get("api_token")
        if not token:
            raise RuntimeError("ERROR: Pipedrive is not connected. Run: zeline connect pipedrive")
        return token

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        token = self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        params = kwargs.pop("params", {}) or {}
        params["api_token"] = token
        try:
            resp = requests.request(method, f"{API_BASE}{path}", params=params, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Pipedrive API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Pipedrive API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_deals(self, limit: int = 10) -> str:
        """List deals. READ."""
        limit = _clamp(limit)
        payload = self._api("GET", "/deals", params={"limit": limit})
        data = payload.get("data", []) if isinstance(payload, dict) else []
        lines = []
        for deal in data[:limit]:
            lines.append(f"{deal.get('id', '?')}: {deal.get('title', '?')} (${deal.get('value', 0)})")
        return "\n".join(lines) if lines else "No deals found."

    def create_deal(self, title: str, value: str = "") -> str:
        """Create a deal. NETWORK (writes a deal)."""
        title = (title or "").strip()
        if not title:
            raise RuntimeError("ERROR: title is required.")
        body: dict = {"title": title}
        if value not in ("", None):
            body["value"] = value
        payload = self._api("POST", "/deals", json=body)
        if isinstance(payload, dict) and not payload.get("success", True):
            raise RuntimeError("ERROR: Pipedrive rejected the deal.")
        data = (payload or {}).get("data") or {}
        new_id = data.get("id", "?") if isinstance(data, dict) else "?"
        return f"Deal created: {new_id}"


def _register() -> PipedriveConnector:
    from zeline.connectors import register

    return register(PipedriveConnector())


_register()
