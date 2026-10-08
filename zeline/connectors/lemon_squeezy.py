"""Lemon Squeezy connector (API key, Bearer)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.lemonsqueezy.com/v1"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


def _attributes(item: dict) -> dict:
    if isinstance(item, dict):
        attrs = item.get("attributes")
        if isinstance(attrs, dict):
            return attrs
    return {}


class LemonSqueezyConnector(BaseConnector):
    id = "lemon_squeezy"
    name = "Lemon Squeezy"
    description = "List Lemon Squeezy customers and orders."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/users/me",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Lemon Squeezy API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Lemon Squeezy rejected the API key (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            return "ERROR: Lemon Squeezy returned an unreadable response."
        attrs = _attributes(payload.get("data") if isinstance(payload, dict) else None)
        name = str(attrs.get("name") or "").strip()
        email = str(attrs.get("email") or "").strip()
        store.save(self.id, {"api_key": api_key, "name": name, "email": email})
        who = name or email or "user"
        return f"Connected to Lemon Squeezy as {who}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Lemon Squeezy disconnected."
        return "Lemon Squeezy was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        detail = data.get("name") or data.get("email") or "linked"
        return {"connected": True, "detail": f"connected as {detail}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError(
                "ERROR: Lemon Squeezy is not connected. Run: zeline connect lemon_squeezy"
            )

    def _stored(self) -> dict:
        return store.load(self.id) or {}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._stored().get('api_key', '')}"}

    def _api(self, path: str, params: dict | None = None):
        self._require_connected()
        try:
            resp = requests.get(
                f"{API_BASE}{path}",
                headers=self._headers(),
                params=params or {},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Lemon Squeezy API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Lemon Squeezy API {resp.status_code} on {path}.")
        try:
            payload = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Lemon Squeezy returned an unreadable response.") from None
        if isinstance(payload, dict):
            items = payload.get("data") or []
        else:
            items = []
        return items if isinstance(items, list) else []

    # -- user-facing operations -------------------------------------------

    def list_customers(self, limit: int = 10) -> str:
        """[READ] List Lemon Squeezy customers."""
        limit = _clamp(limit)
        items = self._api("/customers", params={"page[size]": limit})
        lines = []
        for item in items[:limit]:
            attrs = _attributes(item)
            lines.append(
                f"{attrs.get('name', '(no name)')} <{attrs.get('email', '?')}> "
                f"({attrs.get('status', '?')})"
            )
        return "\n".join(lines) if lines else "No customers found."

    def list_orders(self, limit: int = 10) -> str:
        """[READ] List Lemon Squeezy orders."""
        limit = _clamp(limit)
        items = self._api("/orders", params={"page[size]": limit})
        lines = []
        for item in items[:limit]:
            attrs = _attributes(item)
            lines.append(
                f"#{attrs.get('order_number', '?')}: {attrs.get('total_formatted', '?')} "
                f"({attrs.get('status', '?')})"
            )
        return "\n".join(lines) if lines else "No orders found."


def _register() -> LemonSqueezyConnector:
    from zeline.connectors import register

    return register(LemonSqueezyConnector())


_register()
