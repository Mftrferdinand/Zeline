"""Mailchimp connector (API key with datacenter suffix)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://{dc}.api.mailchimp.com/3.0"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


def _api_base(datacenter: str) -> str:
    return API_BASE.format(dc=datacenter)


class MailchimpConnector(BaseConnector):
    id = "mailchimp"
    name = "Mailchimp"
    description = "Read Mailchimp audiences and campaigns."
    auth_kind = "pat"

    def connect(self, api_key: str = "", datacenter: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        datacenter = (datacenter or kwargs.get("datacenter") or "").strip()
        if not datacenter and "-" in api_key:
            datacenter = api_key.rsplit("-", 1)[1].strip()
        if not datacenter:
            return "ERROR: datacenter required (pass datacenter='xxN' or use an API key ending in -xxN)."
        base = _api_base(datacenter)
        try:
            resp = requests.get(base + "/", auth=("anystring", api_key), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach {datacenter}.api.mailchimp.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Mailchimp rejected the API key (HTTP {resp.status_code})."
        try:
            account_name = resp.json().get("account_name", "?")
        except ValueError:
            account_name = "?"
        store.save(self.id, {"api_key": api_key, "datacenter": datacenter})
        return f"Connected to Mailchimp (account {account_name})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Mailchimp disconnected."
        return "Mailchimp was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Mailchimp is not connected. Run: zeline connect mailchimp")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        data = store.load(self.id) or {}
        kwargs.setdefault("auth", ("anystring", data.get("api_key", "")))
        try:
            resp = requests.request(method, f"{_api_base(data.get('datacenter', ''))}{path}", **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Mailchimp API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Mailchimp API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Mailchimp returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_audiences(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/lists", params={"count": limit})
        items = data.get("lists", []) if isinstance(data, dict) else []
        lines = []
        for audience in items[:limit]:
            name = audience.get("name", "(no name)")
            count = (audience.get("stats") or {}).get("member_count", "?")
            lines.append(f"{name} ({count} members)")
        return "\n".join(lines) if lines else "No audiences found."

    def list_campaigns(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/campaigns", params={"count": limit})
        items = data.get("campaigns", []) if isinstance(data, dict) else []
        lines = []
        for campaign in items[:limit]:
            title = (campaign.get("settings") or {}).get("title", "(no title)")
            status = campaign.get("status", "?")
            lines.append(f"{title} [{status}]")
        return "\n".join(lines) if lines else "No campaigns found."


def _register() -> MailchimpConnector:
    from zeline.connectors import register

    return register(MailchimpConnector())


_register()
