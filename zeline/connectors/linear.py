"""Linear connector (personal API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_URL = "https://api.linear.app/graphql"
_TIMEOUT = 30


class LinearConnector(BaseConnector):
    id = "linear"
    name = "Linear"
    description = "List issues and create issues in Linear."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            data = self._graphql({"query": "{ viewer { name } }"}, api_key=api_key)
        except RuntimeError as exc:
            return str(exc)
        viewer = data.get("viewer") or {}
        name = viewer.get("name") or "?"
        if not viewer:
            return "ERROR: Linear rejected the API key."
        store.save(self.id, {"api_key": api_key, "user": name})
        return f"Connected to Linear as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Linear disconnected."
        return "Linear was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("user", "?")}

    # -- API helpers -----------------------------------------------------

    def _headers(self, api_key: str | None = None) -> dict:
        key = api_key if api_key is not None else (store.load(self.id) or {}).get("api_key", "")
        # Linear takes the raw key; no "Bearer" prefix.
        return {"Authorization": key, "Content-Type": "application/json"}

    def _graphql(self, payload: dict, api_key: str | None = None) -> dict:
        try:
            resp = requests.post(API_URL, json=payload, headers=self._headers(api_key), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: could not reach api.linear.app ({exc}).") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"ERROR: Linear API rejected the request (HTTP {resp.status_code}).")
        try:
            body = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Linear returned an unreadable response.") from None
        if not isinstance(body, dict):
            return {}
        errors = body.get("errors") or []
        if errors:
            messages = "; ".join(
                err.get("message", "?") for err in errors if isinstance(err, dict)
            )
            raise RuntimeError(f"ERROR: Linear GraphQL error: {messages}.")
        return body.get("data") or {}

    # -- user-facing operations -------------------------------------------

    def list_issues(self, limit: int = 10) -> str:
        n = max(1, min(limit, 100))
        data = self._graphql(
            {"query": f"{{ issues(first: {n}) {{ nodes {{ identifier title state {{ name }} }} }} }}"}
        )
        nodes = (data.get("issues") or {}).get("nodes") or []
        lines = []
        for issue in nodes[:n]:
            state = (issue.get("state") or {}).get("name", "?")
            lines.append(f"{issue.get('identifier', '?')} {issue.get('title', '')} [{state}]")
        return "\n".join(lines) if lines else "No issues found."

    def create_issue(self, team_id: str, title: str, description: str = "") -> str:
        data = self._graphql(
            {
                "query": (
                    "mutation ($input: IssueCreateInput!) { "
                    "issueCreate(input: $input) { success issue { identifier title url } } }"
                ),
                "variables": {
                    "input": {"teamId": team_id, "title": title, "description": description}
                },
            }
        )
        result = data.get("issueCreate") or {}
        issue = result.get("issue") or {}
        if not result.get("success") or not issue:
            raise RuntimeError("ERROR: Linear issueCreate did not succeed.")
        return f"{issue.get('identifier', '?')} {issue.get('url', '')}".strip()


def _register() -> LinearConnector:
    from zeline.connectors import register

    return register(LinearConnector())


_register()
