"""Mailgun connector (API key + domain)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.mailgun.net/v3"
_TIMEOUT = 30


class MailgunConnector(BaseConnector):
    id = "mailgun"
    name = "Mailgun"
    description = "Send email and list events via Mailgun."
    auth_kind = "pat"

    def connect(self, api_key: str = "", domain: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        domain = (domain or kwargs.get("domain") or "").strip()
        if not api_key or not domain:
            return "ERROR: api_key and domain are required."
        try:
            resp = requests.get(
                f"{API_BASE}/{domain}",
                auth=("api", api_key),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.mailgun.net ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Mailgun rejected the key or domain (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key, "domain": domain})
        return f"Connected to Mailgun (domain {domain})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Mailgun disconnected."
        return "Mailgun was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key") or not data.get("domain"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"domain {data.get('domain')}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> dict:
        data = store.load(self.id) or {}
        if not data.get("api_key") or not data.get("domain"):
            raise RuntimeError("ERROR: Mailgun is not connected. Run: zeline connect mailgun")
        return data

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        data = self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.request(
                method,
                f"{API_BASE}/{data['domain']}{path}",
                auth=("api", data["api_key"]),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Mailgun API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Mailgun API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def send_email(self, from_addr: str, to: str, subject: str, text: str) -> str:
        result = self._api(
            "POST",
            "/messages",
            data={"from": from_addr, "to": to, "subject": subject, "text": text},
        )
        return f"Email queued: {result.get('id', '?')}"

    def list_messages(self, limit: int = 10) -> str:
        data = self._api(
            "GET",
            "/events",
            params={"limit": max(1, min(limit, 100))},
        )
        items = data.get("items", []) if isinstance(data, dict) else []
        lines = []
        for item in items[:limit]:
            lines.append(f"{item.get('timestamp', '?')} {item.get('event', '?')} — {item.get('recipient', '?')}")
        return "\n".join(lines) if lines else "No messages found."


def _register() -> MailgunConnector:
    from zeline.connectors import register

    return register(MailgunConnector())


_register()
