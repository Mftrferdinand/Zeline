"""HubSpot connector (private app token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.hubapi.com"
_TIMEOUT = 30


class HubSpotConnector(BaseConnector):
    id = "hubspot"
    name = "HubSpot"
    description = "List and create CRM contacts in HubSpot."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/crm/v3/objects/contacts",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": 1},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.hubapi.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: HubSpot rejected the token (HTTP {resp.status_code})."
        store.save(self.id, {"token": token})
        return "Connected to HubSpot."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "HubSpot disconnected."
        return "HubSpot was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        token = data.get("token", "")
        if not token:
            raise RuntimeError("ERROR: HubSpot is not connected. Run 'zeline connect hubspot' first.")
        return {"Authorization": f"Bearer {token}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: HubSpot API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: HubSpot API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_contacts(self, limit: int = 10) -> str:
        """READ. List contacts as ``email (firstname lastname)`` lines."""
        data = self._api(
            "GET",
            "/crm/v3/objects/contacts",
            params={
                "limit": max(1, min(limit, 100)),
                "properties": "email,firstname,lastname",
            },
        )
        results = data.get("results", []) if isinstance(data, dict) else []
        lines = []
        for contact in results[:limit]:
            props = contact.get("properties", {})
            email = props.get("email", "?")
            name = " ".join(p for p in (props.get("firstname", ""), props.get("lastname", "")) if p).strip()
            line = f"{email}"
            if name:
                line += f" ({name})"
            lines.append(line)
        return "\n".join(lines) if lines else "No contacts found."

    def create_contact(self, email: str, firstname: str = "", lastname: str = "") -> str:
        """NETWORK. Create a contact. Returns the created contact id."""
        email = (email or "").strip()
        if not email:
            return "ERROR: no email provided."
        properties = {"email": email}
        if firstname and firstname.strip():
            properties["firstname"] = firstname.strip()
        if lastname and lastname.strip():
            properties["lastname"] = lastname.strip()
        contact = self._api(
            "POST",
            "/crm/v3/objects/contacts",
            json={"properties": properties},
        )
        cid = contact.get("id", "?") if isinstance(contact, dict) else "?"
        return f"Created contact {cid}."


def _register() -> HubSpotConnector:
    from zeline.connectors import register

    return register(HubSpotConnector())


_register()
