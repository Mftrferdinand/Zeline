"""Reddit connector (script app credentials)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_TIMEOUT = 30

_SORTS = ("hot", "new", "top")


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class RedditConnector(BaseConnector):
    id = "reddit"
    name = "Reddit"
    description = "Read subreddit posts and search Reddit via the API."
    auth_kind = "pat"

    def connect(
        self,
        client_id: str = "",
        client_secret: str = "",
        username: str = "",
        password: str = "",
        **kwargs,
    ) -> str:
        client_id = (client_id or kwargs.get("client_id") or "").strip()
        client_secret = (client_secret or kwargs.get("client_secret") or "").strip()
        username = (username or kwargs.get("username") or "").strip()
        password = password or kwargs.get("password") or ""
        if not client_id or not client_secret or not username or not password:
            return "ERROR: client_id, client_secret, username and password are required."
        try:
            resp = requests.post(
                TOKEN_URL,
                auth=(client_id, client_secret),
                data={
                    "grant_type": "password",
                    "username": username,
                    "password": password,
                },
                headers={"User-Agent": f"zeline/0.1 by {username}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach www.reddit.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Reddit rejected the credentials (HTTP {resp.status_code})."
        try:
            token = resp.json().get("access_token")
        except ValueError:
            return "ERROR: Reddit returned an unreadable response."
        if not token:
            return "ERROR: Reddit did not return an access token."
        store.save(
            self.id,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "username": username,
                "password": password,
            },
        )
        return f"Connected to Reddit as u/{username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Reddit disconnected."
        return "Reddit was not connected."

    def status(self) -> dict:
        data = store.load(self.id) or {}
        if (
            not data
            or not data.get("client_id")
            or not data.get("client_secret")
            or not data.get("username")
            or not data.get("password")
        ):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"u/{data.get('username', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _creds(self) -> dict:
        data = store.load(self.id) or {}
        if (
            not data.get("client_id")
            or not data.get("client_secret")
            or not data.get("username")
            or not data.get("password")
        ):
            raise RuntimeError("ERROR: Reddit is not connected. Run: zeline connect reddit")
        return data

    def _token(self) -> str:
        data = self._creds()
        username = data["username"]
        try:
            resp = requests.post(
                TOKEN_URL,
                auth=(data["client_id"], data["client_secret"]),
                data={
                    "grant_type": "password",
                    "username": username,
                    "password": data["password"],
                },
                headers={"User-Agent": f"zeline/0.1 by {username}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Reddit token request failed ({exc}).") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"ERROR: Reddit rejected the credentials (HTTP {resp.status_code}).")
        try:
            token = resp.json().get("access_token")
        except ValueError:
            raise RuntimeError("ERROR: Reddit returned an unreadable token response.") from None
        if not token:
            raise RuntimeError("ERROR: Reddit did not return an access token.")
        return token

    def _headers(self, token: str, username: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": f"zeline/0.1 by {username}",
        }

    def _api(self, method: str, path: str, **kwargs) -> dict:
        data = self._creds()
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers(self._token(), data["username"]))
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Reddit API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Reddit API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError(f"ERROR: Reddit returned an unreadable response on {path}.") from None

    @staticmethod
    def _format_post(child: dict) -> str:
        data = (child or {}).get("data") or {}
        title = str(data.get("title", "")).replace("\n", " ").strip()
        return (
            f"{title} ({data.get('score', 0)} points, "
            f"{data.get('num_comments', 0)} comments, u/{data.get('author', '?')})"
        )

    # -- user-facing operations -------------------------------------------

    def list_subreddit_posts(self, subreddit: str, sort: str = "hot", limit: int = 10) -> str:
        sort = sort if sort in _SORTS else "hot"
        limit = _clamp(limit)
        payload = self._api("GET", f"/r/{subreddit}/{sort}", params={"limit": limit})
        children = (payload or {}).get("data", {}).get("children", [])
        lines = [self._format_post(child) for child in children[:limit]]
        return "\n".join(lines) if lines else f"No posts found in r/{subreddit}."

    def search(self, query: str, subreddit: str = "", limit: int = 10) -> str:
        limit = _clamp(limit)
        params: dict = {"q": query, "limit": limit}
        if subreddit:
            params.update({"restrict_sr": "true", "sr": subreddit})
        payload = self._api("GET", "/search", params=params)
        children = (payload or {}).get("data", {}).get("children", [])
        lines = [self._format_post(child) for child in children[:limit]]
        return "\n".join(lines) if lines else "No posts found."


def _register() -> RedditConnector:
    from zeline.connectors import register

    return register(RedditConnector())


_register()
