"""GitLab connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

DEFAULT_BASE_URL = "https://gitlab.com"
API_PATH = "/api/v4"
_TIMEOUT = 30


def _normalize_base_url(base_url: str) -> str:
    """Normalize to a base URL ending in ``/api/v4``.

    Accepts either a bare host (``https://gitlab.example.com``) or a full
    API root (``https://gitlab.example.com/api/v4``).
    """
    url = (base_url or "").strip().rstrip("/")
    if url.endswith(API_PATH):
        return url
    return url + API_PATH


class GitLabConnector(BaseConnector):
    id = "gitlab"
    name = "GitLab"
    description = "Read projects, merge requests and issues."
    auth_kind = "pat"

    def connect(self, token: str = "", base_url: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        base_url = (base_url or kwargs.get("base_url") or DEFAULT_BASE_URL).strip()
        api_root = _normalize_base_url(base_url)
        try:
            resp = requests.get(
                f"{api_root}/user",
                headers={"PRIVATE-TOKEN": token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {api_root} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: GitLab rejected the token (HTTP {resp.status_code})."
        try:
            username = resp.json().get("username", "?")
        except ValueError:
            return "ERROR: GitLab returned an unreadable response."
        store.save(self.id, {"token": token, "base_url": api_root, "username": username})
        return f"Connected to GitLab as @{username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "GitLab disconnected."
        return "GitLab was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('username', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _api_root(self) -> str:
        data = store.load(self.id) or {}
        return data.get("base_url") or _normalize_base_url(DEFAULT_BASE_URL)

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"PRIVATE-TOKEN": data.get("token", "")}

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: GitLab is not connected. Run: zeline connect gitlab")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{self._api_root()}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: GitLab API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: GitLab API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_projects(self, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        projects = self._api(
            "GET", "/projects",
            params={
                "membership": "true",
                "order_by": "last_activity_at",
                "per_page": limit,
            },
        )
        lines = []
        for project in projects[:limit]:
            ns = project.get("path_with_namespace", "?")
            desc = (project.get("description") or "").strip().replace("\n", " ")
            line = ns
            if desc:
                line += f" — {desc}"
            lines.append(line)
        return "\n".join(lines) if lines else "No projects found."

    def list_merge_requests(self, state: str = "opened", limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        mrs = self._api(
            "GET", "/merge_requests",
            params={"scope": "all", "state": state, "per_page": limit},
        )
        lines = []
        for mr in mrs[:limit]:
            lines.append(f"!{mr['iid']} {mr['title']} ({mr.get('source_branch', '?')}→{mr.get('target_branch', '?')})")
        return "\n".join(lines) if lines else f"No {state} merge requests."

    def list_issues(self, state: str = "opened", limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        issues = self._api(
            "GET", "/issues",
            params={"scope": "all", "state": state, "per_page": limit},
        )
        lines = []
        for issue in issues[:limit]:
            labels = ",".join(issue.get("labels", []))
            suffix = f" [{labels}]" if labels else ""
            lines.append(f"#{issue['iid']} {issue['title']}{suffix}")
        return "\n".join(lines) if lines else f"No {state} issues."


def _register() -> GitLabConnector:
    from zeline.connectors import register

    return register(GitLabConnector())


_register()
