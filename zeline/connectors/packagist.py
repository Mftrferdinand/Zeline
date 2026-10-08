"""Packagist connector (public API, no key needed)."""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

SEARCH_BASE = "https://packagist.org"
METADATA_BASE = "https://repo.packagist.org"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class PackagistConnector(BaseConnector):
    id = "packagist"
    name = "Packagist"
    description = "Look up PHP packages and search Packagist (public API, no key needed)."
    auth_kind = "none"

    def connect(self, **kwargs) -> str:
        url = f"{SEARCH_BASE}/search.json?q=zeline"
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach Packagist API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: could not reach Packagist API (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            return "ERROR: could not reach Packagist API (unreadable response)."
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            return "ERROR: could not reach Packagist API (unexpected response)."
        store.save(self.id, {"connected": True})
        return "Connected to Packagist (public API, no key needed)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Packagist disconnected."
        return "Packagist was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("connected"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "public API"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Packagist is not connected. Run: zeline connect packagist")

    def _fetch(self, url: str):
        self._require_connected()
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Packagist API request failed ({exc}).") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Packagist API {resp.status_code}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Packagist returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def package_info(self, vendor: str, package: str) -> str:
        full_name = f"{vendor}/{package}"
        payload = self._fetch(f"{METADATA_BASE}/p2/{full_name}.json")
        if not payload:
            raise RuntimeError(f"ERROR: package '{full_name}' not found.")
        versions = (payload.get("packages") or {}).get(full_name)
        if not isinstance(versions, list) or not versions:
            raise RuntimeError(f"ERROR: package '{full_name}' not found.")
        latest = versions[0] or {}
        description = latest.get("description") or ""
        if not description:
            for version in versions:
                if version.get("description"):
                    description = version["description"]
                    break
        lines = [
            f"Name: {latest.get('name', full_name)}",
            f"Description: {description or '(no description)'}",
            f"Latest version: {latest.get('version', '(unknown)')}",
        ]
        released = latest.get("time")
        if released:
            lines.append(f"Released: {released}")
        return "\n".join(lines)

    def search_packages(self, query: str, limit: int = 10) -> str:
        limit = _clamp(limit)
        payload = self._fetch(f"{SEARCH_BASE}/search.json?q={query}&per_page={limit}")
        if not isinstance(payload, dict):
            raise RuntimeError("ERROR: Packagist returned an unexpected response.")
        results = payload.get("results")
        if not results:
            return f"No packages found for '{query}'."
        lines = []
        for result in results[:limit]:
            name = result.get("name", "(unknown)")
            description = result.get("description") or "(no description)"
            lines.append(f"{name} — {description}")
        return "\n".join(lines)


def _register() -> PackagistConnector:
    from zeline.connectors import register

    return register(PackagistConnector())


_register()
