"""Confluence connector (email + API token, HTTP Basic auth).

Talks to the Confluence Cloud REST API at ``{base_url}/wiki/rest/api``.
"""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30
_API_PATH = "/wiki/rest/api"


def _normalize_base_url(base_url: str) -> str:
    """Strip whitespace/trailing slash; must be an https:// URL."""
    return (base_url or "").strip().rstrip("/")


class ConfluenceConnector(BaseConnector):
    id = "confluence"
    name = "Confluence"
    description = "Search Confluence pages and read page content."
    auth_kind = "pat"

    def connect(self, email: str = "", token: str = "", base_url: str = "", **kwargs) -> str:
        email = (email or kwargs.get("email") or "").strip()
        token = (token or kwargs.get("token") or "").strip()
        base_url = _normalize_base_url(base_url or kwargs.get("base_url") or "")
        if not email:
            return "ERROR: no email provided."
        if not token:
            return "ERROR: no API token provided."
        if not base_url:
            return "ERROR: no base URL provided."
        if not base_url.startswith("https://"):
            return "ERROR: base URL must be an https:// URL."
        try:
            resp = requests.get(
                f"{base_url}{_API_PATH}/user/current",
                auth=(email, token),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {base_url} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Confluence rejected the credentials (HTTP {resp.status_code})."
        try:
            display_name = resp.json().get("displayName", "?")
        except ValueError:
            return "ERROR: Confluence returned an unreadable response."
        store.save(self.id, {
            "email": email,
            "token": token,
            "base_url": base_url,
            "user": display_name,
        })
        return f"Connected to Confluence as {display_name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Confluence disconnected."
        return "Confluence was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token") or not data.get("base_url"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("user", "linked")}

    # -- API helpers -----------------------------------------------------

    def _auth(self) -> tuple[tuple[str, str], str]:
        data = store.load(self.id) or {}
        email = data.get("email", "")
        token = data.get("token", "")
        base_url = data.get("base_url", "")
        if not email or not token or not base_url:
            raise RuntimeError("ERROR: Confluence is not connected. Run 'zeline connect confluence' first.")
        return (email, token), base_url

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        (email, token), base_url = self._auth()
        try:
            resp = requests.request(
                method, f"{base_url}{_API_PATH}{path}", auth=(email, token), **kwargs
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Confluence API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Confluence API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def search_pages(self, cql: str, limit: int = 10) -> str:
        """Search pages by CQL; one "id: title" line per result."""
        cql = (cql or "").strip()
        if not cql:
            return "ERROR: no CQL query provided."
        data = self._api(
            "GET", "/content/search",
            params={"cql": cql, "limit": max(1, min(limit, 100))},
        )
        results = data.get("results", []) if isinstance(data, dict) else []
        lines = []
        for page in results[:limit]:
            lines.append(f"{page.get('id', '?')}: {page.get('title', '')}")
        return "\n".join(lines) if lines else "No pages found."

    def get_page(self, page_id: str) -> str:
        """Return the first 500 characters of a page's storage body."""
        page_id = (page_id or "").strip()
        if not page_id:
            return "ERROR: no page ID provided."
        page = self._api("GET", f"/content/{page_id}", params={"expand": "body.storage"})
        body = (
            page.get("body", {}).get("storage", {}).get("value", "")
            if isinstance(page, dict)
            else ""
        )
        body = (body or "").strip()
        if not body:
            return "ERROR: no content."
        return body[:500]


def _register() -> ConfluenceConnector:
    from zeline.connectors import register

    return register(ConfluenceConnector())


_register()
