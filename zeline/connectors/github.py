"""GitHub connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.github.com"
_TIMEOUT = 30


class GitHubConnector(BaseConnector):
    id = "github"
    name = "GitHub"
    description = "Read repos, issues and PRs; create issues and comments."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.github.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: GitHub rejected the token (HTTP {resp.status_code})."
        login = resp.json().get("login", "?")
        store.save(self.id, {"token": token, "login": login})
        return f"Connected to GitHub as @{login}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "GitHub disconnected."
        return "GitHub was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('login', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('token', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: GitHub API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: GitHub API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_repos(self, limit: int = 10) -> str:
        repos = self._api("GET", "/user/repos", params={"per_page": max(1, min(limit, 100)), "sort": "updated"})
        lines = []
        for repo in repos[:limit]:
            desc = (repo.get("description") or "").strip().replace("\n", " ")
            line = f"{repo['full_name']}"
            if desc:
                line += f" — {desc}"
            line += f" (★{repo.get('stargazers_count', 0)})"
            lines.append(line)
        return "\n".join(lines) if lines else "No repositories found."

    def list_issues(self, owner: str, repo: str, state: str = "open", limit: int = 10) -> str:
        issues = self._api(
            "GET", f"/repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": max(1, min(limit, 100))},
        )
        lines = []
        for issue in issues[:limit]:
            if "pull_request" in issue:
                continue
            labels = ",".join(label["name"] for label in issue.get("labels", []))
            suffix = f" [{labels}]" if labels else ""
            lines.append(f"#{issue['number']} {issue['title']}{suffix}")
        return "\n".join(lines) if lines else f"No {state} issues in {owner}/{repo}."

    def create_issue(self, owner: str, repo: str, title: str, body: str = "") -> str:
        issue = self._api(
            "POST", f"/repos/{owner}/{repo}/issues",
            json={"title": title, "body": body},
        )
        return f"#{issue['number']} {issue.get('html_url', '')}".strip()

    def comment_issue(self, owner: str, repo: str, number: int, body: str) -> str:
        comment = self._api(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments",
            json={"body": body},
        )
        return f"Comment posted: {comment.get('html_url', '')}".strip()

    def list_prs(self, owner: str, repo: str, state: str = "open", limit: int = 10) -> str:
        prs = self._api(
            "GET", f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": max(1, min(limit, 100))},
        )
        lines = []
        for pr in prs[:limit]:
            lines.append(f"#{pr['number']} {pr['title']} ({pr['head']['ref']}→{pr['base']['ref']})")
        return "\n".join(lines) if lines else f"No {state} PRs in {owner}/{repo}."


def _register() -> GitHubConnector:
    from zeline.connectors import register

    return register(GitHubConnector())


_register()
