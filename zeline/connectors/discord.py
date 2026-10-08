"""Discord connector (bot token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://discord.com/api/v10"
_TIMEOUT = 30

_CHANNEL_TYPES = {
    0: "text",
    1: "dm",
    2: "voice",
    3: "group-dm",
    4: "category",
    5: "news",
    10: "news-thread",
    11: "public-thread",
    12: "private-thread",
    13: "stage",
    14: "directory",
    15: "forum",
    16: "media",
}


class DiscordConnector(BaseConnector):
    id = "discord"
    name = "Discord"
    description = "List guild channels and send messages via a bot token."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/users/@me",
                headers={"Authorization": f"Bot {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach discord.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Discord rejected the token (HTTP {resp.status_code})."
        try:
            body = resp.json() or {}
        except ValueError:
            return "ERROR: Discord returned an unreadable response."
        if body.get("bot", True) is not True:
            return "ERROR: this token is not a bot token."
        username = body.get("username", "?")
        store.save(self.id, {"token": token, "user": username})
        return f"Connected to Discord as @{username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Discord disconnected."
        return "Discord was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('user', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _require_token(self) -> str:
        data = store.load(self.id) or {}
        token = data.get("token")
        if not token:
            raise RuntimeError("ERROR: Discord is not connected (run `zeline connect discord` first).")
        return token

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update({"Authorization": f"Bot {self._require_token()}"})
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Discord API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            try:
                message = (resp.json() or {}).get("message")
            except (ValueError, AttributeError):
                message = None
            detail = message or f"HTTP {resp.status_code}"
            raise RuntimeError(f"ERROR: Discord API: {detail}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_channels(self, guild_id: str, limit: int = 20) -> str:
        guild_id = (guild_id or "").strip()
        if not guild_id:
            return "ERROR: no guild_id provided."
        limit = max(1, min(limit, 100))
        channels = self._api("GET", f"/guilds/{guild_id}/channels")
        lines = []
        for channel in channels[:limit]:
            ctype = _CHANNEL_TYPES.get(channel.get("type"), "unknown")
            lines.append(f"#{channel.get('name', '?')} ({ctype})")
        return "\n".join(lines) if lines else f"No channels in guild {guild_id}."

    def send_message(self, channel_id: str, content: str) -> str:
        channel_id = (channel_id or "").strip()
        content = (content or "").strip()
        if not channel_id:
            return "ERROR: no channel_id provided."
        if not content:
            return "ERROR: no message content provided."
        message = self._api(
            "POST", f"/channels/{channel_id}/messages", json={"content": content}
        )
        return f"Pesan terkirim: {message.get('id', '?')}."


def _register() -> DiscordConnector:
    from zeline.connectors import register

    return register(DiscordConnector())


_register()
