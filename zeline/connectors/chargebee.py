"""Chargebee connector (API key + site, Basic auth)."""
from __future__ import annotations

import requests
from requests.auth import HTTPBasicAuth

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class ChargebeeConnector(BaseConnector):
    id = "chargebee"
    name = "Chargebee"
    description = "Read Chargebee customers and subscriptions."
    auth_kind = "pat"

    def connect(self, api_key: str = "", site: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        site = (site or kwargs.get("site") or "").strip().lower()
        for prefix in ("https://", "http://"):
            if site.startswith(prefix):
                site = site[len(prefix):]
        site = site.rstrip("/")
        suffix = ".chargebee.com"
        if site.endswith(suffix):
            site = site[: -len(suffix)]
        if not api_key or not site:
            return "ERROR: api_key and site are required."
        try:
            resp = requests.get(
                f"https://{site}.chargebee.com/api/v2/customers",
                params={"limit": 1},
                auth=HTTPBasicAuth(api_key, ""),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Chargebee ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Chargebee rejected the API key (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key, "site": site})
        return f"Connected to Chargebee site '{site}'."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Chargebee disconnected."
        return "Chargebee was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"site: {data.get('site', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Chargebee is not connected. Run: zeline connect chargebee")

    def _creds(self) -> tuple[str, HTTPBasicAuth]:
        self._require_connected()
        data = store.load(self.id) or {}
        site = data.get("site", "")
        return site, HTTPBasicAuth(data.get("api_key", ""), "")

    def _get(self, path: str, params: dict | None = None):
        site, auth = self._creds()
        try:
            resp = requests.get(
                f"https://{site}.chargebee.com/api/v2{path}",
                params=params,
                auth=auth,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Chargebee API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Chargebee API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_customers(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        payload = self._get("/customers", {"limit": limit})
        entries = payload.get("list", []) if isinstance(payload, dict) else []
        lines = []
        for entry in entries[:limit]:
            cust = entry.get("customer", {}) if isinstance(entry, dict) else {}
            first = str(cust.get("first_name") or "").strip()
            last = str(cust.get("last_name") or "").strip()
            name = f"{first} {last}".strip() or "?"
            lines.append(f"{cust.get('id', '?')}: {name} <{cust.get('email', '?')}>")
        return "\n".join(lines) if lines else "No customers found."

    def list_subscriptions(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        payload = self._get("/subscriptions", {"limit": limit})
        entries = payload.get("list", []) if isinstance(payload, dict) else []
        lines = []
        for entry in entries[:limit]:
            sub = entry.get("subscription", {}) if isinstance(entry, dict) else {}
            items = sub.get("subscription_items") or []
            plan = str(sub.get("plan_id") or "")
            if not plan and items:
                plan = ", ".join(str(i.get("item_price_id", "?")) for i in items)
            plan = plan or "?"
            amount_cents = sub.get("plan_amount") or 0
            if not amount_cents and items:
                amount_cents = sum(int(i.get("unit_price") or 0) for i in items)
            lines.append(
                f"{sub.get('id', '?')}: {plan} (${amount_cents / 100:.2f}) "
                f"[{sub.get('status', '?')}]"
            )
        return "\n".join(lines) if lines else "No subscriptions found."


def _register() -> ChargebeeConnector:
    from zeline.connectors import register

    return register(ChargebeeConnector())


_register()
