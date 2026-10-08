"""SendGrid connector (API key, Bearer auth).

Talks to the SendGrid v3 API at ``https://api.sendgrid.com/v3``.
"""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.sendgrid.com/v3"
_TIMEOUT = 30


class SendGridConnector(BaseConnector):
    id = "sendgrid"
    name = "SendGrid"
    description = "Send transactional emails via the SendGrid v3 API."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/scopes",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.sendgrid.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: SendGrid rejected the API key (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key})
        return "Connected to SendGrid."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "SendGrid disconnected."
        return "SendGrid was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        api_key = data.get("api_key", "")
        if not api_key:
            raise RuntimeError(
                "ERROR: SendGrid is not connected. Run 'zeline connect sendgrid' first."
            )
        return {"Authorization": f"Bearer {api_key}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | str:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: SendGrid API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: SendGrid API {resp.status_code} on {path}.")
        if not resp.text:
            return ""
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- user-facing operations -------------------------------------------

    def send_email(self, to_email: str, subject: str, body: str, from_email: str) -> str:
        """NETWORK. Send a plain-text email via SendGrid."""
        to_email = (to_email or "").strip()
        subject = (subject or "").strip()
        body = (body or "").strip()
        from_email = (from_email or "").strip()
        if not to_email:
            return "ERROR: no to_email provided."
        if not subject:
            return "ERROR: no subject provided."
        if not body:
            return "ERROR: no body provided."
        if not from_email:
            return "ERROR: no from_email provided."
        self._api(
            "POST",
            "/mail/send",
            json={
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": from_email},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}],
            },
        )
        return f"Email sent to {to_email}."


def _register() -> SendGridConnector:
    from zeline.connectors import register

    return register(SendGridConnector())


_register()
