"""Apollo connector (API key via X-Api-Key header)."""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.apollo.io/v1"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class ApolloConnector(BaseConnector):
    id = "apollo"
    name = "Apollo"
    description = "Search B2B people data via Apollo.io."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/auth/health",
                headers={"X-Api-Key": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Apollo API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Apollo rejected the API key (HTTP {resp.status_code})."
        store.save(self.id, {"api_key": api_key})
        return "Connected to Apollo."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Apollo disconnected."
        return "Apollo was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key linked"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Apollo is not connected. Run: zeline connect apollo")

    def _stored(self) -> dict:
        return store.load(self.id) or {}

    def _headers(self) -> dict:
        return {"X-Api-Key": self._stored().get("api_key", "")}

    def _api(self, path: str, payload: dict) -> dict:
        self._require_connected()
        try:
            resp = requests.post(
                f"{API_BASE}{path}",
                headers=self._headers(),
                json=payload,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Apollo API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Apollo API {resp.status_code} on {path}.")
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Apollo returned an unreadable response.") from None
        if not isinstance(data, dict):
            raise RuntimeError("ERROR: Apollo returned an unexpected response.")
        return data

    # -- user-facing operations -------------------------------------------

    def people_search(self, query: str, limit: int = 10) -> str:
        """[READ] Search people by keywords (name, title, company, ...)."""
        limit = _clamp(limit)
        data = self._api(
            "/mixed_people/search",
            {"q_keywords": query, "page": 1, "per_page": limit},
        )
        people = data.get("people") or []
        lines = []
        for person in people[:limit]:
            if not isinstance(person, dict):
                continue
            name = person.get("name", "(no name)")
            title = person.get("title", "")
            org = person.get("organization") or {}
            org_name = org.get("name", "") if isinstance(org, dict) else ""
            line = str(name)
            if title:
                line += f" — {title}"
            if org_name:
                line += f" ({org_name})"
            lines.append(line)
        return "\n".join(lines) if lines else "No people found."

    def enrich_person(self, email: str) -> str:
        """[READ] Enrich a person by email address."""
        data = self._api("/people/match", {"email": email})
        person = data.get("person") or {}
        if not isinstance(person, dict):
            return "No match found."
        name = person.get("name", "(no name)")
        title = person.get("title", "")
        org = person.get("organization") or {}
        org_name = org.get("name", "") if isinstance(org, dict) else ""
        line = str(name)
        if title:
            line += f" — {title}"
        if org_name:
            line += f" ({org_name})"
        return line


def _register() -> ApolloConnector:
    from zeline.connectors import register

    return register(ApolloConnector())


_register()
