"""ConvertKit connector (API secret)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.convertkit.com"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class ConvertKitConnector(BaseConnector):
    id = "convertkit"
    name = "ConvertKit"
    description = "Read ConvertKit subscribers."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API secret provided."
        try:
            resp = requests.get(
                f"{API_BASE}/v3/account",
                params={"api_secret": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.convertkit.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: ConvertKit rejected the API secret (HTTP {resp.status_code})."
        try:
            account_name = resp.json().get("name", "?")
        except ValueError:
            account_name = "?"
        store.save(self.id, {"api_key": api_key})
        return f"Connected to ConvertKit ({account_name})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "ConvertKit disconnected."
        return "ConvertKit was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API secret stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: ConvertKit is not connected. Run: zeline connect convertkit")

    def _params(self) -> dict:
        data = store.load(self.id) or {}
        return {"api_secret": data.get("api_key", "")}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        params = kwargs.pop("params", {}) or {}
        params.update(self._params())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", params=params, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: ConvertKit API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: ConvertKit API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: ConvertKit returned an unreadable response.") from None

    # -- user-facing operations ------------------------------------------

    def list_subscribers(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/v3/subscribers", params={"page_size": limit})
        items = data.get("subscribers", []) if isinstance(data, dict) else []
        lines = []
        for sub in items[:limit]:
            name = sub.get("first_name") or "(no name)"
            lines.append(f"{sub.get('email_address', '?')} - {name} ({sub.get('state', '?')})")
        return "\n".join(lines) if lines else "No subscribers found."


def _register() -> ConvertKitConnector:
    from zeline.connectors import register

    return register(ConvertKitConnector())


_register()
