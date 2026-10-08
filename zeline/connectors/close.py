"""Close connector (API key, HTTP Basic auth)."""
from __future__ import annotations

import requests
from requests.auth import HTTPBasicAuth

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.close.com/api/v1"
_TIMEOUT = 30


def _clamp(value: int, lo: int = 1, hi: int = 100) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = lo
    return max(lo, min(hi, value))


class CloseConnector(BaseConnector):
    id = "close"
    name = "Close"
    description = "List and create Close CRM leads."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no api key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/me/",
                auth=HTTPBasicAuth(api_key, ""),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Close ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Close rejected the api key (HTTP {resp.status_code})."
        user = resp.json() or {}
        email = user.get("email", "?")
        store.save(self.id, {"api_key": api_key, "email": email})
        return f"Connected to Close as {email}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Close disconnected."
        return "Close was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("email", "?")}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> HTTPBasicAuth:
        data = store.load(self.id) or {}
        api_key = data.get("api_key")
        if not api_key:
            raise RuntimeError("ERROR: Close is not connected. Run: zeline connect close")
        return HTTPBasicAuth(api_key, "")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        auth = self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.request(method, f"{API_BASE}{path}", auth=auth, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Close API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Close API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_leads(self, limit: int = 10) -> str:
        """List leads. READ."""
        limit = _clamp(limit)
        payload = self._api("GET", "/lead/", params={"_limit": limit})
        data = payload.get("data", []) if isinstance(payload, dict) else []
        lines = [f"{lead.get('id', '?')}: {lead.get('name', '?')}" for lead in data[:limit]]
        return "\n".join(lines) if lines else "No leads found."

    def create_lead(self, name: str) -> str:
        """Create a lead. NETWORK (writes a lead)."""
        name = (name or "").strip()
        if not name:
            raise RuntimeError("ERROR: name is required.")
        lead = self._api("POST", "/lead/", json={"name": name})
        if not isinstance(lead, dict) or "id" not in lead:
            raise RuntimeError("ERROR: Close did not return a lead id.")
        return f"Lead created: {lead['id']}"


def _register() -> CloseConnector:
    from zeline.connectors import register

    return register(CloseConnector())


_register()
