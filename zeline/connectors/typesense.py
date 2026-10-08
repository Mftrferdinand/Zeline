"""Typesense connector (API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


def _summarize(doc: dict) -> str:
    parts = [f"{k}={v}" for k, v in list(doc.items())[:5]]
    return ", ".join(parts) if parts else "(empty document)"


class TypesenseConnector(BaseConnector):
    id = "typesense"
    name = "Typesense"
    description = "Search Typesense collections."
    auth_kind = "pat"

    def connect(self, api_key: str = "", base_url: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        base_url = (base_url or kwargs.get("base_url") or "").strip().rstrip("/")
        if not api_key:
            return "ERROR: no API key provided."
        if not base_url:
            return "ERROR: no base URL provided."
        try:
            resp = requests.get(
                f"{base_url}/collections",
                headers={"X-TYPESENSE-API-KEY": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {base_url} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Typesense rejected the API key (HTTP {resp.status_code})."
        try:
            payload = resp.json()
            count = len(payload) if isinstance(payload, list) else 0
        except ValueError:
            count = 0
        store.save(self.id, {"api_key": api_key, "base_url": base_url})
        return f"Connected to Typesense at {base_url} ({count} collections)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Typesense disconnected."
        return "Typesense was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        base = (data.get("base_url") or "").rstrip("/")
        detail = f"linked to {base}" if base else "API key stored"
        return {"connected": True, "detail": detail}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Typesense is not connected. Run: zeline connect typesense")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"X-TYPESENSE-API-KEY": data.get("api_key", "")}

    def _base(self) -> str:
        data = store.load(self.id) or {}
        return (data.get("base_url") or "").rstrip("/")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        base = self._base()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{base}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Typesense API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Typesense API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Typesense returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_collections(self) -> str:
        data = self._api("GET", "/collections")
        items = data if isinstance(data, list) else []
        lines = []
        for collection in items:
            if not isinstance(collection, dict):
                continue
            name = collection.get("name", "?")
            docs = collection.get("num_documents", "?")
            lines.append(f"{name} ({docs} documents)")
        return "\n".join(lines) if lines else "No collections found."

    def search_collection(self, collection: str, query: str, query_by: str = "*") -> str:
        data = self._api(
            "GET",
            f"/collections/{collection}/documents/search",
            params={"q": query, "query_by": query_by},
        )
        hits = data.get("hits", []) if isinstance(data, dict) else []
        lines = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            doc = hit.get("document") or {}
            if not isinstance(doc, dict):
                continue
            lines.append(_summarize(doc))
        return "\n".join(lines) if lines else f"No hits in collection {collection}."


def _register() -> TypesenseConnector:
    from zeline.connectors import register

    return register(TypesenseConnector())


_register()
