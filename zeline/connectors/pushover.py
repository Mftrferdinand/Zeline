"""Pushover connector (user key + app token).

Talks to the Pushover API at ``https://api.pushover.net/1``. Credentials are
sent as form fields (``token`` = app token, ``user`` = user key) per the
Pushover API convention.
"""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.pushover.net/1"
_TIMEOUT = 30


class PushoverConnector(BaseConnector):
    id = "pushover"
    name = "Pushover"
    description = "Send push notifications via the Pushover API."
    auth_kind = "pat"

    def connect(self, user_key: str = "", app_token: str = "", **kwargs) -> str:
        user_key = (user_key or kwargs.get("user_key") or "").strip()
        app_token = (app_token or kwargs.get("app_token") or "").strip()
        if not user_key:
            return "ERROR: no user key provided."
        if not app_token:
            return "ERROR: no app token provided."
        try:
            resp = requests.post(
                f"{API_BASE}/users/validate.json",
                data={"token": app_token, "user": user_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.pushover.net ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Pushover rejected the credentials (HTTP {resp.status_code})."
        try:
            ok = resp.json().get("status") == 1
        except ValueError:
            ok = False
        if not ok:
            return "ERROR: Pushover rejected the credentials (validation failed)."
        store.save(self.id, {"user_key": user_key, "app_token": app_token})
        return "Connected to Pushover."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Pushover disconnected."
        return "Pushover was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("user_key") or not data.get("app_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _creds(self) -> tuple[str, str]:
        data = store.load(self.id) or {}
        user_key = data.get("user_key", "")
        app_token = data.get("app_token", "")
        if not user_key or not app_token:
            raise RuntimeError(
                "ERROR: Pushover is not connected. Run 'zeline connect pushover' first."
            )
        return user_key, app_token

    def _api(self, method: str, path: str, **kwargs) -> dict:
        kwargs.setdefault("timeout", _TIMEOUT)
        user_key, app_token = self._creds()
        data = kwargs.pop("data", {}) or {}
        data.setdefault("token", app_token)
        data.setdefault("user", user_key)
        try:
            resp = requests.request(method, f"{API_BASE}{path}", data=data, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Pushover API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Pushover API {resp.status_code} on {path}.")
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("status") != 1:
            errors = payload.get("errors") or ["unknown error"]
            raise RuntimeError(f"ERROR: Pushover API error: {errors[0]}.")
        return payload

    # -- user-facing operations -------------------------------------------

    def send_notification(self, message: str, title: str = "", priority: int = 0) -> str:
        """NETWORK. Send a push notification. *priority* is clamped to -2..2."""
        message = (message or "").strip()
        title = (title or "").strip()
        if not message:
            return "ERROR: no message provided."
        priority = max(-2, min(int(priority), 2))
        fields = {"message": message, "priority": priority}
        if title:
            fields["title"] = title
        data = self._api("POST", "/messages.json", data=fields)
        return f"sent (request {data.get('request', '?')})"


def _register() -> PushoverConnector:
    from zeline.connectors import register

    return register(PushoverConnector())


_register()
