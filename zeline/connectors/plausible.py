"""Plausible connector (API key)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://plausible.io/api/v1"
_TIMEOUT = 30

_PERIODS = {"12mo", "6mo", "30d", "7d", "day"}


class PlausibleConnector(BaseConnector):
    id = "plausible"
    name = "Plausible"
    description = "Read Plausible site stats."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/sites",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach plausible.io ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Plausible rejected the API key (HTTP {resp.status_code})."
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            sites = data.get("sites") or []
            site_count = len(sites) if isinstance(sites, list) else 0
        else:
            site_count = 0
        store.save(self.id, {"api_key": api_key})
        return f"Connected to Plausible ({site_count} site(s) visible)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Plausible disconnected."
        return "Plausible was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Plausible is not connected. Run: zeline connect plausible")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_key', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Plausible API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Plausible API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Plausible returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_sites(self) -> str:
        data = self._api("GET", "/sites")
        if isinstance(data, dict):
            sites = data.get("sites") or []
        elif isinstance(data, list):
            sites = data
        else:
            sites = []
        lines = []
        for site in sites:
            if isinstance(site, dict):
                lines.append(site.get("domain", "?"))
            else:
                lines.append(str(site))
        return "\n".join(lines) if lines else "No sites found."

    def site_stats(self, site_id: str, period: str = "7d") -> str:
        period = period if period in _PERIODS else "7d"
        data = self._api(
            "GET",
            "/stats/aggregate",
            params={"site_id": site_id, "period": period, "metrics": "visitors,pageviews"},
        )
        results = data.get("results", {}) if isinstance(data, dict) else {}
        if not isinstance(results, dict) or not results:
            return f"No stats for {site_id} (period {period})."
        lines = []
        for metric, value in results.items():
            if isinstance(value, dict):
                value = value.get("value", "?")
            lines.append(f"{metric}: {value}")
        return f"{site_id} ({period}):\n" + "\n".join(lines)


def _register() -> PlausibleConnector:
    from zeline.connectors import register

    return register(PlausibleConnector())


_register()
