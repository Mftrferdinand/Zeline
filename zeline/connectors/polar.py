"""Polar connector (polar.sh API, personal access token)."""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.polar.sh/v1"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class PolarConnector(BaseConnector):
    id = "polar"
    name = "Polar"
    description = "List Polar.sh products and orders."
    auth_kind = "pat"

    def connect(self, access_token: str = "", **kwargs) -> str:
        access_token = (access_token or kwargs.get("token") or "").strip()
        if not access_token:
            return "ERROR: no access token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/products",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"limit": 1},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Polar API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Polar rejected the access token (HTTP {resp.status_code})."
        try:
            resp.json()
        except ValueError:
            return "ERROR: Polar returned an unreadable response."
        store.save(self.id, {"access_token": access_token})
        return "Connected to Polar."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Polar disconnected."
        return "Polar was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Polar is not connected. Run: zeline connect polar")

    def _stored(self) -> dict:
        return store.load(self.id) or {}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._stored().get('access_token', '')}"}

    def _api(self, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request("GET", f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Polar API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Polar API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Polar returned an unreadable response.") from None

    @staticmethod
    def _items(payload: dict | list) -> list:
        if isinstance(payload, dict):
            items = payload.get("items") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        return items if isinstance(items, list) else []

    @staticmethod
    def _price_summary(product: dict) -> str:
        prices = product.get("prices") or []
        if not isinstance(prices, list) or not prices:
            return "no price"
        parts = []
        for price in prices:
            if not isinstance(price, dict):
                continue
            amount = price.get("price_amount")
            currency = price.get("price_currency") or ""
            recurring = price.get("recurring_interval")
            part = f"{amount} {currency}".strip() if amount is not None else "(free?)"
            if recurring:
                part += f"/{recurring}"
            parts.append(part)
        return ", ".join(parts) if parts else "no price"

    # -- user-facing operations -------------------------------------------

    def list_products(self, limit: int = 10) -> str:
        """[READ] List products from Polar."""
        limit = _clamp(limit)
        data = self._api("/products", params={"limit": limit})
        items = self._items(data)
        lines = []
        for product in items[:limit]:
            if isinstance(product, dict):
                lines.append(
                    f"{product.get('id', '?')}: {product.get('name', '(no name)')} "
                    f"({self._price_summary(product)})"
                )
        return "\n".join(lines) if lines else "No products found."

    def list_orders(self, limit: int = 10) -> str:
        """[READ] List orders from Polar."""
        limit = _clamp(limit)
        data = self._api("/orders", params={"limit": limit})
        items = self._items(data)
        lines = []
        for order in items[:limit]:
            if isinstance(order, dict):
                lines.append(
                    f"{order.get('id', '?')}: {order.get('amount', '?')} "
                    f"(status: {order.get('status', '?')}, "
                    f"created: {order.get('created_at', '?')})"
                )
        return "\n".join(lines) if lines else "No orders found."


def _register() -> PolarConnector:
    from zeline.connectors import register

    return register(PolarConnector())


_register()
