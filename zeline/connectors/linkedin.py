"""LinkedIn connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.linkedin.com/v2"
_TIMEOUT = 30


class LinkedInConnector(BaseConnector):
    id = "linkedin"
    name = "LinkedIn"
    description = "Read your LinkedIn profile and share posts."
    auth_kind = "pat"

    def connect(self, access_token: str = "", **kwargs) -> str:
        access_token = (access_token or kwargs.get("access_token") or kwargs.get("token") or "").strip()
        if not access_token:
            return "ERROR: no access token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.linkedin.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: LinkedIn rejected the token (HTTP {resp.status_code})."
        try:
            info = resp.json()
        except ValueError:
            return "ERROR: LinkedIn returned an unreadable response."
        name = info.get("name", "?")
        store.save(self.id, {"access_token": access_token, "name": name})
        return f"Connected to LinkedIn as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "LinkedIn disconnected."
        return "LinkedIn was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("name", "?")}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        data = store.load(self.id) or {}
        if not data.get("access_token"):
            raise RuntimeError("ERROR: LinkedIn is not connected. Run: zeline connect linkedin")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('access_token', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: LinkedIn API request failed ({exc}).") from exc
        if resp.status_code == 401:
            raise RuntimeError(
                "ERROR: LinkedIn returned 401 (access token expired). "
                "Reconnect with: zeline connect linkedin"
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: LinkedIn API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: LinkedIn returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def get_profile(self) -> str:
        """READ: return the connected member's name and email."""
        info = self._api("GET", "/userinfo")
        return f"{info.get('name', '?')} ({info.get('email', '?')})"

    def share_post(self, text: str) -> str:
        """NETWORK: publish a public text post as the connected member."""
        info = self._api("GET", "/userinfo")
        person_id = info.get("sub")
        if not person_id:
            raise RuntimeError("ERROR: LinkedIn userinfo did not return a person id.")
        author = f"urn:li:person:{person_id}"
        created = self._api(
            "POST",
            "/ugcPosts",
            json={
                "author": author,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": text},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
            },
        )
        return f"Post shared: {created.get('id', '?')}"


def _register() -> LinkedInConnector:
    from zeline.connectors import register

    return register(LinkedInConnector())


_register()
