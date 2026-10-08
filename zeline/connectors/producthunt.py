"""Product Hunt connector (API token, GraphQL)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_URL = "https://api.producthunt.com/v2/api/graphql"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class ProductHuntConnector(BaseConnector):
    id = "producthunt"
    name = "Product Hunt"
    description = "Browse today's top Product Hunt launches and search posts."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("api_token") or kwargs.get("token") or "").strip()
        if not api_token:
            return "ERROR: no API token provided."
        try:
            resp = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {api_token}"},
                json={"query": "{ viewer { user { id } } }"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.producthunt.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Product Hunt rejected the token (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: Product Hunt returned an unreadable response."
        if not isinstance(body, dict) or body.get("errors"):
            return "ERROR: Product Hunt rejected the token (GraphQL errors)."
        store.save(self.id, {"api_token": api_token})
        return "Connected to Product Hunt."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Product Hunt disconnected."
        return "Product Hunt was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API token"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        data = store.load(self.id) or {}
        if not data.get("api_token"):
            raise RuntimeError("ERROR: Product Hunt is not connected. Run: zeline connect producthunt")

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        self._require_connected()
        data = store.load(self.id) or {}
        try:
            resp = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {data.get('api_token', '')}"},
                json={"query": query, "variables": variables or {}},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Product Hunt API request failed ({exc}).") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"ERROR: Product Hunt API {resp.status_code}.")
        try:
            body = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Product Hunt returned an unreadable response.") from None
        if body.get("errors"):
            message = (body["errors"][0] or {}).get("message", "unknown error")
            raise RuntimeError(f"ERROR: Product Hunt GraphQL error: {message}.")
        return body.get("data") or {}

    def _format_posts(self, posts: list) -> str:
        lines = []
        for edge in posts or []:
            node = (edge or {}).get("node") or {}
            name = node.get("name", "?")
            tagline = (node.get("tagline") or "").strip()
            url = (node.get("url") or "").strip()
            line = name
            if tagline:
                line += f" — {tagline}"
            if url:
                line += f" ({url})"
            lines.append(line)
        return "\n".join(lines)

    # -- user-facing operations -------------------------------------------

    def todays_hunts(self, limit: int = 10) -> str:
        """READ: today's top-ranked Product Hunt posts."""
        limit = _clamp(limit)
        query = (
            "query ($first: Int) { posts(first: $first, order: RANKING) "
            "{ edges { node { name tagline url } } } }"
        )
        data = self._graphql(query, {"first": limit})
        posts = ((data.get("posts") or {}).get("edges") or [])[:limit]
        formatted = self._format_posts(posts)
        return formatted if formatted else "No hunts found."

    def search_posts(self, query: str, limit: int = 10) -> str:
        """READ: search Product Hunt posts by keyword."""
        limit = _clamp(limit)
        gql = (
            "query ($q: String, $first: Int) { posts(first: $first, query: $q) "
            "{ edges { node { name tagline url } } } }"
        )
        data = self._graphql(gql, {"q": query, "first": limit})
        posts = ((data.get("posts") or {}).get("edges") or [])[:limit]
        formatted = self._format_posts(posts)
        return formatted if formatted else f"No posts found for {query!r}."


def _register() -> ProductHuntConnector:
    from zeline.connectors import register

    return register(ProductHuntConnector())


_register()
