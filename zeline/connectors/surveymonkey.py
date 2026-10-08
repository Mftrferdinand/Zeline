"""SurveyMonkey connector (OAuth access token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.surveymonkey.com/v3"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class SurveyMonkeyConnector(BaseConnector):
    id = "surveymonkey"
    name = "SurveyMonkey"
    description = "Read SurveyMonkey surveys."
    auth_kind = "pat"

    def connect(self, access_token: str = "", **kwargs) -> str:
        token = (access_token or kwargs.get("access_token") or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no access token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/users/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.surveymonkey.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: SurveyMonkey rejected the access token (HTTP {resp.status_code})."
        try:
            name = resp.json().get("name", "?")
        except ValueError:
            name = "?"
        store.save(self.id, {"access_token": token})
        return f"Connected to SurveyMonkey as {name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "SurveyMonkey disconnected."
        return "SurveyMonkey was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("access_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "access token stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: SurveyMonkey is not connected. Run: zeline connect surveymonkey")

    def _headers(self) -> dict:
        data = store.load(self.id) or {}
        return {"Authorization": f"Bearer {data.get('access_token', '')}"}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: SurveyMonkey API request failed ({exc}).") from exc
        if resp.status_code == 401:
            raise RuntimeError(
                "ERROR: SurveyMonkey returned 401 (access token expired). "
                "Reconnect with: zeline connect surveymonkey"
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: SurveyMonkey API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: SurveyMonkey returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_surveys(self, limit: int = 10) -> str:
        limit = _clamp(limit)
        data = self._api("GET", "/surveys", params={"per_page": limit})
        surveys = []
        if isinstance(data, dict):
            surveys = data.get("data") or data.get("items") or []
        lines = []
        for survey in surveys[:limit]:
            lines.append(f"{survey.get('id', '?')}: {survey.get('title', '(no title)')}")
        return "\n".join(lines) if lines else "No surveys found."


def _register() -> SurveyMonkeyConnector:
    from zeline.connectors import register

    return register(SurveyMonkeyConnector())


_register()
