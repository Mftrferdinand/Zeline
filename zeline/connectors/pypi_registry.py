"""PyPI registry connector (public API, no key needed)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://pypi.org"
_TIMEOUT = 30


class PypiRegistryConnector(BaseConnector):
    id = "pypi_registry"
    name = "PyPI"
    description = "Look up Python package info on PyPI (public, no key)."
    auth_kind = "none"

    def connect(self, **kwargs) -> str:
        try:
            resp = requests.get(f"{API_BASE}/", timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach PyPI ({exc})."
        if resp.status_code != 200:
            return f"ERROR: could not reach PyPI (HTTP {resp.status_code})."
        store.save(self.id, {"connected": True})
        return "Connected to PyPI (public API, no key needed)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "PyPI disconnected."
        return "PyPI was not connected."

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
            raise RuntimeError(f"ERROR: PyPI API request failed ({exc}).") from exc
        if resp.status_code == 404:
            raise RuntimeError("ERROR: package not found.")
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: PyPI API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def package_info(self, name: str) -> str:
        data = self._get(f"/pypi/{name}/json")
        info = data.get("info", {}) if isinstance(data, dict) else {}
        version = info.get("version") or "-"
        summary = (info.get("summary") or "-").strip().replace("\n", " ") or "-"
        return f"{name} {version} — {summary}"


def _register() -> PypiRegistryConnector:
    from zeline.connectors import register

    return register(PypiRegistryConnector())


_register()
