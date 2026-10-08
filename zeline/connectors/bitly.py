"""Bitly connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api-ssl.bitly.com/v4"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class BitlyConnector(BaseConnector):
    id = "bitly"
    name = "Bitly"
    description = "Shorten URLs and list Bitly links."
    auth_kind = "pat"

    def connect(self, access_token: str = "", **kwargs) -> str:
        access_token = (access_token or kwargs.get("token") or "").strip()
        if not access_token:
            return "ERROR: no access token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Bitly ({exc})."
        if resp.status_code in (401, 403):
            return f"ERROR: Bitly rejected the access token (HTTP {resp.status_code})."
        if resp.status_code != 200:
            return f"ERROR: could not reach Bitly (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        group_guid = payload.get("default_group_guid") if isinstance(payload, dict) else None
        store.save(self.id, {"access_token": access_token, "default_group_guid": group_guid})
        return "Connected to Bitly."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Bitly disconnected."
        return "Bitly was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "personal access token"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Bitly is not connected. Run: zeline connect bitly")

    def _stored(self) -> dict:
        return store.load(self.id) or {}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._stored().get('access_token', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Bitly API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Bitly API {resp.status_code} on {path}.")
        try:
            payload = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Bitly returned an unreadable response.") from None
        return payload if isinstance(payload, dict) else {}

    # -- user-facing operations -------------------------------------------

    def shorten(self, long_url: str) -> str:
        """[NETWORK — mutates] Shorten a URL with Bitly."""
        if not (long_url or "").strip():
            raise RuntimeError("ERROR: no URL provided.")
        result = self._api("POST", "/shorten", json={"long_url": long_url.strip()})
        short_link = result.get("link")
        if not short_link:
            raise RuntimeError("ERROR: Bitly returned no shortened link.")
        return short_link

    def list_links(self, limit: int = 10) -> str:
        """[READ] List Bitly links for the default group."""
        limit = _clamp(limit)
        group_guid = self._stored().get("default_group_guid")
        if not group_guid:
            raise RuntimeError("ERROR: Bitly default group not known. Run: zeline connect bitly")
        data = self._api("GET", f"/groups/{group_guid}/bitlinks", params={"size": limit})
        links = data.get("links") or []
        lines = []
        for item in links[:limit]:
            if isinstance(item, dict):
                lines.append(
                    f"{item.get('link', '?')} → {item.get('long_url', '(no long URL)')}"
                )
        return "\n".join(lines) if lines else "No Bitly links found."


def _register() -> BitlyConnector:
    from zeline.connectors import register

    return register(BitlyConnector())


_register()
