"""Resend connector (API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.resend.com"
_TIMEOUT = 30


class ResendConnector(BaseConnector):
    id = "resend"
    name = "Resend"
    description = "Send email via Resend."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        if not api_key:
            return "ERROR: api_key is required."
        try:
            resp = requests.get(
                f"{API_BASE}/domains",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.resend.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Resend rejected the key (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key})
        return "Connected to Resend."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Resend disconnected."
        return "Resend was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_key', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        if not self.is_connected():
            raise RuntimeError("ERROR: Resend is not connected. Run: zeline connect resend")
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Resend API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Resend API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def send_email(self, from_addr: str, to, subject: str, html: str) -> str:
        recipients = [to] if isinstance(to, str) else list(to)
        result = self._api(
            "POST",
            "/emails",
            json={"from": from_addr, "to": recipients, "subject": subject, "html": html},
        )
        return f"Email sent: {result.get('id', '?')}"


def _register() -> ResendConnector:
    from zeline.connectors import register

    return register(ResendConnector())


_register()
