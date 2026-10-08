"""Meilisearch connector (master key + base URL)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class MeilisearchConnector(BaseConnector):
    id = "meilisearch"
    name = "Meilisearch"
    description = "Search Meilisearch indexes."
    auth_kind = "pat"

    def connect(self, master_key: str = "", base_url: str = "", **kwargs) -> str:
        master_key = (master_key or kwargs.get("token") or "").strip()
        base_url = (base_url or kwargs.get("base_url") or "").strip().rstrip("/")
        if not master_key:
            return "ERROR: no master key provided."
        if not base_url:
            return "ERROR: no base URL provided."
        try:
            resp = requests.get(
                f"{base_url}/indexes",
                params={"limit": 1},
                headers={"Authorization": f"Bearer {master_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {base_url} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Meilisearch rejected the master key (HTTP {resp.status_code})."
        try:
            total = resp.json().get("total", "?")
        except ValueError:
            total = "?"
        store.save(self.id, {"master_key": master_key, "base_url": base_url})
        return f"Connected to Meilisearch at {base_url} ({total} indexes)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Meilisearch disconnected."
        return "Meilisearch was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("master_key") or not data.get("base_url"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"linked to {data['base_url']}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Meilisearch is not connected. Run: zeline connect meilisearch")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('master_key', '')}"}

    def _base(self) -> str:
        data = store.load(self.id) or {}
        return (data.get("base_url") or "").rstrip("/")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{self._base()}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Meilisearch API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Meilisearch API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Meilisearch returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_indexes(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/indexes", params={"limit": limit})
        data = data if isinstance(data, dict) else {}
        items = data.get("results", [])
        lines = []
        for index in items[:limit]:
            uid = index.get("uid", "?")
            pk = index.get("primaryKey") or "-"
            lines.append(f"{uid} (primary key: {pk})")
        return "\n".join(lines) if lines else "No indexes found."

    def search_index(self, index_uid: str, query: str, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api(
            "POST", f"/indexes/{index_uid}/search", json={"q": query, "limit": limit}
        )
        data = data if isinstance(data, dict) else {}
        hits = data.get("hits", [])
        lines = []
        for hit in hits[:limit]:
            hit_id = hit.get("id", hit.get("uid", "?"))
            title = hit.get("title") or hit.get("name") or ""
            if title:
                lines.append(f"{hit_id}: {title}")
            else:
                lines.append(f"{hit_id}: {str(hit)[:80]}")
        return "\n".join(lines) if lines else f"No results for {query!r} in index {index_uid}."


def _register() -> MeilisearchConnector:
    from zeline.connectors import register

    return register(MeilisearchConnector())


_register()
