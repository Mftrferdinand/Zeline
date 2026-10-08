"""crates.io connector (public API, no key needed)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://crates.io/api/v1"
_TIMEOUT = 30
_USER_AGENT = "zeline-connector (https://github.com/Zerolinear)"

_HEADERS = {"User-Agent": _USER_AGENT}


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class CratesIoConnector(BaseConnector):
    id = "crates_io"
    name = "crates.io"
    description = "Look up Rust crates and search crates.io (public API, no key needed)."
    auth_kind = "none"

    def connect(self, **kwargs) -> str:
        try:
            resp = requests.get(
                f"{API_BASE}/summary", timeout=_TIMEOUT, headers=_HEADERS
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach crates.io API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: could not reach crates.io API (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: could not reach crates.io API (unreadable response)."
        if not isinstance(body, dict):
            return "ERROR: could not reach crates.io API (unexpected response)."
        store.save(self.id, {"connected": True})
        return "Connected to crates.io (public API, no key needed)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "crates.io disconnected."
        return "crates.io was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("connected"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "public API"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: crates.io is not connected. Run: zeline connect crates_io")

    def _fetch(self, url: str, params: dict | None = None):
        self._require_connected()
        try:
            resp = requests.get(url, params=params, timeout=_TIMEOUT, headers=_HEADERS)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: crates.io API request failed ({exc}).") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: crates.io API {resp.status_code}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: crates.io returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def crate_info(self, name: str) -> str:
        name = str(name).strip()
        if not name:
            raise RuntimeError("ERROR: crate name is required.")
        body = self._fetch(f"{API_BASE}/crates/{name}")
        if not body or not isinstance(body.get("crate"), dict):
            raise RuntimeError("ERROR: crate not found.")
        crate = body["crate"]
        crate_name = crate.get("id", name)
        description = (crate.get("description") or "(no description)").strip() or "(no description)"
        lines = [
            f"Name: {crate_name}",
            f"Description: {description}",
            f"Max version: {crate.get('max_version', '?')}",
            f"Downloads: {crate.get('downloads', '?')}",
        ]
        return "\n".join(lines)

    def search_crates(self, query: str, limit: int = 10) -> str:
        limit = _clamp(limit)
        query = str(query).strip()
        if not query:
            raise RuntimeError("ERROR: search query is required.")
        body = self._fetch(f"{API_BASE}/crates", params={"q": query, "per_page": limit})
        crates = (body or {}).get("crates") if isinstance(body, dict) else None
        if not isinstance(crates, list) or not crates:
            return f'No crates found for "{query}".'
        lines = []
        for crate in crates[:limit]:
            if not isinstance(crate, dict):
                continue
            name = crate.get("id") or crate.get("name") or "(unknown)"
            max_version = crate.get("max_version", "?")
            description = str(crate.get("description") or "").strip()
            line = f"{name} ({max_version})"
            if description:
                line += f" — {description[:120]}"
            lines.append(line)
        return "\n".join(lines) if lines else f'No crates found for "{query}".'


def _register() -> CratesIoConnector:
    from zeline.connectors import register

    return register(CratesIoConnector())


_register()
