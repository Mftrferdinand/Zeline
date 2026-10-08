"""monday.com connector (personal API token, GraphQL)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_URL = "https://api.monday.com/v2"
_TIMEOUT = 30


class MondayConnector(BaseConnector):
    id = "monday"
    name = "monday.com"
    description = "List boards and board items on monday.com via GraphQL."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.post(
                API_URL,
                json={"query": "{ me { name } }"},
                headers={"Authorization": token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.monday.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: monday.com rejected the token (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: monday.com returned an unreadable response."
        me = (body.get("data") or {}).get("me") or {}
        name = me.get("name")
        if not name:
            return "ERROR: monday.com rejected the token (no user returned)."
        store.save(self.id, {"token": token, "user": name})
        return f"Connected to monday.com as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "monday.com disconnected."
        return "monday.com was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"{data.get('user', '?')}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> dict:
        data = store.load(self.id) or {}
        if not data.get("token"):
            raise RuntimeError("ERROR: monday.com is not connected. Run 'zeline connect monday' first.")
        return data

    def _gql(self, query: str) -> dict:
        """Run a GraphQL query; returns the ``data`` dict."""
        try:
            resp = requests.post(
                API_URL,
                json={"query": query},
                headers={"Authorization": self._require_connected()["token"]},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: monday.com API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: monday.com API {resp.status_code}.")
        body = resp.json()
        if body.get("errors"):
            messages = "; ".join(e.get("message", "?") for e in body["errors"])
            raise RuntimeError(f"ERROR: monday.com GraphQL errors: {messages}.")
        return body.get("data") or {}

    # -- user-facing operations -------------------------------------------

    def list_boards(self, limit: int = 10) -> str:
        n = max(1, min(limit, 100))
        data = self._gql(f"{{ boards (limit: {n}) {{ id name }} }}")
        lines = []
        for board in data.get("boards", [])[:limit]:
            lines.append(f"{board.get('id')}: {board.get('name')}")
        return "\n".join(lines) if lines else "No boards found."

    def list_items(self, board_id: str, limit: int = 10) -> str:
        board_id = (board_id or "").strip()
        if not board_id:
            return "ERROR: no board_id provided."
        n = max(1, min(limit, 100))
        data = self._gql(
            f"{{ boards (ids: {board_id}) {{ items_page (limit: {n}) {{ items {{ id name }} }} }} }}"
        )
        items = []
        for board in data.get("boards", []):
            items.extend(board.get("items_page", {}).get("items", []))
        lines = []
        for item in items[:limit]:
            lines.append(f"{item.get('id')}: {item.get('name')}")
        return "\n".join(lines) if lines else f"No items in board {board_id}."


def _register() -> MondayConnector:
    from zeline.connectors import register

    return register(MondayConnector())


_register()
