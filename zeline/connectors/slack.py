"""Slack connector (bot token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://slack.com/api"
_TIMEOUT = 30


def _clamp_limit(limit: int) -> int:
    """Clamp a user-supplied limit to the 1..100 range Slack accepts."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    return max(1, min(limit, 100))


class SlackConnector(BaseConnector):
    id = "slack"
    name = "Slack"
    description = "List channels, read channel history and send messages."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/auth.test",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach slack.com/api ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Slack rejected the token (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: Slack returned an unreadable response."
        if not body.get("ok"):
            return f"ERROR: Slack rejected the token ({body.get('error', 'unknown')})."
        store.save(
            self.id,
            {
                "token": token,
                "team": body.get("team", "?"),
                "user": body.get("user", "?"),
            },
        )
        return f"Connected to Slack workspace {body.get('team', '?')} as {body.get('user', '?')}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Slack disconnected."
        return "Slack was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        team = data.get("team", "?")
        return {"connected": True, "detail": f"workspace {team}"}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('token', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Slack API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Slack API {resp.status_code} on {path}.")
        data = resp.json()
        if isinstance(data, dict) and data.get("ok") is False:
            raise RuntimeError(f"ERROR: Slack API error: {data.get('error', 'unknown')}.")
        return data

    # -- user-facing operations -------------------------------------------

    def list_channels(self, limit: int = 10) -> str:
        """READ. List visible channels as ``#name (id)``."""
        data = self._api(
            "GET",
            "/conversations.list",
            params={
                "limit": _clamp_limit(limit),
                "types": "public_channel,private_channel",
                "exclude_archived": True,
            },
        )
        lines = [
            f"#{ch.get('name', '?')} ({ch.get('id', '?')})"
            for ch in data.get("channels", [])
        ]
        return "\n".join(lines) if lines else "No channels found."

    def send_message(self, channel: str, text: str) -> str:
        """NETWORK. Post *text* to *channel* (id or #name)."""
        data = self._api(
            "POST",
            "/chat.postMessage",
            json={"channel": channel, "text": text},
        )
        return f"Message sent to {channel} (ts {data.get('ts', '?')})."

    def read_history(self, channel: str, limit: int = 10) -> str:
        """READ. Read recent channel history as ``user: text`` lines."""
        data = self._api(
            "GET",
            "/conversations.history",
            params={"channel": channel, "limit": _clamp_limit(limit)},
        )
        lines = []
        for msg in data.get("messages", []):
            text = " ".join((msg.get("text") or "").split())
            if len(text) > 120:
                text = text[:117] + "..."
            lines.append(f"{msg.get('user', '?')}: {text}")
        return "\n".join(lines) if lines else f"No messages in {channel}."


def _register() -> SlackConnector:
    from zeline.connectors import register

    return register(SlackConnector())


_register()
