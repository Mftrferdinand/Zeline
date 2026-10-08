"""Railway connector (personal access token, GraphQL)."""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_URL = "https://backboard.railway.app/graphql/v2"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class RailwayConnector(BaseConnector):
    id = "railway"
    name = "Railway"
    description = "Read Railway projects via GraphQL."
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
                json={"query": "{ projects(first: 1) { edges { node { id } } } }"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach backboard.railway.app ({exc})."
        if resp.status_code >= 400:
            return f"ERROR: Railway rejected the token (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: Railway returned an unreadable response."
        if not isinstance(body, dict) or "data" not in body:
            return "ERROR: Railway rejected the token (invalid response)."
        if body.get("errors"):
            return "ERROR: Railway rejected the token (GraphQL errors)."
        store.save(self.id, {"api_token": api_token})
        return "Connected to Railway."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Railway disconnected."
        return "Railway was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API token stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Railway is not connected. Run: zeline connect railway")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {
            "Authorization": f"Bearer {data.get('api_token', '')}",
            "Content-Type": "application/json",
        }

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """POST a GraphQL query and return the ``data`` dict."""
        self._require_connected()
        payload = {"query": query, "variables": variables}
        try:
            resp = requests.post(API_URL, headers=self._headers(), json=payload, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Railway API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Railway API {resp.status_code}.")
        try:
            body = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Railway returned an unreadable response.") from None
        if not isinstance(body, dict):
            raise RuntimeError("ERROR: Railway returned an unreadable response.")
        if body.get("errors"):
            raise RuntimeError(f"ERROR: Railway GraphQL errors: {body['errors']}.")
        data = body.get("data")
        if data is None:
            raise RuntimeError("ERROR: Railway returned no data.")
        return data

    # -- user-facing operations -------------------------------------------

    def list_projects(self, limit: int = 10) -> str:
        """List Railway projects [READ]."""
        limit = _clamp(limit)
        data = self._graphql("{ projects(first: %d) { edges { node { id name } } } }" % limit)
        projects = data.get("projects") if isinstance(data, dict) else None
        edges = projects.get("edges") if isinstance(projects, dict) else []
        lines = []
        for edge in edges or []:
            node = (edge or {}).get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict):
                continue
            lines.append(f"{node.get('id', '?')}: {node.get('name', '(no name)')}")
        return "\n".join(lines) if lines else "No projects found."


def _register() -> RailwayConnector:
    from zeline.connectors import register

    return register(RailwayConnector())


_register()
