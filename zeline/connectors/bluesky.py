"""Bluesky connector (app password)."""
from __future__ import annotations

import datetime

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

PDS_HOST = "https://bsky.social"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class BlueskyConnector(BaseConnector):
    id = "bluesky"
    name = "Bluesky"
    description = "Post and read timelines on Bluesky (app password)."
    auth_kind = "pat"

    def connect(self, identifier: str = "", app_password: str = "", **kwargs) -> str:
        identifier = (identifier or kwargs.get("identifier") or "").strip()
        app_password = (app_password or kwargs.get("app_password") or "").strip()
        if not identifier or not app_password:
            return "ERROR: identifier and app_password are required."
        try:
            resp = requests.post(
                f"{PDS_HOST}/xrpc/com.atproto.server.createSession",
                json={"identifier": identifier, "password": app_password},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach bsky.social ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Bluesky rejected the credentials (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            return "ERROR: Bluesky returned an unreadable response."
        did = payload.get("did", "")
        handle = payload.get("handle", "?")
        if not did:
            return "ERROR: Bluesky did not return a session DID."
        store.save(
            self.id,
            {
                "identifier": identifier,
                "app_password": app_password,
                "did": did,
                "handle": handle,
            },
        )
        return f"Connected to Bluesky as @{handle}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Bluesky disconnected."
        return "Bluesky was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("app_password"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('handle', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> dict:
        data = store.load(self.id) or {}
        if not data.get("app_password") or not data.get("identifier"):
            raise RuntimeError("ERROR: Bluesky is not connected. Run: zeline connect bluesky")
        return data

    def _session(self) -> tuple[str, str]:
        """Return (did, accessJwt) for a fresh session."""
        data = self._require_connected()
        try:
            resp = requests.post(
                f"{PDS_HOST}/xrpc/com.atproto.server.createSession",
                json={"identifier": data["identifier"], "password": data["app_password"]},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Bluesky session request failed ({exc}).") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"ERROR: Bluesky session rejected (HTTP {resp.status_code}).")
        try:
            payload = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Bluesky returned an unreadable session response.") from None
        did = payload.get("did") or data.get("did", "")
        access_jwt = payload.get("accessJwt", "")
        if not did or not access_jwt:
            raise RuntimeError("ERROR: Bluesky did not return a session token.")
        return did, access_jwt

    @staticmethod
    def _now_iso() -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        did, access_jwt = self._session()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {access_jwt}"
        try:
            resp = requests.request(method, f"{PDS_HOST}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Bluesky API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Bluesky API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def post(self, text: str) -> str:
        did, access_jwt = self._session()
        try:
            resp = requests.post(
                f"{PDS_HOST}/xrpc/com.atproto.repo.createRecord",
                headers={"Authorization": f"Bearer {access_jwt}"},
                json={
                    "repo": did,
                    "collection": "app.bsky.feed.post",
                    "record": {
                        "$type": "app.bsky.feed.post",
                        "text": text,
                        "createdAt": self._now_iso(),
                    },
                },
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Bluesky API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError("ERROR: Bluesky API error on /xrpc/com.atproto.repo.createRecord.")
        uri = resp.json().get("uri", "")
        return f"Post published: {uri}".rstrip()

    def read_timeline(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        feed = self._api(
            "GET",
            "/xrpc/app.bsky.feed.getTimeline",
            params={"limit": limit},
        )
        items = feed.get("feed", []) if isinstance(feed, dict) else []
        lines = []
        for item in items[:limit]:
            post = item.get("post") or {}
            author = post.get("author") or {}
            record = post.get("record") or {}
            handle = author.get("handle", "?")
            text = str(record.get("text", "")).replace("\n", " ")
            lines.append(f"@{handle}: {text[:200]}")
        return "\n".join(lines) if lines else "No posts in timeline."


def _register() -> BlueskyConnector:
    from zeline.connectors import register

    return register(BlueskyConnector())


_register()
