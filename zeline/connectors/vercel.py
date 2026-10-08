"""Vercel connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.vercel.com"
_TIMEOUT = 30


class VercelConnector(BaseConnector):
    id = "vercel"
    name = "Vercel"
    description = "List deployments and inspect their state on Vercel."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/v2/user",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.vercel.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Vercel rejected the token (HTTP {resp.status_code})."
        username = resp.json().get("user", {}).get("username", "?")
        store.save(self.id, {"token": token, "user": username})
        return f"Connected to Vercel as @{username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Vercel disconnected."
        return "Vercel was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('user', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        token = data.get("token", "")
        if not token:
            raise RuntimeError("ERROR: Vercel is not connected. Run 'zeline connect vercel' first.")
        return {"Authorization": f"Bearer {token}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Vercel API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Vercel API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_deployments(self, limit: int = 10) -> str:
        """List recent deployments; one "url [state] (created)" line each."""
        data = self._api("GET", "/v6/deployments", params={"limit": max(1, min(limit, 100))})
        deployments = data.get("deployments", []) if isinstance(data, dict) else []
        lines = []
        for dep in deployments[:limit]:
            lines.append(
                f"{dep.get('url', '?')} [{dep.get('state', '?')}] "
                f"({dep.get('createdAt', '?')})"
            )
        return "\n".join(lines) if lines else "No deployments found."


def _register() -> VercelConnector:
    from zeline.connectors import register

    return register(VercelConnector())


_register()
