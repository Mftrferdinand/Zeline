"""Algolia connector (application ID + API key)."""
from __future__ import annotations

from urllib.parse import urlencode

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


def _base(app_id: str) -> str:
    return f"https://{app_id}-dsn.algolia.net/1"


class AlgoliaConnector(BaseConnector):
    id = "algolia"
    name = "Algolia"
    description = "Search Algolia indices."
    auth_kind = "pat"

    def connect(self, app_id: str = "", api_key: str = "", **kwargs) -> str:
        app_id = (app_id or kwargs.get("app_id") or "").strip()
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not app_id:
            return "ERROR: no Algolia application ID provided."
        if not api_key:
            return "ERROR: no Algolia API key provided."
        base = _base(app_id)
        try:
            resp = requests.get(
                f"{base}/indexes",
                headers={
                    "X-Algolia-Application-Id": app_id,
                    "X-Algolia-API-Key": api_key,
                    "Content-Type": "application/json",
                },
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {app_id}-dsn.algolia.net ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Algolia rejected the credentials (HTTP {resp.status_code})."
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                index_count = len(payload.get("items", []))
            elif isinstance(payload, list):
                index_count = len(payload)
            else:
                index_count = 0
        except ValueError:
            index_count = 0
        store.save(self.id, {"app_id": app_id, "api_key": api_key})
        return f"Connected to Algolia (app {app_id}, {index_count} indices)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Algolia disconnected."
        return "Algolia was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("app_id") or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"app_id {data['app_id']}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Algolia is not connected. Run: zeline connect algolia")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {
            "X-Algolia-Application-Id": data.get("app_id", ""),
            "X-Algolia-API-Key": data.get("api_key", ""),
            "Content-Type": "application/json",
        }

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        data = store.load(self.id) or {}
        base = _base(data.get("app_id", ""))
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{base}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Algolia API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Algolia API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Algolia returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_indexes(self) -> str:
        data = self._api("GET", "/indexes")
        if isinstance(data, dict):
            items = data.get("items", [])
        elif isinstance(data, list):
            items = data
        else:
            items = []
        lines = [
            str(item.get("name", "?")) if isinstance(item, dict) else str(item)
            for item in items
        ]
        return "\n".join(lines) if lines else "No indices found."

    def search_index(self, index: str, query: str, limit: int = 10) -> str:
        limit = _clamp(limit)
        body = {"params": urlencode({"query": query, "hitsPerPage": limit})}
        data = self._api("POST", f"/indexes/{index}/query", json=body)
        hits = data.get("hits", []) if isinstance(data, dict) else []
        lines = []
        for hit in hits[:limit]:
            object_id = hit.get("objectID", "?")
            extras = ", ".join(
                f"{key}={hit[key]}"
                for key in ("name", "title", "heading", "description")
                if hit.get(key)
            )
            lines.append(f"{object_id}: {extras}" if extras else str(object_id))
        return "\n".join(lines) if lines else f"No hits for '{query}' in index {index}."


def _register() -> AlgoliaConnector:
    from zeline.connectors import register

    return register(AlgoliaConnector())


_register()
