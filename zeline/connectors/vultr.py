"""Vultr connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.vultr.com/v2"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class VultrConnector(BaseConnector):
    id = "vultr"
    name = "Vultr"
    description = "Read Vultr instances."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/account",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.vultr.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Vultr rejected the API key (HTTP {resp.status_code})."
        try:
            account_name = resp.json().get("account", {}).get("name", "?")
        except ValueError:
            account_name = "?"
        store.save(self.id, {"api_key": api_key})
        return f"Connected to Vultr (account {account_name})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Vultr disconnected."
        return "Vultr was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Vultr is not connected. Run: zeline connect vultr")

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
            raise RuntimeError(f"ERROR: Vultr API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Vultr API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Vultr returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_instances(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/instances", params={"per_page": limit})
        instances = data.get("instances", []) if isinstance(data, dict) else []
        lines = []
        for instance in instances[:limit]:
            ip = instance.get("main_ip", "") or ""
            if not ip:
                v4 = instance.get("v4_main_ip") or {}
                if isinstance(v4, dict):
                    ip = v4.get("ip", "") or ""
            lines.append(
                f"{instance.get('id', '?')}: {instance.get('label', '(no label)')} "
                f"[{instance.get('status', '?')}] region={instance.get('region', '?')} ip={ip or '?'}"
            )
        return "\n".join(lines) if lines else "No instances found."


def _register() -> VultrConnector:
    from zeline.connectors import register

    return register(VultrConnector())


_register()
