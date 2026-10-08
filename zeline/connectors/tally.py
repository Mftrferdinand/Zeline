"""Tally connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.tally.so"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class TallyConnector(BaseConnector):
    id = "tally"
    name = "Tally"
    description = "Read Tally forms."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/forms",
                params={"perPage": 1},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.tally.so ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Tally rejected the API key (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key})
        return "Connected to Tally."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Tally disconnected."
        return "Tally was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Tally is not connected. Run: zeline connect tally")

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
            raise RuntimeError(f"ERROR: Tally API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Tally API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Tally returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_forms(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/forms", params={"perPage": limit})
        forms = []
        if isinstance(data, dict):
            forms = data.get("forms") or data.get("items") or []
        lines = []
        for form in forms[:limit]:
            lines.append(f"{form.get('id', '?')}: {form.get('title', '(no title)')}")
        return "\n".join(lines) if lines else "No forms found."


def _register() -> TallyConnector:
    from zeline.connectors import register

    return register(TallyConnector())


_register()
