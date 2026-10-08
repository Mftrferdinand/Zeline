"""Trello connector (API key + token, passed as query params)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.trello.com/1"
_TIMEOUT = 30


class TrelloConnector(BaseConnector):
    id = "trello"
    name = "Trello"
    description = "List boards and cards; create cards."
    auth_kind = "pat"

    def connect(self, api_key: str = "", token: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or "").strip()
        token = (token or kwargs.get("token") or "").strip()
        if not api_key or not token:
            return "ERROR: both api_key and token are required."
        try:
            resp = requests.get(
                f"{API_BASE}/members/me",
                params={"key": api_key, "token": token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.trello.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Trello rejected the credentials (HTTP {resp.status_code})."
        try:
            username = resp.json().get("username", "?")
        except ValueError:
            return "ERROR: Trello returned an unreadable response."
        store.save(self.id, {"api_key": api_key, "token": token, "username": username})
        return f"Connected to Trello as @{username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Trello disconnected."
        return "Trello was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key") or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"@{data.get('username', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _auth_params(self) -> dict:
        data = store.load(self.id) or {}
        api_key = data.get("api_key") or ""
        token = data.get("token") or ""
        if not api_key or not token:
            raise RuntimeError(
                "ERROR: Trello is not connected (run `zeline connect trello` first)."
            )
        return {"key": api_key, "token": token}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        params = self._auth_params()
        extra = kwargs.pop("params", {}) or {}
        params.update(extra)
        try:
            resp = requests.request(method, f"{API_BASE}{path}", params=params, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Trello API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Trello API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_boards(self, limit: int = 10) -> str:
        limit = max(1, min(limit, 100))
        boards = self._api("GET", "/members/me/boards", params={"fields": "name,url"})
        lines = []
        for board in boards[:limit]:
            line = f"{board['name']}"
            if board.get("url"):
                line += f" — {board['url']}"
            line += f" (id {board['id']})"
            lines.append(line)
        return "\n".join(lines) if lines else "No boards found."

    def list_cards(self, board_id: str, limit: int = 20) -> str:
        board_id = (board_id or "").strip()
        if not board_id:
            return "ERROR: board_id is required."
        limit = max(1, min(limit, 100))
        cards = self._api(
            "GET", f"/boards/{board_id}/cards",
            params={"fields": "name,shortLink"},
        )
        lines = []
        for card in cards[:limit]:
            line = f"{card['name']}"
            if card.get("shortLink"):
                line += f" (https://trello.com/c/{card['shortLink']})"
            line += f" (id {card['id']})"
            lines.append(line)
        return "\n".join(lines) if lines else f"No cards on board {board_id}."

    def create_card(self, list_id: str, name: str, desc: str = "") -> str:
        list_id = (list_id or "").strip()
        name = (name or "").strip()
        if not list_id:
            return "ERROR: list_id is required."
        if not name:
            return "ERROR: card name is required."
        card = self._api(
            "POST", "/cards",
            params={"idList": list_id, "name": name, "desc": desc or ""},
        )
        url = card.get("shortUrl", "")
        return f"Card created: {name} ({url})".strip()


def _register() -> TrelloConnector:
    from zeline.connectors import register

    return register(TrelloConnector())


_register()
