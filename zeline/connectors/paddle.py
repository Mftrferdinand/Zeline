"""Paddle connector (API key, Bearer auth, new Paddle Billing API)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.paddle.com"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


def _to_cents(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


class PaddleConnector(BaseConnector):
    id = "paddle"
    name = "Paddle"
    description = "Read Paddle customers and transactions."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: api_key is required."
        try:
            resp = requests.get(
                f"{API_BASE}/event-types",
                params={"per_page": 1},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Paddle ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Paddle rejected the API key (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key})
        return "Connected to Paddle."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Paddle disconnected."
        return "Paddle was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "Paddle Billing API"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Paddle is not connected. Run: zeline connect paddle")

    def _headers(self) -> dict:
        self._require_connected()
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_key', '')}"}

    def _get(self, path: str, params: dict | None = None):
        try:
            resp = requests.get(
                f"{API_BASE}{path}", params=params, headers=self._headers(), timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Paddle API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Paddle API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_customers(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        payload = self._get("/customers", {"per_page": limit})
        customers = payload.get("data", []) if isinstance(payload, dict) else []
        lines = []
        for cust in customers[:limit]:
            lines.append(f"{cust.get('id', '?')}: {cust.get('name', '?')} <{cust.get('email', '?')}>")
        return "\n".join(lines) if lines else "No customers found."

    def list_transactions(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        payload = self._get("/transactions", {"per_page": limit})
        txns = payload.get("data", []) if isinstance(payload, dict) else []
        lines = []
        for txn in txns[:limit]:
            details = txn.get("details") or {}
            totals = details.get("totals") or {}
            cents = _to_cents(totals.get("grand_total", 0))
            currency = totals.get("currency_code", "?")
            lines.append(f"{txn.get('id', '?')}: ${cents / 100:.2f} {currency} [{txn.get('status', '?')}]")
        return "\n".join(lines) if lines else "No transactions found."


def _register() -> PaddleConnector:
    from zeline.connectors import register

    return register(PaddleConnector())


_register()
