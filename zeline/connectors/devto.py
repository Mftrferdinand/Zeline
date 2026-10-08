"""dev.to connector (API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://dev.to/api"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class DevToConnector(BaseConnector):
    id = "devto"
    name = "dev.to"
    description = "List and publish articles on dev.to."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        if not api_key:
            return "ERROR: api_key is required."
        try:
            resp = requests.get(
                f"{API_BASE}/articles/me",
                headers={"api-key": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach dev.to ({exc})."
        if resp.status_code != 200:
            return f"ERROR: dev.to rejected the API key (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            return "ERROR: dev.to returned an unreadable response."
        username = payload.get("username", "?")
        store.save(self.id, {"api_key": api_key, "username": username})
        return f"Connected to dev.to as @{username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "dev.to disconnected."
        return "dev.to was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('username', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"api-key": data.get("api_key", "")}

    def _require_connected(self) -> None:
        data = store.load(self.id) or {}
        if not data.get("api_key"):
            raise RuntimeError("ERROR: dev.to is not connected. Run: zeline connect devto")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: dev.to API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: dev.to API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_articles(self, limit: int = 10, tag: str = "") -> str:
        params = {"per_page": _clamp(limit)}
        tag = (tag or "").strip()
        if tag:
            params["tag"] = tag
        articles = self._api("GET", "/articles", params=params)
        articles = articles[:limit] if isinstance(articles, list) else []
        lines = []
        for article in articles:
            lines.append(f"{article.get('title', '?')} — {article.get('url', '')}")
        return "\n".join(lines) if lines else "No articles found."

    def create_article(self, title: str, body_markdown: str, published: bool = False) -> str:
        article = self._api(
            "POST",
            "/articles",
            json={
                "article": {
                    "title": title,
                    "body_markdown": body_markdown,
                    "published": bool(published),
                }
            },
        )
        url = article.get("url", "") if isinstance(article, dict) else ""
        return f"Article created: {url}".rstrip()


def _register() -> DevToConnector:
    from zeline.connectors import register

    return register(DevToConnector())


_register()
