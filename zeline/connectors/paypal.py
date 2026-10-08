"""PayPal connector (OAuth2 client_credentials via client_id + client_secret).

Production base is https://api-m.paypal.com ; for sandbox testing use
https://api-m.sandbox.paypal.com instead. Access tokens are short-lived:
when the API answers 401, reconnect with ``zeline connect paypal`` to get
a fresh token.
"""

from __future__ import annotations

import requests
from requests.auth import HTTPBasicAuth

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api-m.paypal.com"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class PayPalConnector(BaseConnector):
    id = "paypal"
    name = "PayPal"
    description = "Read PayPal invoices and checkout orders (OAuth2 client credentials)."
    auth_kind = "pat"

    def connect(self, client_id: str = "", client_secret: str = "", **kwargs) -> str:
        client_id = (client_id or kwargs.get("client_id") or "").strip()
        client_secret = (client_secret or kwargs.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return "ERROR: client_id and client_secret are both required."
        try:
            resp = requests.post(
                f"{API_BASE}/v1/oauth2/token",
                auth=HTTPBasicAuth(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api-m.paypal.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: PayPal rejected the credentials (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: PayPal returned an unreadable response."
        access_token = body.get("access_token")
        if not access_token:
            return "ERROR: PayPal did not return an access token."
        store.save(self.id, {"client_id": client_id, "access_token": access_token})
        return "Connected to PayPal."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "PayPal disconnected."
        return "PayPal was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked (token cached)"}

    # -- API helpers -----------------------------------------------------

    def _auth_headers(self) -> dict:
        data = store.load(self.id) or {}
        access_token = data.get("access_token", "")
        if not access_token:
            raise RuntimeError("ERROR: PayPal is not connected. Run: zeline connect paypal")
        return {"Authorization": f"Bearer {access_token}"}

    def _require_connected(self) -> None:
        self._auth_headers()

    def _api(self, method: str, path: str, **kwargs) -> dict:
        headers = self._auth_headers()
        extra = kwargs.pop("headers", {}) or {}
        headers.update(extra)
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: PayPal API request failed ({exc}).") from exc
        if resp.status_code == 401:
            raise RuntimeError(
                "ERROR: PayPal returned 401 (access token expired). "
                "Reconnect with: zeline connect paypal"
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: PayPal API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: PayPal returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_invoices(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        body = self._api("GET", "/v2/invoicing/invoices", params={"page_size": limit})
        items = body.get("items") or []
        lines = []
        for invoice in items[:limit]:
            amount = invoice.get("amount") or {}
            value = amount.get("value", "?")
            currency = amount.get("currency_code", "?")
            lines.append(
                f"{invoice.get('id', '?')}: ${value} {currency} ({invoice.get('status', '?')})"
            )
        return "\n".join(lines) if lines else "No invoices found."

    def get_order(self, order_id: str) -> str:
        order_id = (order_id or "").strip()
        if not order_id:
            raise RuntimeError("ERROR: no order id provided.")
        body = self._api("GET", f"/v2/checkout/orders/{order_id}")
        units = body.get("purchase_units") or []
        parts = [f"Order {body.get('id', order_id)}: {body.get('status', '?')}"]
        for unit in units:
            amount = unit.get("amount") or {}
            parts.append(f"{amount.get('value', '?')} {amount.get('currency_code', '?')}")
        return ", ".join(parts)


def _register() -> PayPalConnector:
    from zeline.connectors import register

    return register(PayPalConnector())


_register()
