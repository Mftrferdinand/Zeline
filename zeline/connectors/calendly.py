"""Calendly connector (personal access token).

Lists scheduled events for the authenticated user. The user's canonical
URI is captured at connect time and reused for event listing.
"""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.calendly.com"
_TIMEOUT = 30


class CalendlyConnector(BaseConnector):
    id = "calendly"
    name = "Calendly"
    description = "List scheduled events in Calendly."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/users/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.calendly.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Calendly rejected the token (HTTP {resp.status_code})."
        try:
            resource = resp.json().get("resource", {})
        except ValueError:
            return "ERROR: Calendly returned an unreadable response."
        user_uri = resource.get("uri", "")
        name = resource.get("name", "?")
        store.save(self.id, {"token": token, "user_uri": user_uri})
        return f"Connected to Calendly as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Calendly disconnected."
        return "Calendly was not connected."

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
            raise RuntimeError("ERROR: Calendly is not connected. Run 'zeline connect calendly' first.")
        return {"Authorization": f"Bearer {token}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Calendly API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Calendly API {resp.status_code} on {path}.")
        return resp.json()

    def _user_uri(self) -> str:
        data = store.load(self.id) or {}
        user_uri = data.get("user_uri", "")
        if not user_uri:
            raise RuntimeError("ERROR: Calendly user URI is missing. Run 'zeline connect calendly' again.")
        return user_uri

    # -- user-facing operations -------------------------------------------

    def list_events(self, limit: int = 10) -> str:
        """READ. List scheduled events as ``name (start_time)`` lines."""
        data = self._api(
            "GET",
            "/scheduled_events",
            params={
                "user": self._user_uri(),
                "count": max(1, min(limit, 100)),
            },
        )
        events = data.get("collection", []) if isinstance(data, dict) else []
        lines = []
        for event in events[:limit]:
            lines.append(f"{event.get('name', '?')} ({event.get('start_time', '?')})")
        return "\n".join(lines) if lines else "No scheduled events found."


def _register() -> CalendlyConnector:
    from zeline.connectors import register

    return register(CalendlyConnector())


_register()
