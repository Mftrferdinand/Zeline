"""Jira connector (email + API token, HTTP Basic auth).

Talks to the Jira Cloud REST API v3 at ``{base_url}/rest/api/3``.
"""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30
_API_PATH = "/rest/api/3"


def _normalize_base_url(base_url: str) -> str:
    """Strip whitespace/trailing slash; must be an https:// URL."""
    return (base_url or "").strip().rstrip("/")


class JiraConnector(BaseConnector):
    id = "jira"
    name = "Jira"
    description = "Search issues and create new issues in Jira Cloud."
    auth_kind = "pat"

    def connect(self, email: str = "", token: str = "", base_url: str = "", **kwargs) -> str:
        email = (email or kwargs.get("email") or "").strip()
        token = (token or kwargs.get("token") or "").strip()
        base_url = _normalize_base_url(base_url or kwargs.get("base_url") or "")
        if not email:
            return "ERROR: no email provided."
        if not token:
            return "ERROR: no API token provided."
        if not base_url:
            return "ERROR: no base URL provided."
        if not base_url.startswith("https://"):
            return "ERROR: base URL must be an https:// URL."
        try:
            resp = requests.get(
                f"{base_url}{_API_PATH}/myself",
                auth=(email, token),
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {base_url} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Jira rejected the credentials (HTTP {resp.status_code})."
        try:
            display_name = resp.json().get("displayName", "?")
        except ValueError:
            return "ERROR: Jira returned an unreadable response."
        store.save(self.id, {
            "email": email,
            "token": token,
            "base_url": base_url,
            "user": display_name,
        })
        return f"Connected to Jira as {display_name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Jira disconnected."
        return "Jira was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token") or not data.get("base_url"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _auth(self) -> tuple[tuple[str, str], str]:
        data = store.load(self.id) or {}
        email = data.get("email", "")
        token = data.get("token", "")
        base_url = data.get("base_url", "")
        if not email or not token or not base_url:
            raise RuntimeError("ERROR: Jira is not connected. Run 'zeline connect jira' first.")
        return (email, token), base_url

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        (email, token), base_url = self._auth()
        try:
            resp = requests.request(
                method, f"{base_url}{_API_PATH}{path}", auth=(email, token), **kwargs
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Jira API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Jira API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def search(self, jql: str, limit: int = 10) -> str:
        """Search issues by JQL; returns one "KEY: summary [status]" line per issue."""
        jql = (jql or "").strip()
        if not jql:
            return "ERROR: no JQL query provided."
        data = self._api(
            "GET", "/search/jql",
            params={
                "jql": jql,
                "maxResults": max(1, min(limit, 100)),
                "fields": "key,summary,status",
            },
        )
        issues = data.get("issues", []) if isinstance(data, dict) else []
        lines = []
        for issue in issues[:limit]:
            fields = issue.get("fields", {})
            status = fields.get("status", {}).get("name", "?")
            lines.append(f"{issue.get('key', '?')}: {fields.get('summary', '')} [{status}]")
        return "\n".join(lines) if lines else "No issues found."

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str = "",
        issue_type: str = "Task",
    ) -> str:
        """Create a new issue in *project_key*. Returns the created issue key."""
        project_key = (project_key or "").strip()
        summary = (summary or "").strip()
        issue_type = (issue_type or "Task").strip()
        if not project_key:
            return "ERROR: no project key provided."
        if not summary:
            return "ERROR: no summary provided."
        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description and description.strip():
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description.strip()}],
                    }
                ],
            }
        issue = self._api("POST", "/issue", json={"fields": fields})
        key = issue.get("key", "?") if isinstance(issue, dict) else "?"
        return f"Created issue {key}."


def _register() -> JiraConnector:
    from zeline.connectors import register

    return register(JiraConnector())


_register()
