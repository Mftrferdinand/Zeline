"""Box connector (access token, Bearer auth)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.box.com/2.0"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class BoxConnector(BaseConnector):
    id = "box"
    name = "Box"
    description = "Read Box files and folder listings."
    auth_kind = "pat"

    def connect(self, access_token: str = "", **kwargs) -> str:
        access_token = (access_token or kwargs.get("access_token") or kwargs.get("token") or "").strip()
        if not access_token:
            return "ERROR: access_token is required."
        try:
            resp = requests.get(
                f"{API_BASE}/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Box ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Box rejected the access token (HTTP {resp.status_code})."
        login = resp.json().get("login", "?")
        store.save(self.id, {"access_token": access_token, "login": login})
        return f"Connected to Box as {login}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Box disconnected."
        return "Box was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("login", "box user")}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Box is not connected. Run: zeline connect box")

    def _headers(self) -> dict:
        self._require_connected()
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('access_token', '')}"}

    def _get(self, path: str, params: dict | None = None):
        try:
            resp = requests.get(
                f"{API_BASE}{path}", params=params, headers=self._headers(), timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Box API request failed ({exc}).") from exc
        if resp.status_code == 401:
            raise RuntimeError(
                "ERROR: Box returned 401 (access token expired, ~1h lifetime). "
                "Reconnect with: zeline connect box"
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Box API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_files(self, folder_id: str = "0", limit: int = 10) -> str:
        limit = _clamp(limit)
        payload = self._get(f"/folders/{folder_id}/items", {"limit": limit})
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        lines = []
        for entry in entries[:limit]:
            name = entry.get("name", "?")
            if entry.get("type") == "folder":
                lines.append(f"[folder] {name}")
            else:
                lines.append(f"[file] {name} ({entry.get('size', 0)} bytes)")
        return "\n".join(lines) if lines else f"No items in folder {folder_id}."

    def get_file_info(self, file_id: str) -> str:
        info = self._get(f"/files/{file_id}")
        if not isinstance(info, dict):
            raise RuntimeError("ERROR: Box returned an unexpected file response.")
        return f"{info.get('name', '?')}: {info.get('size', 0)} bytes, {info.get('modified_at', '?')}"


def _register() -> BoxConnector:
    from zeline.connectors import register

    return register(BoxConnector())


_register()
