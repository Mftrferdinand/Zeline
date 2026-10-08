"""Wise connector (API token as a Bearer token)."""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.wise.com"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class WiseConnector(BaseConnector):
    id = "wise"
    name = "Wise"
    description = "Read Wise profiles and live exchange rates."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("api_token") or kwargs.get("token") or "").strip()
        if not api_token:
            return "ERROR: no API token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/v1/profiles",
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.wise.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Wise rejected the API token (HTTP {resp.status_code})."
        store.save(self.id, {"api_token": api_token})
        return "Connected to Wise."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Wise disconnected."
        return "Wise was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API token stored"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_token', '')}"}

    def _require_connected(self) -> None:
        data = store.load(self.id) or {}
        if not data.get("api_token"):
            raise RuntimeError("ERROR: Wise is not connected. Run: zeline connect wise")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Wise API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Wise API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Wise returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_profiles(self) -> str:
        profiles = self._api("GET", "/v1/profiles")
        if not isinstance(profiles, list):
            raise RuntimeError("ERROR: Wise returned an unexpected profiles response.")
        lines = []
        for profile in profiles:
            details = profile.get("details") or {}
            name = (
                details.get("firstName") or details.get("name") or details.get("companyName") or "?"
            )
            lines.append(f"{profile.get('id', '?')}: {profile.get('type', '?')} ({name})")
        return "\n".join(lines) if lines else "No profiles found."

    def get_rate(self, source: str = "USD", target: str = "EUR") -> str:
        source = (source or "USD").strip().upper()
        target = (target or "EUR").strip().upper()
        rates = self._api("GET", "/v1/rates", params={"source": source, "target": target})
        if not isinstance(rates, list) or not rates:
            raise RuntimeError(f"ERROR: no rate found for {source} to {target}.")
        rate = (rates[0] or {}).get("rate", "?")
        return f"{source}\u2192{target}: {rate}"


def _register() -> WiseConnector:
    from zeline.connectors import register

    return register(WiseConnector())


_register()
