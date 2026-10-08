"""Freshdesk connector (API key + subdomain, HTTP Basic auth)."""
from __future__ import annotations

import requests
from requests.auth import HTTPBasicAuth

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30
_STATUS_NAMES = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}


def _clamp(value: int, lo: int = 1, hi: int = 100) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = lo
    return max(lo, min(hi, value))


def _normalize_subdomain(subdomain: str) -> str:
    sub = (subdomain or "").strip().lower()
    for prefix in ("https://", "http://"):
        if sub.startswith(prefix):
            sub = sub[len(prefix):]
    sub = sub.rstrip("/")
    suffix = ".freshdesk.com"
    if sub.endswith(suffix):
        sub = sub[: -len(suffix)]
    return sub


class FreshdeskConnector(BaseConnector):
    id = "freshdesk"
    name = "Freshdesk"
    description = "List and create Freshdesk support tickets."
    auth_kind = "pat"

    def connect(self, api_key: str = "", subdomain: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        subdomain = _normalize_subdomain(subdomain or kwargs.get("subdomain") or "")
        if not api_key or not subdomain:
            return "ERROR: api_key and subdomain are required."
        try:
            resp = requests.get(
                f"https://{subdomain}.freshdesk.com/api/v2/agents/me",
                auth=HTTPBasicAuth(api_key, "X"),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Freshdesk ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Freshdesk rejected the credentials (HTTP {resp.status_code})."
        agent = resp.json() or {}
        name = agent.get("contact", {}).get("name") or agent.get("email", "?")
        store.save(self.id, {"api_key": api_key, "subdomain": subdomain, "name": name})
        return f"Connected to Freshdesk as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Freshdesk disconnected."
        return "Freshdesk was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        detail = data.get("subdomain", "")
        if data.get("name"):
            detail = f"{detail} ({data['name']})"
        return {"connected": True, "detail": detail}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> tuple[str, HTTPBasicAuth]:
        data = store.load(self.id) or {}
        api_key = data.get("api_key")
        subdomain = data.get("subdomain")
        if not api_key or not subdomain:
            raise RuntimeError("ERROR: Freshdesk is not connected. Run: zeline connect freshdesk")
        return subdomain, HTTPBasicAuth(api_key, "X")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        subdomain, auth = self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.request(
                method,
                f"https://{subdomain}.freshdesk.com/api/v2{path}",
                auth=auth,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Freshdesk API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Freshdesk API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_tickets(self, limit: int = 10) -> str:
        """List support tickets. READ."""
        limit = _clamp(limit)
        tickets = self._api("GET", "/tickets", params={"per_page": limit})
        if not isinstance(tickets, list):
            tickets = []
        lines = []
        for ticket in tickets[:limit]:
            status_code = ticket.get("status")
            status_name = _STATUS_NAMES.get(status_code, status_code)
            lines.append(f"#{ticket.get('id', '?')}: {ticket.get('subject', '?')} [{status_name}]")
        return "\n".join(lines) if lines else "No tickets found."

    def create_ticket(
        self,
        subject: str,
        description: str,
        email: str = "",
        priority: int = 1,
        status: int = 2,
    ) -> str:
        """Create a support ticket. NETWORK (writes a ticket)."""
        subject = (subject or "").strip()
        description = (description or "").strip()
        if not subject or not description:
            raise RuntimeError("ERROR: subject and description are required.")
        if priority not in (1, 2, 3, 4):
            raise RuntimeError("ERROR: priority must be 1-4.")
        if status not in (2, 3, 4, 5):
            raise RuntimeError("ERROR: status must be 2-5.")
        body: dict = {"subject": subject, "description": description, "priority": priority, "status": status}
        if email:
            body["email"] = email
        ticket = self._api("POST", "/tickets", json=body)
        if not isinstance(ticket, dict) or "id" not in ticket:
            raise RuntimeError("ERROR: Freshdesk did not return a ticket id.")
        return f"#{ticket['id']} created"


def _register() -> FreshdeskConnector:
    from zeline.connectors import register

    return register(FreshdeskConnector())


_register()
