"""Hacker News connector (public API, no key needed)."""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://hacker-news.firebaseio.com/v0"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class HackerNewsConnector(BaseConnector):
    id = "hackernews"
    name = "Hacker News"
    description = "Read Hacker News top stories and items (public API, no key needed)."
    auth_kind = "none"

    def connect(self, **kwargs) -> str:
        try:
            resp = requests.get(f"{API_BASE}/topstories.json", timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach Hacker News API ({exc})."
        if resp.status_code != 200:
            return f"ERROR: could not reach Hacker News API (HTTP {resp.status_code})."
        try:
            ids = resp.json()
        except ValueError:
            return "ERROR: could not reach Hacker News API (unreadable response)."
        if not isinstance(ids, list):
            return "ERROR: could not reach Hacker News API (unexpected response)."
        store.save(self.id, {"connected": True})
        return "Connected to Hacker News (public API, no key needed)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Hacker News disconnected."
        return "Hacker News was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("connected"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "public API"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Hacker News is not connected. Run: zeline connect hackernews")

    def _fetch(self, url: str):
        self._require_connected()
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Hacker News API request failed ({exc}).") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Hacker News API {resp.status_code}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Hacker News returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def top_stories(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        ids = self._fetch(f"{API_BASE}/topstories.json")
        if not isinstance(ids, list) or not ids:
            return "No top stories found."
        lines = []
        for item_id in ids[:limit]:
            item = self._fetch(f"{API_BASE}/item/{item_id}.json") or {}
            line = (
                f"{item.get('title', '(no title)')} "
                f"({item.get('score', 0)} points, by {item.get('by', '?')})"
            )
            if item.get("url"):
                line += f" — {item['url']}"
            lines.append(line)
        return "\n".join(lines) if lines else "No top stories found."

    def get_item(self, item_id: int) -> str:
        item = self._fetch(f"{API_BASE}/item/{item_id}.json")
        if not item:
            raise RuntimeError("ERROR: item not found.")
        title = str(item.get("title", "")).strip()
        lines = [f"Title: {title or '(no title)'}", f"By: {item.get('by', '?')}"]
        timestamp = item.get("time")
        if timestamp:
            try:
                when = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                )
            except (ValueError, OSError, OverflowError):
                when = str(timestamp)
            lines.append(f"Time: {when}")
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(f"Text: {text}")
        return "\n".join(lines)


def _register() -> HackerNewsConnector:
    from zeline.connectors import register

    return register(HackerNewsConnector())


_register()
