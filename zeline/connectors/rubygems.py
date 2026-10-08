"""RubyGems connector (public API, no key needed)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://rubygems.org"
_TIMEOUT = 30


class RubyGemsConnector(BaseConnector):
    id = "rubygems"
    name = "RubyGems"
    description = "Look up Ruby gem info and search (public, no key)."
    auth_kind = "none"

    def connect(self, **kwargs) -> str:
        try:
            resp = requests.get(f"{API_BASE}/", timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach RubyGems API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: could not reach RubyGems API (HTTP {resp.status_code})."
        store.save(self.id, {"connected": True})
        return "Connected to RubyGems (public API, no key needed)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "RubyGems disconnected."
        return "RubyGems was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("connected"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "public API, no key needed"}

    # -- API helpers -----------------------------------------------------

    def _get(self, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.get(f"{API_BASE}{path}", **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: RubyGems API request failed ({exc}).") from exc
        if resp.status_code == 404:
            raise RuntimeError("ERROR: gem not found.")
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: RubyGems API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def package_info(self, name: str) -> str:
        gem = self._get(f"/api/v1/gems/{name}.json")
        version = gem.get("version") or "-"
        info = (gem.get("info") or "-").strip().replace("\n", " ")
        return f"{name} {version} — {info}"

    def search(self, query: str, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        gems = self._get("/api/v1/search.json", params={"query": query})
        if not isinstance(gems, list):
            gems = [gems]
        lines = [f"{gem.get('name', '?')} {gem.get('version', '?')}" for gem in gems[:limit]]
        return "\n".join(lines) if lines else f"No gems found for {query!r}."


def _register() -> RubyGemsConnector:
    from zeline.connectors import register

    return register(RubyGemsConnector())


_register()
