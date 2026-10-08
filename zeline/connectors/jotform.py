"""JotForm connector (API key sent as the apiKey query parameter)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.jotform.com"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class JotFormConnector(BaseConnector):
    id = "jotform"
    name = "JotForm"
    description = "Read JotForm forms and their submissions."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/user",
                params={"apiKey": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.jotform.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: JotForm rejected the API key (HTTP {resp.status_code})."
        try:
            data = resp.json()
        except ValueError:
            return "ERROR: could not reach JotForm (unreadable response)."
        if not isinstance(data, dict) or data.get("responseCode") != 200:
            message = data.get("message", "unknown error") if isinstance(data, dict) else "unknown error"
            return f"ERROR: JotForm rejected the API key ({message})."
        content = data.get("content") or {}
        username = content.get("username", "?")
        store.save(self.id, {"api_key": api_key})
        return f"Connected to JotForm as {username}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "JotForm disconnected."
        return "JotForm was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: JotForm is not connected. Run: zeline connect jotform")

    def _api_key(self) -> str:
        data = store.load(self.id) or {}
        return str(data.get("api_key", ""))

    def _content(self, data: dict | list) -> dict | list:
        """Unwrap the JotForm response envelope ({responseCode, content})."""
        if isinstance(data, dict) and "content" in data:
            return data["content"]
        return data

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        params = kwargs.pop("params", {}) or {}
        params["apiKey"] = self._api_key()
        try:
            resp = requests.request(method, f"{API_BASE}{path}", params=params, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: JotForm API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: JotForm API {resp.status_code} on {path}.")
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("ERROR: JotForm returned an unreadable response.") from None
        if isinstance(data, dict) and data.get("responseCode", 200) >= 400:
            raise RuntimeError(f"ERROR: JotForm API {data.get('responseCode')} on {path}.")
        return self._content(data)

    # -- user-facing operations -------------------------------------------

    def list_forms(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        content = self._api("GET", "/user/forms", params={"limit": limit})
        forms = list(content.values()) if isinstance(content, dict) else content or []
        lines = []
        for form in forms[:limit]:
            lines.append(f"{form.get('id', '?')}: {form.get('title', '(no title)')}")
        return "\n".join(lines) if lines else "No forms found."

    def get_submissions(self, form_id: str, limit: int = 10) -> str:
        limit = _clamp(limit)
        content = self._api("GET", f"/form/{form_id}/submissions", params={"limit": limit})
        submissions = list(content.values()) if isinstance(content, dict) else content or []
        lines = []
        for submission in submissions[:limit]:
            lines.append(
                f"{submission.get('id', '?')} (created {submission.get('created_at', '?')})"
            )
        return "\n".join(lines) if lines else f"No submissions found for form {form_id}."


def _register() -> JotFormConnector:
    from zeline.connectors import register

    return register(JotFormConnector())


_register()
