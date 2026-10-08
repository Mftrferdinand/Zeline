"""Bitbucket connector (username + app password, HTTP Basic auth)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.bitbucket.org/2.0"
_TIMEOUT = 30


class BitbucketConnector(BaseConnector):
    id = "bitbucket"
    name = "Bitbucket"
    description = "List repositories and pull requests on Bitbucket."
    auth_kind = "pat"

    def connect(self, username: str = "", app_password: str = "", **kwargs) -> str:
        username = (username or kwargs.get("username") or "").strip()
        app_password = (app_password or kwargs.get("app_password") or "").strip()
        if not username:
            return "ERROR: no username provided."
        if not app_password:
            return "ERROR: no app password provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user",
                auth=(username, app_password),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.bitbucket.org ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Bitbucket rejected the credentials (HTTP {resp.status_code})."
        try:
            display_name = resp.json().get("display_name", "?")
        except ValueError:
            return "ERROR: Bitbucket returned an unreadable response."
        store.save(self.id, {
            "username": username,
            "app_password": app_password,
            "display_name": display_name,
        })
        return f"Connected to Bitbucket as {display_name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Bitbucket disconnected."
        return "Bitbucket was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("app_password"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"{data.get('display_name', '?')} ({data.get('username', '?')})"}

    # -- API helpers -----------------------------------------------------

    def _auth(self) -> tuple[str, str]:
        data = store.load(self.id) or {}
        username = data.get("username", "")
        app_password = data.get("app_password", "")
        if not username or not app_password:
            raise RuntimeError("ERROR: Bitbucket is not connected. Run 'zeline connect bitbucket' first.")
        return username, app_password

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.request(
                method, f"{API_BASE}{path}", auth=self._auth(), **kwargs
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Bitbucket API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Bitbucket API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_repos(self, limit: int = 10) -> str:
        data = self._api(
            "GET", "/repositories",
            params={"role": "member", "pagelen": max(1, min(limit, 100))},
        )
        lines = []
        for repo in data.get("values", [])[:limit]:
            updated = (repo.get("updated_on") or "?")[:10]
            lines.append(f"{repo.get('full_name', '?')} ({updated})")
        return "\n".join(lines) if lines else "No repositories found."

    def list_prs(self, workspace: str, repo_slug: str, limit: int = 10) -> str:
        workspace = (workspace or "").strip()
        repo_slug = (repo_slug or "").strip()
        if not workspace:
            return "ERROR: no workspace provided."
        if not repo_slug:
            return "ERROR: no repo_slug provided."
        data = self._api(
            "GET", f"/repositories/{workspace}/{repo_slug}/pullrequests",
            params={"pagelen": max(1, min(limit, 100))},
        )
        lines = []
        for pr in data.get("values", [])[:limit]:
            lines.append(f"#{pr.get('id')} {pr.get('title', '?')} [{pr.get('state', '?')}]")
        return "\n".join(lines) if lines else f"No pull requests in {workspace}/{repo_slug}."


def _register() -> BitbucketConnector:
    from zeline.connectors import register

    return register(BitbucketConnector())


_register()
