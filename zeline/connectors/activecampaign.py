"""ActiveCampaign connector (API key + account base URL)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class ActiveCampaignConnector(BaseConnector):
    id = "activecampaign"
    name = "ActiveCampaign"
    description = "Read and create ActiveCampaign contacts."
    auth_kind = "pat"

    def connect(self, api_key: str = "", base_url: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        base_url = (base_url or kwargs.get("base_url") or "").strip().rstrip("/")
        if not api_key:
            return "ERROR: no API key provided."
        if not base_url:
            return "ERROR: no base URL provided (e.g. https://xxx.api-us1.com)."
        try:
            resp = requests.get(
                f"{base_url}/api/3/users/me",
                headers={"Api-Token": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {base_url} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: ActiveCampaign rejected the API key (HTTP {resp.status_code})."
        try:
            user_name = resp.json().get("user", {}).get("username", "?")
        except (ValueError, AttributeError):
            user_name = "?"
        store.save(self.id, {"api_key": api_key, "base_url": base_url})
        return f"Connected to ActiveCampaign (user {user_name})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "ActiveCampaign disconnected."
        return "ActiveCampaign was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key") or not data.get("base_url"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"linked to {data.get('base_url')}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError(
                "ERROR: ActiveCampaign is not connected. Run: zeline connect activecampaign"
            )

    def _base_url(self) -> str:
        data = store.load(self.id) or {}
        return data.get("base_url", "")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Api-Token": data.get("api_key", "")}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(
                method, f"{self._base_url()}{path}", headers=headers, **kwargs
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: ActiveCampaign API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: ActiveCampaign API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: ActiveCampaign returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_contacts(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/api/3/contacts", params={"limit": limit})
        items = data.get("contacts", []) if isinstance(data, dict) else []
        lines = []
        for contact in items[:limit]:
            lines.append(f"{contact.get('id', '?')}: {contact.get('email', '(no email)')}")
        return "\n".join(lines) if lines else "No contacts found."

    def create_contact(
        self, email: str, first_name: str = "", last_name: str = ""
    ) -> str:
        email = (email or "").strip()
        if not email:
            raise RuntimeError("ERROR: contact email is required.")
        data = self._api(
            "POST",
            "/api/3/contacts",
            json={
                "contact": {
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                }
            },
        )
        contact = data.get("contact", {}) if isinstance(data, dict) else {}
        return f"Contact created: {contact.get('id', '?')} ({contact.get('email', email)})."


def _register() -> ActiveCampaignConnector:
    from zeline.connectors import register

    return register(ActiveCampaignConnector())


_register()
