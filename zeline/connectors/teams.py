"""Microsoft Teams connector (incoming-webhook URL).

Teams incoming webhooks are **webhook-only**: there is no read-side API that
accepts the webhook URL, so :meth:`connect` cannot validate the credential
against a live API. Instead it validates the URL *shape* (``https://`` and a
Microsoft webhook host) and stores it. Any send failure surfaces on the next
:meth:`send_message` call.
"""

from __future__ import annotations

from urllib.parse import urlparse

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _looks_like_teams_webhook(url: str) -> bool:
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.netloc:
        return False
    host = parts.netloc.lower()
    return host == "outlook.office.com" or host.endswith(".webhook.office.com")


class TeamsConnector(BaseConnector):
    id = "teams"
    name = "Microsoft Teams"
    description = "Post messages to a Teams channel via an incoming-webhook URL."
    auth_kind = "pat"

    def connect(self, url: str = "", **kwargs) -> str:
        url = (url or kwargs.get("url") or "").strip()
        if not url:
            return "ERROR: no webhook URL provided."
        if not _looks_like_teams_webhook(url):
            return "ERROR: not a Teams webhook URL"
        store.save(self.id, {"url": url})
        return "Connected to Microsoft Teams."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Microsoft Teams disconnected."
        return "Microsoft Teams was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("url"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _webhook_url(self) -> str:
        data = store.load(self.id) or {}
        url = data.get("url", "")
        if not url:
            raise RuntimeError(
                "ERROR: Microsoft Teams is not connected. Run 'zeline connect teams' first."
            )
        return url

    def _api(self, method: str, path: str, **kwargs) -> dict | str:
        # For webhook-only Teams, *path* is the full webhook URL.
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.request(method, path, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Teams API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Teams API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- user-facing operations -------------------------------------------

    def send_message(self, text: str) -> str:
        """NETWORK. Post *text* to the configured Teams channel."""
        text = (text or "").strip()
        if not text:
            return "ERROR: no text provided."
        url = self._webhook_url()
        self._api("POST", url, json={"text": text})
        return "Message sent to Teams."


def _register() -> TeamsConnector:
    from zeline.connectors import register

    return register(TeamsConnector())


_register()
