"""Sentry connector (auth token + organization slug)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://sentry.io/api/0"
_TIMEOUT = 30


class SentryConnector(BaseConnector):
    id = "sentry"
    name = "Sentry"
    description = "List issues for a Sentry organization."
    auth_kind = "pat"

    def connect(self, token: str = "", organization_slug: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        organization_slug = (organization_slug or kwargs.get("organization_slug") or "").strip()
        if not token:
            return "ERROR: no token provided."
        if not organization_slug:
            return "ERROR: no organization_slug provided."
        try:
            resp = requests.get(
                f"{API_BASE}/organizations/{organization_slug}/",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach sentry.io ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Sentry rejected the credentials (HTTP {resp.status_code})."
        try:
            org_name = resp.json().get("name", organization_slug)
        except ValueError:
            return "ERROR: Sentry returned an unreadable response."
        store.save(self.id, {
            "token": token,
            "organization_slug": organization_slug,
            "org_name": org_name,
        })
        return f"Connected to Sentry organization {organization_slug}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Sentry disconnected."
        return "Sentry was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token") or not data.get("organization_slug"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"org {data.get('organization_slug')}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> dict:
        data = store.load(self.id) or {}
        if not data.get("token") or not data.get("organization_slug"):
            raise RuntimeError("ERROR: Sentry is not connected. Run 'zeline connect sentry' first.")
        return data

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        data = self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update({"Authorization": f"Bearer {data['token']}"})
        try:
            resp = requests.request(
                method, f"{API_BASE}/organizations/{data['organization_slug']}{path}",
                headers=headers, **kwargs
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Sentry API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Sentry API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_issues(self, limit: int = 10, project_slug: str = "") -> str:
        params: dict = {"limit": max(1, min(limit, 100))}
        if project_slug:
            params["project"] = project_slug
        issues = self._api("GET", "/issues/", params=params)
        lines = []
        for issue in issues[:limit]:
            lines.append(
                f"{issue.get('shortId', '?')}: {issue.get('title', '?')} [{issue.get('level', '?')}]"
            )
        return "\n".join(lines) if lines else "No issues found."


def _register() -> SentryConnector:
    from zeline.connectors import register

    return register(SentryConnector())


_register()
