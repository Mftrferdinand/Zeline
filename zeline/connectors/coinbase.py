"""Coinbase connector (API key as a Bearer token)."""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.coinbase.com/v2"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class CoinbaseConnector(BaseConnector):
    id = "coinbase"
    name = "Coinbase"
    description = "Read Coinbase wallets and live spot prices."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.coinbase.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Coinbase rejected the API key (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: Coinbase returned an unreadable response."
        user = (body.get("data") or {}).get("name") or (body.get("data") or {}).get("email") or "?"
        store.save(self.id, {"api_key": api_key, "user": user})
        return f"Connected to Coinbase as {user}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Coinbase disconnected."
        return "Coinbase was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"{data.get('user', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_key', '')}"}

    def _require_connected(self) -> None:
        data = store.load(self.id) or {}
        if not data.get("api_key"):
            raise RuntimeError("ERROR: Coinbase is not connected. Run: zeline connect coinbase")

    def _api(self, method: str, path: str, **kwargs) -> dict:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Coinbase API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Coinbase API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Coinbase returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_accounts(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        body = self._api("GET", "/accounts", params={"limit": limit})
        accounts = body.get("data") or []
        lines = []
        for account in accounts[:limit]:
            balance = account.get("balance") or {}
            amount = balance.get("amount", "?")
            currency = balance.get("currency", "?")
            lines.append(f"{account.get('name', '?')}: {amount} {currency}")
        return "\n".join(lines) if lines else "No accounts found."

    def spot_price(self, pair: str = "BTC-USD") -> str:
        pair = (pair or "BTC-USD").strip().upper()
        body = self._api("GET", f"/prices/{pair}/spot")
        data = body.get("data") or {}
        amount = data.get("amount", "?")
        base = data.get("base", pair.split("-")[0])
        currency = data.get("currency", pair.split("-")[-1])
        try:
            pretty = f"${float(amount):,.2f}"
        except (TypeError, ValueError):
            pretty = f"${amount}"
        return f"{base}-{currency} spot: {pretty}"


def _register() -> CoinbaseConnector:
    from zeline.connectors import register

    return register(CoinbaseConnector())


_register()
