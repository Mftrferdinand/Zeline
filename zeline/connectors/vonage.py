"""Vonage connector (API key + API secret)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://rest.nexmo.com"
_TIMEOUT = 30


class VonageConnector(BaseConnector):
    id = "vonage"
    name = "Vonage"
    description = "Send SMS via Vonage."
    auth_kind = "pat"

    def connect(self, api_key: str = "", api_secret: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        api_secret = (api_secret or kwargs.get("api_secret") or "").strip()
        if not api_key or not api_secret:
            return "ERROR: api_key and api_secret are required."
        try:
            resp = requests.get(
                f"{API_BASE}/account/get-balance",
                params={"api_key": api_key, "api_secret": api_secret},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach rest.nexmo.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Vonage rejected the credentials (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key, "api_secret": api_secret})
        return "Connected to Vonage."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Vonage disconnected."
        return "Vonage was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key") or not data.get("api_secret"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _creds(self) -> dict:
        data = store.load(self.id) or {}
        api_key = data.get("api_key", "")
        api_secret = data.get("api_secret", "")
        if not api_key or not api_secret:
            raise RuntimeError("ERROR: Vonage is not connected. Run: zeline connect vonage")
        return {"api_key": api_key, "api_secret": api_secret}

    # -- user-facing operations -------------------------------------------

    def send_sms(self, to: str, from_name: str, text: str) -> str:
        creds = self._creds()
        payload = {
            "api_key": creds["api_key"],
            "api_secret": creds["api_secret"],
            "to": to,
            "from": from_name,
            "text": text,
        }
        try:
            resp = requests.post(f"{API_BASE}/sms/json", json=payload, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Vonage API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Vonage API {resp.status_code} on /sms/json.")
        try:
            messages = resp.json().get("messages", [])
        except ValueError as exc:
            raise RuntimeError("ERROR: Vonage returned an unreadable response.") from exc
        message = messages[0] if messages else {}
        if message.get("status") == "0":
            return f"SMS sent to {to}."
        error_text = message.get("error-text", "unknown error")
        raise RuntimeError(f"ERROR: Vonage SMS failed ({error_text}).")


def _register() -> VonageConnector:
    from zeline.connectors import register

    return register(VonageConnector())


_register()
