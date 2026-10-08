"""DigitalOcean connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.digitalocean.com/v2"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class DigitalOceanConnector(BaseConnector):
    id = "digitalocean"
    name = "DigitalOcean"
    description = "Read DigitalOcean droplets."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("api_key") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/account",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.digitalocean.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: DigitalOcean rejected the token (HTTP {resp.status_code})."
        try:
            account = resp.json().get("account", {})
            email = account.get("email", "?") if isinstance(account, dict) else "?"
        except ValueError:
            email = "?"
        store.save(self.id, {"token": token})
        return f"Connected to DigitalOcean (account {email})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "DigitalOcean disconnected."
        return "DigitalOcean was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "token stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: DigitalOcean is not connected. Run: zeline connect digitalocean")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {
            "Authorization": f"Bearer {data.get('token', '')}",
            "Content-Type": "application/json",
        }

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: DigitalOcean API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: DigitalOcean API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: DigitalOcean returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def _public_ip(self, droplet: dict) -> str:
        networks = droplet.get("networks") or {}
        for addr in networks.get("v4") or []:
            if isinstance(addr, dict) and addr.get("type") == "public":
                return addr.get("ip_address", "?")
        return "?"

    def _region(self, droplet: dict) -> str:
        region = droplet.get("region") or {}
        if isinstance(region, dict):
            return region.get("slug") or region.get("name") or "?"
        return str(region) if region else "?"

    def list_droplets(self, limit: int = 10) -> str:
        """[READ] List DigitalOcean droplets with their status and public IP."""
        limit = _clamp(limit)
        data = self._api("GET", "/droplets", params={"per_page": limit})
        droplets = data.get("droplets", []) if isinstance(data, dict) else []
        lines = []
        for droplet in droplets[:limit]:
            if not isinstance(droplet, dict):
                continue
            lines.append(
                f"{droplet.get('id', '?')}: {droplet.get('name', '(no name)')} "
                f"[{droplet.get('status', '?')}] {self._region(droplet)} "
                f"({self._public_ip(droplet)})"
            )
        return "\n".join(lines) if lines else "No droplets found."


def _register() -> DigitalOceanConnector:
    from zeline.connectors import register

    return register(DigitalOceanConnector())


_register()
