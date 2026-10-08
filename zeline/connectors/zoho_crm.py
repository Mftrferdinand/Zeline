"""Zoho CRM connector (OAuth access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

DEFAULT_API_BASE = "https://www.zohoapis.com"
_TIMEOUT = 30


def _clamp(value: int, lo: int = 1, hi: int = 100) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = lo
    return max(lo, min(hi, value))


class ZohoCrmConnector(BaseConnector):
    id = "zoho_crm"
    name = "Zoho CRM"
    description = "List and create Zoho CRM contacts."
    auth_kind = "pat"

    def connect(self, access_token: str = "", **kwargs) -> str:
        """Validate the OAuth access token against the CRM API.

        Regional domains (``.eu`` / ``.com.cn`` / ...) are supported via an
        optional ``base_url`` kwarg, e.g.
        ``base_url="https://www.zohoapis.eu"``.
        """
        token = (access_token or kwargs.get("access_token") or kwargs.get("token") or "").strip()
        base = (kwargs.get("base_url") or "").strip().rstrip("/") or DEFAULT_API_BASE
        if not token:
            return "ERROR: no access token provided."
        try:
            resp = requests.get(
                f"{base}/crm/v2/users?type=CurrentUser",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Zoho CRM ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Zoho CRM rejected the token (HTTP {resp.status_code})."
        users = (resp.json() or {}).get("users", [])
        login = users[0].get("email", "?") if users else "?"
        store.save(self.id, {"access_token": token, "base_url": base, "login": login})
        return f"Connected to Zoho CRM as {login}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Zoho CRM disconnected."
        return "Zoho CRM was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token"):
            return {"connected": False, "detail": "not linked"}
        detail = data.get("login") or data.get("base_url", "")
        return {"connected": True, "detail": detail}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> tuple[str, str]:
        data = store.load(self.id) or {}
        token = data.get("access_token")
        base = data.get("base_url") or DEFAULT_API_BASE
        if not token:
            raise RuntimeError("ERROR: Zoho CRM is not connected. Run: zeline connect zoho_crm")
        return base, token

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        base, token = self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {token}"
        try:
            resp = requests.request(method, f"{base}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Zoho CRM API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Zoho CRM API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_contacts(self, limit: int = 10) -> str:
        """List CRM contacts. READ."""
        limit = _clamp(limit)
        payload = self._api("GET", "/crm/v2/Contacts", params={"per_page": limit})
        data = payload.get("data", []) if isinstance(payload, dict) else []
        lines = []
        for contact in data[:limit]:
            name = f"{contact.get('First_Name', '')} {contact.get('Last_Name', '')}".strip()
            email = contact.get("Email") or "?"
            lines.append(f"{contact.get('id', '?')}: {name} <{email}>")
        return "\n".join(lines) if lines else "No contacts found."

    def create_contact(self, first_name: str, last_name: str, email: str = "") -> str:
        """Create a CRM contact. NETWORK (writes a contact)."""
        first_name = (first_name or "").strip()
        last_name = (last_name or "").strip()
        if not first_name or not last_name:
            raise RuntimeError("ERROR: first_name and last_name are required.")
        payload = self._api(
            "POST",
            "/crm/v2/Contacts",
            json={"data": [{"First_Name": first_name, "Last_Name": last_name, "Email": email}]},
        )
        records = payload.get("data", []) if isinstance(payload, dict) else []
        if not records:
            raise RuntimeError("ERROR: Zoho CRM did not return a contact record.")
        record = records[0]
        status = (record.get("status") or "").lower()
        if status and status != "success":
            raise RuntimeError(
                f"ERROR: Zoho CRM rejected the contact ({record.get('message', status)})."
            )
        new_id = record.get("details", {}).get("id", "?") if isinstance(record.get("details"), dict) else "?"
        return f"Contact created: {new_id}"


def _register() -> ZohoCrmConnector:
    from zeline.connectors import register

    return register(ZohoCrmConnector())


_register()
