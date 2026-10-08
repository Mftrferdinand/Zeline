"""BunnyCDN connector (API key via AccessKey header)."""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.bunny.net"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class BunnyCDNConnector(BaseConnector):
    id = "bunnycdn"
    name = "BunnyCDN"
    description = "List BunnyCDN pull zones and storage zones."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/pullzone",
                headers={"AccessKey": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {API_BASE} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: BunnyCDN rejected the API key (HTTP {resp.status_code})."
        try:
            zones = resp.json()
        except ValueError:
            zones = []
        count = len(zones) if isinstance(zones, list) else 0
        store.save(self.id, {"api_key": api_key})
        return f"Connected to BunnyCDN ({count} pull zones)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "BunnyCDN disconnected."
        return "BunnyCDN was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key linked"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: BunnyCDN is not connected. Run: zeline connect bunnycdn")

    def _stored(self) -> dict:
        return store.load(self.id) or {}

    def _headers(self) -> dict:
        return {"AccessKey": self._stored().get("api_key", "")}

    def _api(self, method: str, path: str, **kwargs) -> list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: BunnyCDN API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: BunnyCDN API {resp.status_code} on {path}.")
        try:
            payload = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: BunnyCDN returned an unreadable response.") from None
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _first_hostname(zone: dict) -> str:
        hostnames = zone.get("Hostnames") or []
        if not hostnames:
            return "?"
        first = hostnames[0]
        if isinstance(first, dict):
            return str(first.get("Value") or "?")
        return str(first)

    # -- user-facing operations -------------------------------------------

    def list_pull_zones(self, limit: int = 10) -> str:
        """[READ] List BunnyCDN pull zones."""
        limit = _clamp(limit)
        zones = self._api("GET", "/pullzone", params={"limit": limit})
        lines = []
        for zone in zones[:limit]:
            if isinstance(zone, dict):
                lines.append(
                    f"{zone.get('Id', '?')}: {zone.get('Name', '(no name)')} "
                    f"({self._first_hostname(zone)})"
                )
        return "\n".join(lines) if lines else "No pull zones found."

    def list_storage_zones(self, limit: int = 10) -> str:
        """[READ] List BunnyCDN storage zones."""
        limit = _clamp(limit)
        zones = self._api("GET", "/storagezone", params={"limit": limit})
        lines = []
        for zone in zones[:limit]:
            if isinstance(zone, dict):
                lines.append(
                    f"{zone.get('Id', '?')}: {zone.get('Name', '(no name)')} "
                    f"(region: {zone.get('Region', '?')})"
                )
        return "\n".join(lines) if lines else "No storage zones found."


def _register() -> BunnyCDNConnector:
    from zeline.connectors import register

    return register(BunnyCDNConnector())


_register()
