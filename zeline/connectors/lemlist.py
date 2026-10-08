"""lemlist connector (API key, Basic auth)."""
from __future__ import annotations

import base64

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.lemlist.com/api"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class LemlistConnector(BaseConnector):
    id = "lemlist"
    name = "Lemlist"
    description = "List campaigns and view campaign stats."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        headers = self._auth_headers(api_key)
        try:
            resp = requests.get(f"{API_BASE}/campaigns", headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach the lemlist API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: lemlist rejected the API key (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            items = payload.get("campaigns") or payload.get("data") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        store.save(self.id, {"api_key": api_key})
        return f"Connected to lemlist ({len(items)} campaigns)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Lemlist disconnected."
        return "Lemlist was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: lemlist is not connected. Run: zeline connect lemlist")

    def _stored(self) -> dict:
        return store.load(self.id) or {}

    @staticmethod
    def _auth_headers(api_key: str) -> dict:
        token = base64.b64encode(f":{api_key}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._auth_headers(self._stored().get("api_key", "")))
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: lemlist API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: lemlist API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: lemlist returned an unreadable response.") from None

    @staticmethod
    def _campaign_items(payload: dict | list) -> list:
        if isinstance(payload, dict):
            items = payload.get("campaigns") or payload.get("data") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        return items if isinstance(items, list) else []

    # -- user-facing operations -------------------------------------------

    def list_campaigns(self, limit: int = 10) -> str:
        """[READ] List lemlist campaigns."""
        limit = _clamp(limit)
        data = self._api("GET", "/campaigns", params={"limit": limit})
        items = self._campaign_items(data)
        lines = []
        for campaign in items[:limit]:
            if isinstance(campaign, dict):
                line = (
                    f"{campaign.get('_id', '?')}: {campaign.get('name', '(no name)')}"
                )
                if campaign.get("status"):
                    line += f" ({campaign['status']})"
                lines.append(line)
        return "\n".join(lines) if lines else "No campaigns found."

    def campaign_stats(self, campaign_id: str) -> str:
        """[READ] Show one campaign's stats."""
        data = self._api("GET", f"/campaigns/{campaign_id}")
        if not isinstance(data, dict):
            return str(data)
        name = data.get("name", "(no name)")
        parts = []
        for key in (
            "sent",
            "opened",
            "clicked",
            "replied",
            "bounced",
            "unsubscribed",
            "leads",
        ):
            value = data.get(key)
            if isinstance(value, (int, float)):
                parts.append(f"{key}: {value}")
        stats = ", ".join(parts) if parts else "no stats available"
        return f"{name} ({campaign_id}): {stats}"


def _register() -> LemlistConnector:
    from zeline.connectors import register

    return register(LemlistConnector())


_register()
