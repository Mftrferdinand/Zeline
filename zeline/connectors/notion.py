"""Notion connector (integration token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
_TIMEOUT = 30


class NotionConnector(BaseConnector):
    id = "notion"
    name = "Notion"
    description = "Search pages/databases, read database rows, create pages."
    auth_kind = "pat"

    # -- lifecycle ---------------------------------------------------------

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/users/me",
                headers=self._base_headers(token),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.notion.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Notion rejected the token (HTTP {resp.status_code})."
        try:
            workspace = resp.json().get("name", "?")
        except ValueError:
            return "ERROR: Notion returned an unreadable response."
        store.save(self.id, {"token": token, "workspace": workspace})
        return f"Connected to Notion integration {workspace!r}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Notion disconnected."
        return "Notion was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"{data.get('workspace', '?')!r}"}

    # -- API helpers -------------------------------------------------------

    def _base_headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
        }

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return self._base_headers(data.get("token", ""))

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        merged = dict(headers)
        merged.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=merged, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Notion API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Notion API {resp.status_code} on {path}.")
        return resp.json()

    @staticmethod
    def _rich_text(segments) -> str:
        if not segments:
            return ""
        return "".join(seg.get("plain_text", "") for seg in segments)

    def _title(self, result: dict) -> str:
        """Best-effort title for a search/query row (page or database)."""
        if result.get("object") == "database":
            return self._rich_text(result.get("title")) or "Untitled"
        for prop in (result.get("properties") or {}).values():
            if prop.get("type") == "title":
                return self._rich_text(prop.get("title")) or "Untitled"
        return "Untitled"

    @staticmethod
    def _clamp(limit: int) -> int:
        return max(1, min(int(limit), 100))

    # -- user-facing operations --------------------------------------------

    def search(self, query: str, limit: int = 10) -> str:
        """Search pages and databases. Read-only."""
        data = self._api("POST", "/search", json={"query": query, "page_size": self._clamp(limit)})
        lines = []
        for item in data.get("results", [])[:limit]:
            kind = item.get("object", "?")
            lines.append(f"{kind}: {self._title(item)} ({item.get('id', '?')})")
        return "\n".join(lines) if lines else f"No results for {query!r}."

    def create_page(self, parent_page_id: str, title: str, content: str = "") -> str:
        """Create a page under *parent_page_id*. Network write."""
        payload = {
            "parent": {"page_id": parent_page_id},
            "properties": {"title": [{"text": {"content": title}}]},
        }
        if content:
            payload["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": content}}]
                    },
                }
            ]
        page = self._api("POST", "/pages", json=payload)
        return f"Page created: {page.get('id', '?')}"

    def query_database(self, database_id: str, limit: int = 10) -> str:
        """Read rows from a database. Read-only."""
        data = self._api(
            "POST",
            f"/databases/{database_id}/query",
            json={"page_size": self._clamp(limit)},
        )
        lines = []
        for row in data.get("results", [])[:limit]:
            lines.append(f"{self._title(row)} ({row.get('id', '?')})")
        return "\n".join(lines) if lines else f"Database {database_id} has no rows."


def _register() -> NotionConnector:
    from zeline.connectors import register

    return register(NotionConnector())


_register()
