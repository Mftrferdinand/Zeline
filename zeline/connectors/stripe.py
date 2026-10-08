"""Stripe connector (secret API key).

Read-only: lists charges and customers via the Stripe REST API v1.
"""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.stripe.com"
_TIMEOUT = 30


class StripeConnector(BaseConnector):
    id = "stripe"
    name = "Stripe"
    description = "List charges and customers in Stripe."
    auth_kind = "pat"

    def connect(self, secret_key: str = "", **kwargs) -> str:
        secret_key = (secret_key or kwargs.get("secret_key") or kwargs.get("token") or "").strip()
        if not secret_key:
            return "ERROR: no secret key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/v1/account",
                headers={"Authorization": f"Bearer {secret_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.stripe.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Stripe rejected the secret key (HTTP {resp.status_code})."
        try:
            business_name = resp.json().get("business_profile", {}).get("name") or "?"
        except ValueError:
            return "ERROR: Stripe returned an unreadable response."
        store.save(self.id, {"secret_key": secret_key})
        return f"Connected to Stripe account {business_name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Stripe disconnected."
        return "Stripe was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("secret_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        secret_key = data.get("secret_key", "")
        if not secret_key:
            raise RuntimeError("ERROR: Stripe is not connected. Run 'zeline connect stripe' first.")
        return {"Authorization": f"Bearer {secret_key}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Stripe API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Stripe API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_charges(self, limit: int = 10) -> str:
        """READ. List charges as ``id: amount currency [status]`` lines."""
        data = self._api(
            "GET",
            "/v1/charges",
            params={"limit": max(1, min(limit, 100))},
        )
        charges = data.get("data", []) if isinstance(data, dict) else []
        lines = []
        for charge in charges[:limit]:
            lines.append(
                f"{charge.get('id', '?')}: {charge.get('amount', '?')} "
                f"{charge.get('currency', '?')} [{charge.get('status', '?')}]"
            )
        return "\n".join(lines) if lines else "No charges found."

    def list_customers(self, limit: int = 10) -> str:
        """READ. List customers as ``id: email/name`` lines."""
        data = self._api(
            "GET",
            "/v1/customers",
            params={"limit": max(1, min(limit, 100))},
        )
        customers = data.get("data", []) if isinstance(data, dict) else []
        lines = []
        for customer in customers[:limit]:
            email = customer.get("email") or ""
            name = customer.get("name") or ""
            ident = email or name or "?"
            if email and name:
                ident = f"{email} ({name})"
            lines.append(f"{customer.get('id', '?')}: {ident}")
        return "\n".join(lines) if lines else "No customers found."


def _register() -> StripeConnector:
    from zeline.connectors import register

    return register(StripeConnector())


_register()
