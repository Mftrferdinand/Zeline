"""OneSignal connector (app id + REST API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.onesignal.com"
_TIMEOUT = 30


class OneSignalConnector(BaseConnector):
    id = "onesignal"
    name = "OneSignal"
    description = "Send push notifications via OneSignal."
    auth_kind = "pat"

    def connect(self, app_id: str = "", api_key: str = "", **kwargs) -> str:
        app_id = (app_id or kwargs.get("app_id") or "").strip()
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        if not app_id or not api_key:
            return "ERROR: app_id and api_key are required."
        try:
            resp = requests.get(
                f"{API_BASE}/apps/{app_id}",
                headers={"Authorization": f"Basic {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.onesignal.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: OneSignal rejected the credentials (HTTP {resp.status_code})."
        store.save(self.id, {"app_id": app_id, "api_key": api_key})
        return "Connected to OneSignal."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "OneSignal disconnected."
        return "OneSignal was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("app_id") or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _creds(self) -> dict:
        data = store.load(self.id) or {}
        app_id = data.get("app_id", "")
        api_key = data.get("api_key", "")
        if not app_id or not api_key:
            raise RuntimeError("ERROR: OneSignal is not connected. Run: zeline connect onesignal")
        return {"app_id": app_id, "api_key": api_key}

    # -- user-facing operations -------------------------------------------

    def send_push(self, title: str, message: str, segments: list | None = None) -> str:
        creds = self._creds()
        segments = list(segments) if segments else ["All"]
        payload = {
            "app_id": creds["app_id"],
            "included_segments": segments,
            "headings": {"en": title},
            "contents": {"en": message},
        }
        try:
            resp = requests.post(
                f"{API_BASE}/notifications",
                headers={"Authorization": f"Basic {creds['api_key']}"},
                json=payload,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: OneSignal API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: OneSignal API {resp.status_code} on /notifications.")
        try:
            notification_id = resp.json().get("id", "?")
        except ValueError as exc:
            raise RuntimeError("ERROR: OneSignal returned an unreadable response.") from exc
        return f"Push sent: {notification_id}"


def _register() -> OneSignalConnector:
    from zeline.connectors import register

    return register(OneSignalConnector())


_register()
