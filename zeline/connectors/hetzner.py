"""Hetzner Cloud connector (API token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.hetzner.cloud/v1"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class HetznerConnector(BaseConnector):
    id = "hetzner"
    name = "Hetzner Cloud"
    description = "Read Hetzner Cloud servers."
    auth_kind = "pat"

    def connect(self, api_token: str = "", **kwargs) -> str:
        api_token = (api_token or kwargs.get("token") or "").strip()
        if not api_token:
            return "ERROR: no API token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/servers",
                params={"per_page": 1},
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.hetzner.cloud ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Hetzner Cloud rejected the API token (HTTP {resp.status_code})."
        try:
            meta = resp.json().get("meta", {}) if isinstance(resp.json(), dict) else {}
        except ValueError:
            meta = {}
        total = meta.get("pagination", {}).get("total_entries", "?")
        store.save(self.id, {"api_token": api_token})
        return f"Connected to Hetzner Cloud ({total} servers)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Hetzner Cloud disconnected."
        return "Hetzner Cloud was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API token stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Hetzner Cloud is not connected. Run: zeline connect hetzner")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('api_token', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Hetzner Cloud API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Hetzner Cloud API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Hetzner Cloud returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_servers(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/servers", params={"per_page": limit})
        items = data.get("servers", []) if isinstance(data, dict) else []
        lines = []
        for server in items[:limit]:
            if not isinstance(server, dict):
                continue
            srv_type = server.get("server_type") or {}
            ipv4 = ((server.get("public_net") or {}).get("ipv4") or {}).get("ip", "?")
            lines.append(
                f"{server.get('id', '?')}: {server.get('name', '(no name)')} "
                f"[{server.get('status', '?')}] "
                f"{srv_type.get('name', '?') if isinstance(srv_type, dict) else '?'} "
                f"ipv4={ipv4}"
            )
        return "\n".join(lines) if lines else "No servers found."


def _register() -> HetznerConnector:
    from zeline.connectors import register

    return register(HetznerConnector())


_register()
