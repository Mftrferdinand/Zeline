"""Hunter connector (API key as query param)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.hunter.io/v2"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class HunterConnector(BaseConnector):
    id = "hunter"
    name = "Hunter"
    description = "Find and verify professional email addresses."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/account",
                params={"api_key": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Hunter API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Hunter rejected the API key (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        email = ""
        if isinstance(payload, dict):
            data = payload.get("data") or {}
            email = str(data.get("email") or "").strip()
        store.save(self.id, {"api_key": api_key, "login": email})
        if email:
            return f"Connected to Hunter as {email}."
        return "Connected to Hunter."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Hunter disconnected."
        return "Hunter was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        login = data.get("login") or "API key"
        return {"connected": True, "detail": f"linked as {login}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Hunter is not connected. Run: zeline connect hunter")

    def _stored(self) -> dict:
        return store.load(self.id) or {}

    def _api(self, path: str, params: dict | None = None) -> dict:
        self._require_connected()
        query = dict(params or {})
        query["api_key"] = self._stored().get("api_key", "")
        try:
            resp = requests.get(f"{API_BASE}{path}", params=query, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Hunter API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Hunter API {resp.status_code} on {path}.")
        try:
            payload = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Hunter returned an unreadable response.") from None
        if not isinstance(payload, dict):
            raise RuntimeError("ERROR: Hunter returned an unexpected response.")
        return payload

    # -- user-facing operations -------------------------------------------

    def domain_search(self, domain: str, limit: int = 10) -> str:
        """[READ] Find email addresses at a domain."""
        limit = _clamp(limit)
        payload = self._api("/domain-search", {"domain": domain, "limit": limit})
        data = payload.get("data") or {}
        emails = data.get("emails") or []
        lines = []
        for entry in emails[:limit]:
            if not isinstance(entry, dict):
                continue
            email = entry.get("value") or "(unknown)"
            lines.append(
                f"{email} (type: {entry.get('type', '?')}, "
                f"confidence: {entry.get('confidence', '?')})"
            )
        return "\n".join(lines) if lines else f"No email addresses found for {domain}."

    def verify_email(self, email: str) -> str:
        """[READ] Verify a single email address."""
        payload = self._api("/email-verifier", {"email": email})
        data = payload.get("data") or {}
        verified = data.get("email") or email
        return (
            f"{verified} — result: {data.get('result', '?')}, "
            f"score: {data.get('score', '?')}"
        )


def _register() -> HunterConnector:
    from zeline.connectors import register

    return register(HunterConnector())


_register()
