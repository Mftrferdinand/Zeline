"""Mastodon connector (access token, any instance)."""
from __future__ import annotations

import re

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30

_TAG_RE = re.compile(r"<[^>]+>")


def _normalize_instance(instance: str) -> str:
    """Normalize to ``https://<host>`` with no trailing slash.

    A bare host gains the ``https://`` scheme; an explicit scheme
    (``http://`` or ``https://``) is kept as given.
    """
    inst = (instance or "").strip().rstrip("/")
    if "://" not in inst:
        inst = "https://" + inst
    return inst


def _strip_html(html: str) -> str:
    return _TAG_RE.sub("", html or "").strip()


class MastodonConnector(BaseConnector):
    id = "mastodon"
    name = "Mastodon"
    description = "Post toots and read timelines on any Mastodon instance."
    auth_kind = "pat"

    def connect(self, access_token: str = "", instance: str = "", **kwargs) -> str:
        access_token = (access_token or kwargs.get("access_token") or "").strip()
        if not access_token:
            return "ERROR: no access token provided."
        raw_instance = (instance or kwargs.get("instance") or "").strip()
        if not raw_instance:
            return "ERROR: no instance provided."
        instance = _normalize_instance(raw_instance)
        try:
            resp = requests.get(
                f"{instance}/api/v1/accounts/verify_credentials",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {instance} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Mastodon rejected the credentials (HTTP {resp.status_code})."
        try:
            username = resp.json().get("username", "?")
        except ValueError:
            return "ERROR: Mastodon returned an unreadable response."
        store.save(
            self.id,
            {"access_token": access_token, "instance": instance, "username": username},
        )
        return f"Connected to Mastodon as @{username} on {instance}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Mastodon disconnected."
        return "Mastodon was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('username', '?')} on {data.get('instance', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _api_root(self) -> str:
        data = store.load(self.id) or {}
        return data.get("instance") or ""

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('access_token', '')}"}

    def _require_connected(self) -> None:
        data = store.load(self.id) or {}
        if not data.get("access_token") or not data.get("instance"):
            raise RuntimeError("ERROR: Mastodon is not connected. Run: zeline connect mastodon")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{self._api_root()}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Mastodon API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Mastodon API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def post_toot(self, text: str, visibility: str = "public") -> str:
        status_post = self._api(
            "POST",
            "/api/v1/statuses",
            json={"status": text, "visibility": visibility},
        )
        url = (status_post.get("url") or "").strip()
        return f"Toot posted: {url}".strip()

    def read_timeline(self, limit: int = 10) -> str:
        statuses = self._api(
            "GET",
            "/api/v1/timelines/home",
            params={"limit": max(1, min(limit, 100))},
        )
        lines = []
        for item in (statuses or [])[:limit]:
            account = item.get("account") or {}
            display_name = account.get("display_name") or account.get("acct", "?")
            acct = account.get("acct", "?")
            text = _strip_html(item.get("content", ""))[:200]
            lines.append(f"{display_name} (@{acct}): {text}")
        return "\n".join(lines) if lines else "No toots found."


def _register() -> MastodonConnector:
    from zeline.connectors import register

    return register(MastodonConnector())


_register()
