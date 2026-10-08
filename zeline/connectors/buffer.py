"""Buffer connector (personal access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.bufferapp.com/1"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class BufferConnector(BaseConnector):
    id = "buffer"
    name = "Buffer"
    description = "Read Buffer profiles and publish posts."
    auth_kind = "pat"

    def connect(self, access_token: str = "", **kwargs) -> str:
        access_token = (access_token or kwargs.get("token") or "").strip()
        if not access_token:
            return "ERROR: no access token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user.json",
                params={"access_token": access_token},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.bufferapp.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Buffer rejected the access token (HTTP {resp.status_code})."
        try:
            data = resp.json()
            user_id = data.get("id", "?") if isinstance(data, dict) else "?"
        except ValueError:
            user_id = "?"
        store.save(self.id, {"access_token": access_token})
        return f"Connected to Buffer (user {user_id})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Buffer disconnected."
        return "Buffer was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "access token stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Buffer is not connected. Run: zeline connect buffer")

    def _access_token(self) -> str:
        data = store.load(self.id) or {}
        return data.get("access_token", "")

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        params = kwargs.pop("params", {}) or {}
        params["access_token"] = self._access_token()
        kwargs["params"] = params
        try:
            resp = requests.request(method, f"{API_BASE}{path}", **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Buffer API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Buffer API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Buffer returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_profiles(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/profiles.json")
        items = data if isinstance(data, list) else []
        lines = []
        for profile in items[:limit]:
            pid = profile.get("id", "?")
            service = profile.get("service", "?")
            username = profile.get("service_username") or profile.get("username", "?")
            lines.append(f"{pid}: {service} ({username})")
        return "\n".join(lines) if lines else "No profiles found."

    def create_post(self, text: str, profile_ids: list) -> str:
        profile_ids = [str(pid) for pid in (profile_ids or [])]
        if not profile_ids:
            return "ERROR: profile_ids required."
        data = self._api(
            "POST",
            "/updates/create.json",
            json={"text": text, "profile_ids": profile_ids},
        )
        if isinstance(data, dict) and data.get("success"):
            return f"Post queued to {len(profile_ids)} profile(s)."
        return "Post sent, but Buffer did not confirm success."


def _register() -> BufferConnector:
    from zeline.connectors import register

    return register(BufferConnector())


_register()
