"""Cloudflare connector (API token).

Talks to the Cloudflare API v4. All responses are wrapped as
``{"success": bool, "result": ...}``; a false *success* is an error.
"""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.cloudflare.com/client/v4"
_TIMEOUT = 30


class CloudflareConnector(BaseConnector):
    id = "cloudflare"
    name = "Cloudflare"
    description = "List zones and DNS records in Cloudflare."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user/tokens/verify",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.cloudflare.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Cloudflare rejected the token (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: Cloudflare returned an unreadable response."
        if not body.get("success"):
            return "ERROR: Cloudflare token verification failed."
        store.save(self.id, {"token": token})
        return "Connected to Cloudflare."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Cloudflare disconnected."
        return "Cloudflare was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        token = data.get("token", "")
        if not token:
            raise RuntimeError("ERROR: Cloudflare is not connected. Run 'zeline connect cloudflare' first.")
        return {"Authorization": f"Bearer {token}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Cloudflare API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Cloudflare API {resp.status_code} on {path}.")
        try:
            body = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"ERROR: Cloudflare returned an unreadable response ({exc}).") from exc
        if not body.get("success"):
            errors = body.get("errors", [])
            detail = "; ".join(
                str(err.get("message", err)) for err in errors if isinstance(err, dict)
            ) or str(errors)
            raise RuntimeError(f"ERROR: Cloudflare API failed on {path}: {detail}.")
        return body.get("result")

    # -- user-facing operations -------------------------------------------

    def list_zones(self) -> str:
        """List zones; one "id: name [status]" line each."""
        zones = self._api("GET", "/zones")
        zones = zones if isinstance(zones, list) else []
        lines = [
            f"{zone.get('id', '?')}: {zone.get('name', '?')} [{zone.get('status', '?')}]"
            for zone in zones
        ]
        return "\n".join(lines) if lines else "No zones found."

    def list_dns_records(self, zone_id: str) -> str:
        """List DNS records in a zone; one "type name → content" line each."""
        zone_id = (zone_id or "").strip()
        if not zone_id:
            return "ERROR: no zone ID provided."
        records = self._api("GET", f"/zones/{zone_id}/dns_records")
        records = records if isinstance(records, list) else []
        lines = [
            f"{rec.get('type', '?')} {rec.get('name', '?')} → {rec.get('content', '?')}"
            for rec in records
        ]
        return "\n".join(lines) if lines else f"No DNS records found in zone {zone_id}."


def _register() -> CloudflareConnector:
    from zeline.connectors import register

    return register(CloudflareConnector())


_register()
