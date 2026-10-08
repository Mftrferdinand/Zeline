"""Intercom connector (bearer access token, Intercom API v2.11)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.intercom.io"
_TIMEOUT = 30
_INTERCOM_VERSION = "2.11"


class IntercomConnector(BaseConnector):
    id = "intercom"
    name = "Intercom"
    description = "List customer conversations in Intercom."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        headers = {
            "Authorization": f"Bearer {token}",
            "Intercom-Version": _INTERCOM_VERSION,
        }
        try:
            resp = requests.get(f"{API_BASE}/me", headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.intercom.io ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Intercom rejected the token (HTTP {resp.status_code})."
        store.save(self.id, {"token": token})
        return "Connected to Intercom."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Intercom disconnected."
        return "Intercom was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        token = data.get("token", "")
        if not token:
            raise RuntimeError("ERROR: Intercom is not connected. Run 'zeline connect intercom' first.")
        return {
            "Authorization": f"Bearer {token}",
            "Intercom-Version": _INTERCOM_VERSION,
        }

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Intercom API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Intercom API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_conversations(self, limit: int = 10) -> str:
        """READ. List conversations as ``id: title [state]`` lines."""
        data = self._api(
            "GET",
            "/conversations",
            params={"per_page": max(1, min(limit, 100))},
        )
        convos = data.get("conversations", []) if isinstance(data, dict) else []
        lines = []
        for convo in convos[:limit]:
            lines.append(
                f"{convo.get('id', '?')}: {convo.get('title', '')} [{convo.get('state', '?')}]"
            )
        return "\n".join(lines) if lines else "No conversations found."


def _register() -> IntercomConnector:
    from zeline.connectors import register

    return register(IntercomConnector())


_register()
