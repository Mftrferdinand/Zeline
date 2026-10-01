"""WhatsApp connector (Business Cloud API).

The operator links it once with ``zeline connect whatsapp``: paste a
WhatsApp Business Cloud API access token plus the phone number ID from the
Meta Developer dashboard. The token is validated (GET the phone number's
profile) before anything is stored.

https://developers.facebook.com/docs/whatsapp/cloud-api
"""
from __future__ import annotations

import re

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://graph.facebook.com/v21.0"
_TIMEOUT = 30


def _normalize_number(to: str) -> str:
    """Strip spaces/dashes; keep digits with an optional leading ``+``."""
    cleaned = re.sub(r"[^\d+]", "", (to or "").strip())
    if cleaned.startswith("+"):
        cleaned = "+" + re.sub(r"\D", "", cleaned[1:])
    return cleaned


class WhatsAppConnector(BaseConnector):
    id = "whatsapp"
    name = "WhatsApp"
    description = "Send WhatsApp messages via the Business Cloud API."
    auth_kind = "token"

    def connect(
        self,
        access_token: str = "",
        phone_number_id: str = "",
        business_account_id: str = "",
        **kwargs,
    ) -> str:
        access_token = (access_token or kwargs.get("access_token") or "").strip()
        phone_number_id = (phone_number_id or kwargs.get("phone_number_id") or "").strip()
        business_account_id = (business_account_id or kwargs.get("business_account_id") or "").strip()
        if not access_token or not phone_number_id:
            return "ERROR: access_token and phone_number_id are required."
        try:
            resp = requests.get(
                f"{API_BASE}/{phone_number_id}",
                params={"fields": "display_phone_number,verified_name"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach graph.facebook.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: WhatsApp rejected the credentials (HTTP {resp.status_code})."
        data = resp.json()
        number = data.get("display_phone_number", "?")
        verified = data.get("verified_name", "")
        store.save(
            self.id,
            {
                "access_token": access_token,
                "phone_number_id": phone_number_id,
                "business_account_id": business_account_id,
                "display_phone_number": number,
                "verified_name": verified,
            },
        )
        label = f"Connected to WhatsApp number {number}"
        if verified:
            label += f" ({verified})"
        return label + "."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "WhatsApp disconnected."
        return "WhatsApp was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token") or not data.get("phone_number_id"):
            return {"connected": False, "detail": "not linked"}
        number = data.get("display_phone_number") or data.get("phone_number_id", "?")
        digits = re.sub(r"\D", "", str(number))
        masked = f"•••{digits[-4:]}" if len(digits) >= 4 else number
        return {"connected": True, "detail": masked}

    # -- API helpers -----------------------------------------------------

    def _creds(self) -> dict:
        data = store.load(self.id) or {}
        if not data.get("access_token") or not data.get("phone_number_id"):
            raise RuntimeError(
                "ERROR: WhatsApp not connected. "
                "The owner can run `zeline connect whatsapp` to link it."
            )
        return data

    def _post_message(self, payload: dict) -> str:
        creds = self._creds()
        try:
            resp = requests.post(
                f"{API_BASE}/{creds['phone_number_id']}/messages",
                headers={"Authorization": f"Bearer {creds['access_token']}"},
                json=payload,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: WhatsApp API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            detail = resp.text[:200].replace("\n", " ")
            raise RuntimeError(f"ERROR: WhatsApp API {resp.status_code} ({detail}).")
        body = resp.json()
        msg_id = ""
        messages = body.get("messages") or []
        if messages:
            msg_id = messages[0].get("id", "")
        return f"Message sent (id {msg_id})." if msg_id else "Message sent."

    # -- user-facing operations -------------------------------------------

    def send_text(self, to: str, text: str) -> str:
        target = _normalize_number(to)
        if not target or not re.sub(r"\D", "", target):
            return "ERROR: recipient number is empty."
        if not (text or "").strip():
            return "ERROR: message text is empty."
        return self._post_message(
            {
                "messaging_product": "whatsapp",
                "to": target,
                "type": "text",
                "text": {"body": text, "preview_url": False},
            }
        )

    def send_template(
        self,
        to: str,
        template: str,
        language: str = "en_US",
        parameters: list | None = None,
    ) -> str:
        target = _normalize_number(to)
        if not target or not re.sub(r"\D", "", target):
            return "ERROR: recipient number is empty."
        template = (template or "").strip()
        if not template:
            return "ERROR: template name is empty."
        components = []
        if parameters:
            components = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(p)} for p in parameters
                    ],
                }
            ]
        return self._post_message(
            {
                "messaging_product": "whatsapp",
                "to": target,
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": (language or "en_US").strip() or "en_US"},
                    **({"components": components} if components else {}),
                },
            }
        )


def _register() -> WhatsAppConnector:
    from zeline.connectors import register

    return register(WhatsAppConnector())


_register()
