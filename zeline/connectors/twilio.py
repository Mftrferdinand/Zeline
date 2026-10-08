"""Twilio connector (account SID + auth token, HTTP Basic auth).

Talks to the Twilio REST API v2010 at ``https://api.twilio.com/2010-04-01``.
"""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.twilio.com/2010-04-01"
_TIMEOUT = 30


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), 100))


class TwilioConnector(BaseConnector):
    id = "twilio"
    name = "Twilio"
    description = "Send and list SMS messages via the Twilio REST API."
    auth_kind = "pat"

    def connect(self, account_sid: str = "", auth_token: str = "", **kwargs) -> str:
        account_sid = (account_sid or kwargs.get("account_sid") or "").strip()
        auth_token = (auth_token or kwargs.get("auth_token") or "").strip()
        if not account_sid:
            return "ERROR: no account SID provided."
        if not auth_token:
            return "ERROR: no auth token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/Accounts/{account_sid}.json",
                auth=(account_sid, auth_token),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.twilio.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Twilio rejected the credentials (HTTP {resp.status_code})."
        store.save(self.id, {"account_sid": account_sid, "auth_token": auth_token})
        return f"Connected to Twilio (account {account_sid})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Twilio disconnected."
        return "Twilio was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("account_sid") or not data.get("auth_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"account {data.get('account_sid')}"}

    # -- API helpers -----------------------------------------------------

    def _auth(self) -> tuple[tuple[str, str], str]:
        data = store.load(self.id) or {}
        account_sid = data.get("account_sid", "")
        auth_token = data.get("auth_token", "")
        if not account_sid or not auth_token:
            raise RuntimeError("ERROR: Twilio is not connected. Run 'zeline connect twilio' first.")
        return (account_sid, auth_token), account_sid

    def _api(self, method: str, path: str, **kwargs) -> dict:
        kwargs.setdefault("timeout", _TIMEOUT)
        auth, account_sid = self._auth()
        try:
            resp = requests.request(
                method,
                f"{API_BASE}/Accounts/{account_sid}{path}",
                auth=auth,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Twilio API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Twilio API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def send_sms(self, from_number: str, to_number: str, body: str) -> str:
        """NETWORK. Send an SMS from *from_number* to *to_number*. Returns the message SID."""
        from_number = (from_number or "").strip()
        to_number = (to_number or "").strip()
        body = (body or "").strip()
        if not from_number:
            return "ERROR: no from_number provided."
        if not to_number:
            return "ERROR: no to_number provided."
        if not body:
            return "ERROR: no body provided."
        data = self._api(
            "POST",
            "/Messages.json",
            data={"From": from_number, "To": to_number, "Body": body},
        )
        return f"SMS sent (SID {data.get('sid', '?')})."

    def list_messages(self, limit: int = 10) -> str:
        """READ. List recent messages as ``from → to: body (status)`` lines."""
        data = self._api("GET", "/Messages.json", params={"PageSize": _clamp_limit(limit)})
        messages = data.get("messages", []) if isinstance(data, dict) else []
        lines = []
        for msg in messages[:limit]:
            body = " ".join((msg.get("body") or "").split())
            lines.append(
                f"{msg.get('from', '?')} → {msg.get('to', '?')}: {body} ({msg.get('status', '?')})"
            )
        return "\n".join(lines) if lines else "No messages found."


def _register() -> TwilioConnector:
    from zeline.connectors import register

    return register(TwilioConnector())


_register()
