"""npm registry connector (public API, no key needed)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://registry.npmjs.org"
_TIMEOUT = 30


class NpmRegistryConnector(BaseConnector):
    id = "npm_registry"
    name = "npm Registry"
    description = "Look up npm package info and search (public, no key)."
    auth_kind = "none"

    def connect(self, **kwargs) -> str:
        try:
            resp = requests.get(f"{API_BASE}/", timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach npm registry ({exc})."
        if resp.status_code != 200:
            return f"ERROR: could not reach npm registry (HTTP {resp.status_code})."
        store.save(self.id, {"connected": True})
        return "Connected to npm registry (public API, no key needed)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "npm Registry disconnected."
        return "npm Registry was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("connected"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "public API"}

    # -- API helpers -----------------------------------------------------

    def _get(self, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.get(f"{API_BASE}{path}", **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: npm registry API request failed ({exc}).") from exc
        if resp.status_code == 404:
            raise RuntimeError("ERROR: package not found.")
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: npm registry API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def package_info(self, name: str) -> str:
        data = self._get(f"/{name}/latest")
        version = data.get("version") or "-"
        desc = (data.get("description") or "-").strip().replace("\n", " ") or "-"
        return f"{name}@{version} — {desc}"

    def search(self, query: str, limit: int = 10) -> str:
        size = max(1, min(limit, 100))
        data = self._get("/-/v1/search", params={"text": query, "size": size})
        objects = data.get("objects", []) if isinstance(data, dict) else []
        lines = []
        for obj in objects:
            pkg = obj.get("package", {}) if isinstance(obj, dict) else {}
            pname = pkg.get("name", "?")
            pdesc = (pkg.get("description") or "-").strip().replace("\n", " ") or "-"
            lines.append(f"{pname} — {pdesc}")
        return "\n".join(lines) if lines else f"No packages found for {query!r}."


def _register() -> NpmRegistryConnector:
    from zeline.connectors import register

    return register(NpmRegistryConnector())


_register()
