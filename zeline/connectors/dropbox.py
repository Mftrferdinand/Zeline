"""Dropbox connector (OAuth access token, Bearer auth)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.dropboxapi.com"
_TIMEOUT = 30


class DropboxConnector(BaseConnector):
    id = "dropbox"
    name = "Dropbox"
    description = "List Dropbox files and read file metadata."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.post(
                f"{API_BASE}/2/users/get_current_account",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.dropboxapi.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Dropbox rejected the token (HTTP {resp.status_code})."
        try:
            display_name = resp.json().get("name", {}).get("display_name", "?")
        except ValueError:
            display_name = "?"
        store.save(self.id, {"token": token, "user": display_name})
        return f"Connected to Dropbox as {display_name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Dropbox disconnected."
        return "Dropbox was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("user", "linked")}

    # -- API helpers -----------------------------------------------------

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        token = data.get("token", "")
        if not token:
            raise RuntimeError("ERROR: Dropbox is not connected. Run 'zeline connect dropbox' first.")
        return {"Authorization": f"Bearer {token}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Dropbox API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Dropbox API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_files(self, path: str = "", limit: int = 10) -> str:
        """List entries in a Dropbox folder; one "name (file/folder)" line each."""
        path = (path or "").strip()
        data = self._api(
            "POST", "/2/files/list_folder",
            json={"path": path, "limit": max(1, min(limit, 100))},
        )
        entries = data.get("entries", []) if isinstance(data, dict) else []
        lines = []
        for entry in entries[:limit]:
            kind = "folder" if entry.get(".tag") == "folder" else "file"
            lines.append(f"{entry.get('name', '?')} ({kind})")
        return "\n".join(lines) if lines else "No files found."

    def get_metadata(self, path: str) -> str:
        """Show metadata for a path; "name | size | modified"."""
        path = (path or "").strip()
        if not path:
            return "ERROR: no path provided."
        meta = self._api("POST", "/2/files/get_metadata", json={"path": path})
        meta = meta if isinstance(meta, dict) else {}
        return (
            f"{meta.get('name', '?')} | {meta.get('size', '?')} | "
            f"{meta.get('client_modified', '?')}"
        )


def _register() -> DropboxConnector:
    from zeline.connectors import register

    return register(DropboxConnector())


_register()
