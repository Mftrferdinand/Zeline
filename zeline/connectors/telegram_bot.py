"""Telegram Bot connector (Bot API).

A sender connector: posts messages through a Telegram bot via the Bot API.
This is separate from the Telegram gateway (``zeline/gateways/``), which
receives and handles chat traffic — this connector only sends.
"""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_API_BASE = "https://api.telegram.org"
_TIMEOUT = 30


class TelegramBotConnector(BaseConnector):
    id = "telegram_bot"
    name = "Telegram Bot"
    description = (
        "Send messages through a Telegram bot (Bot API). "
        "This is a sender connector, distinct from the Telegram gateway."
    )
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no bot token provided."
        try:
            resp = requests.get(f"{_API_BASE}/bot{token}/getMe", timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach the Telegram Bot API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Telegram rejected the bot token (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: Telegram returned an unreadable response."
        if not body.get("ok"):
            return f"ERROR: Telegram API: {body.get('description', 'unknown error')}."
        result = body.get("result") or {}
        username = result.get("username", "?")
        store.save(self.id, {"token": token, "username": username})
        return f"Connected to Telegram bot @{username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Telegram Bot disconnected."
        return "Telegram Bot was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('username', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _api(self, api_method: str, **kwargs) -> dict:
        """Call a Bot API method with the stored token.

        Raises RuntimeError starting with "ERROR:" on any failure; the
        token value never appears in the message.
        """
        data = store.load(self.id) or {}
        token = data.get("token") or ""
        if not token:
            raise RuntimeError("ERROR: telegram_bot is not connected.")
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.post(f"{_API_BASE}/bot{token}/{api_method}", **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Telegram Bot API request failed ({exc}).") from exc
        try:
            body = resp.json()
        except ValueError:
            raise RuntimeError(
                f"ERROR: Telegram API returned HTTP {resp.status_code} with an unreadable body."
            ) from None
        if not body.get("ok"):
            raise RuntimeError(f"ERROR: Telegram API: {body.get('description', 'unknown error')}.")
        return body.get("result") or {}

    # -- user-facing operations -------------------------------------------

    def get_me(self) -> str:
        """Bot identity (read-only; used to verify the stored token)."""
        result = self._api("getMe")
        username = result.get("username", "?")
        bot_id = result.get("id", "?")
        return f"@{username} (id {bot_id})"

    def send_message(self, chat_id: str | int, text: str) -> str:
        """Send a text message via the bot."""
        result = self._api("sendMessage", json={"chat_id": chat_id, "text": text})
        return f"Pesan terkirim (message_id {result.get('message_id', '?')})."


def _register() -> TelegramBotConnector:
    from zeline.connectors import register

    return register(TelegramBotConnector())


_register()
