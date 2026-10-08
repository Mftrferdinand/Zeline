"""Fly.io connector (GraphQL personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_URL = "https://api.fly.io/graphql"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class FlyioConnector(BaseConnector):
    id = "flyio"
    name = "Fly.io"
    description = "Read Fly.io apps via GraphQL."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("token") or "").strip()
        if not api_token:
            return "ERROR: no API token provided."
        try:
            resp = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Content-Type": "application/json",
                },
                json={"query": "{ apps(first: 1) { nodes { id } } }"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.fly.io ({exc})."
        if resp.status_code >= 400:
            return f"ERROR: Fly.io rejected the API token (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            return "ERROR: Fly.io returned an unreadable response."
        if isinstance(payload, dict) and payload.get("errors"):
            return "ERROR: Fly.io returned GraphQL errors; token not saved."
        if not isinstance(payload, dict) or "data" not in payload:
            return "ERROR: Fly.io returned an unexpected response."
        store.save(self.id, {"api_token": api_token})
        return "Connected to Fly.io."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Fly.io disconnected."
        return "Fly.io was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API token stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Fly.io is not connected. Run: zeline connect flyio")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {
            "Authorization": f"Bearer {data.get('api_token', '')}",
            "Content-Type": "application/json",
        }

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """POST a GraphQL query. Raises RuntimeError("ERROR: ...") on failure."""
        self._require_connected()
        body = {"query": query}
        if variables:
            body["variables"] = variables
        try:
            resp = requests.post(
                API_URL, headers=self._headers(), json=body, timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Fly.io API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Fly.io API {resp.status_code}.")
        try:
            payload = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Fly.io returned an unreadable response.") from None
        if not isinstance(payload, dict):
            raise RuntimeError("ERROR: Fly.io returned an unreadable response.")
        if payload.get("errors"):
            raise RuntimeError(f"ERROR: Fly.io GraphQL errors: {payload['errors']}.")
        return payload

    # -- user-facing operations -------------------------------------------

    def list_apps(self, limit: int = 10) -> str:
        """List Fly.io apps with their status. [READ]"""
        limit = _clamp(limit)
        data = self._graphql(
            f"{{ apps(first: {limit}) {{ nodes {{ id name status }} }} }}"
        )
        nodes = (((data.get("data") or {}).get("apps") or {}).get("nodes") or [])
        lines = []
        for app in nodes[:limit]:
            if not isinstance(app, dict):
                continue
            name = app.get("name", "(no name)")
            status = app.get("status", "(unknown status)")
            lines.append(f"{name}: {status}")
        return "\n".join(lines) if lines else "No apps found."


def _register() -> FlyioConnector:
    from zeline.connectors import register

    return register(FlyioConnector())


_register()
