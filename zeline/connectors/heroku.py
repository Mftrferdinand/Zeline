"""Heroku connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.heroku.com"
_TIMEOUT = 30
_API_HEADERS = {"Accept": "application/vnd.heroku+json; version=3"}


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class HerokuConnector(BaseConnector):
    id = "heroku"
    name = "Heroku"
    description = "Read Heroku apps."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("token") or "").strip()
        if not api_token:
            return "ERROR: no API token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/account",
                headers={"Authorization": f"Bearer {api_token}", **_API_HEADERS},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.heroku.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Heroku rejected the API token (HTTP {resp.status_code})."
        try:
            email = resp.json().get("email", "?")
        except ValueError:
            email = "?"
        store.save(self.id, {"api_token": api_token})
        return f"Connected to Heroku (account {email})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Heroku disconnected."
        return "Heroku was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API token stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Heroku is not connected. Run: zeline connect heroku")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_token', '')}", **_API_HEADERS}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Heroku API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Heroku API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Heroku returned an unreadable response.") from None

    # -- user-facing operations ------------------------------------------

    def list_apps(self, limit: int = 10) -> str:
        """[READ] List Heroku apps (name, region, stack)."""
        limit = _clamp(limit)
        data = self._api("GET", "/apps")
        apps = data if isinstance(data, list) else []
        lines = []
        for app in apps[:limit]:
            region = app.get("region") or {}
            stack = app.get("stack") or {}
            lines.append(
                f"{app.get('name', '?')} "
                f"(region: {region.get('name', '?')}, stack: {stack.get('name', '?')})"
            )
        return "\n".join(lines) if lines else "No apps found."


def _register() -> HerokuConnector:
    from zeline.connectors import register

    return register(HerokuConnector())


_register()
