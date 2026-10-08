"""Zendesk connector (email + API token, HTTP Basic auth).

Talks to the Zendesk Support API v2 at
``https://{subdomain}.zendesk.com/api/v2``.
"""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _normalize_subdomain(subdomain: str) -> str:
    sub = (subdomain or "").strip().lower()
    if sub.endswith(".zendesk.com"):
        sub = sub[: -len(".zendesk.com")]
    return sub


class ZendeskConnector(BaseConnector):
    id = "zendesk"
    name = "Zendesk"
    description = "List and create support tickets in Zendesk."
    auth_kind = "pat"

    def connect(self, email: str = "", token: str = "", subdomain: str = "", **kwargs) -> str:
        email = (email or kwargs.get("email") or "").strip()
        token = (token or kwargs.get("token") or "").strip()
        subdomain = _normalize_subdomain(subdomain or kwargs.get("subdomain") or "")
        if not email:
            return "ERROR: no email provided."
        if not token:
            return "ERROR: no API token provided."
        if not subdomain:
            return "ERROR: no subdomain provided."
        try:
            resp = requests.get(
                f"https://{subdomain}.zendesk.com/api/v2/users/me.json",
                auth=(f"{email}/token", token),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {subdomain}.zendesk.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Zendesk rejected the credentials (HTTP {resp.status_code})."
        try:
            name = resp.json().get("user", {}).get("name", "?")
        except ValueError:
            return "ERROR: Zendesk returned an unreadable response."
        store.save(self.id, {
            "email": email,
            "token": token,
            "subdomain": subdomain,
        })
        return f"Connected to Zendesk ({subdomain}) as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Zendesk disconnected."
        return "Zendesk was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token") or not data.get("subdomain"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("subdomain", "linked")}

    # -- API helpers -----------------------------------------------------

    def _auth(self) -> tuple[tuple[str, str], str]:
        data = store.load(self.id) or {}
        email = data.get("email", "")
        token = data.get("token", "")
        subdomain = data.get("subdomain", "")
        if not email or not token or not subdomain:
            raise RuntimeError("ERROR: Zendesk is not connected. Run 'zeline connect zendesk' first.")
        return (f"{email}/token", token), subdomain

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        auth, subdomain = self._auth()
        try:
            resp = requests.request(
                method,
                f"https://{subdomain}.zendesk.com/api/v2{path}",
                auth=auth,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Zendesk API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Zendesk API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_tickets(self, limit: int = 10) -> str:
        """READ. List tickets as ``#id subject [status]`` lines."""
        data = self._api(
            "GET",
            "/tickets.json",
            params={"per_page": max(1, min(limit, 100))},
        )
        tickets = data.get("tickets", []) if isinstance(data, dict) else []
        lines = []
        for ticket in tickets[:limit]:
            lines.append(
                f"#{ticket.get('id', '?')} {ticket.get('subject', '')} [{ticket.get('status', '?')}]"
            )
        return "\n".join(lines) if lines else "No tickets found."

    def create_ticket(self, subject: str, comment: str, priority: str = "normal") -> str:
        """NETWORK. Create a ticket. Returns the created ticket id."""
        subject = (subject or "").strip()
        comment = (comment or "").strip()
        priority = (priority or "normal").strip()
        if not subject:
            return "ERROR: no subject provided."
        if not comment:
            return "ERROR: no comment provided."
        ticket = self._api(
            "POST",
            "/tickets.json",
            json={
                "ticket": {
                    "subject": subject,
                    "comment": {"body": comment},
                    "priority": priority,
                }
            },
        )
        tid = ticket.get("ticket", {}).get("id", "?") if isinstance(ticket, dict) else "?"
        return f"Created ticket #{tid}."


def _register() -> ZendeskConnector:
    from zeline.connectors import register

    return register(ZendeskConnector())


_register()
