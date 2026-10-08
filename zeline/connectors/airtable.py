"""Airtable connector (personal access token).

The operator links it once with ``zeline connect airtable``: paste an
Airtable personal access token. The token is validated (GET the identity
endpoint) before anything is stored.

https://airtable.com/developers/web/api/introduction
"""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.airtable.com/v0"
_TIMEOUT = 30


class AirtableConnector(BaseConnector):
    id = "airtable"
    name = "Airtable"
    description = "Read and create records in Airtable bases and tables."
    auth_kind = "pat"

    def connect(self, token: str = "", **kwargs) -> str:
        token = (token or kwargs.get("token") or "").strip()
        if not token:
            return "ERROR: no token provided."
        try:
            resp = requests.get(
                f"{API_BASE}/meta/whoami",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.airtable.com ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Airtable rejected the token (HTTP {resp.status_code})."
        try:
            who = resp.json()
        except ValueError:
            return "ERROR: Airtable returned an unreadable response."
        if not isinstance(who, dict) or not who.get("id"):
            return "ERROR: Airtable rejected the token."
        store.save(self.id, {"token": token})
        return "Connected to Airtable."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Airtable disconnected."
        return "Airtable was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "linked"}

    # -- API helpers -----------------------------------------------------

    def _token(self) -> str:
        data = store.load(self.id) or {}
        token = data.get("token", "")
        if not token:
            raise RuntimeError(
                "ERROR: Airtable not connected. "
                "The owner can run `zeline connect airtable` to link it."
            )
        return token

    def _api(self, method: str, path: str, **kwargs) -> dict:
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {self._token()}"
        try:
            resp = requests.request(method, f"{API_BASE}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Airtable API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Airtable API {resp.status_code} on {path}.")
        return resp.json()

    # -- user-facing operations ------------------------------------------

    def list_records(self, base_id: str, table_id: str, limit: int = 10) -> str:
        base_id = (base_id or "").strip()
        table_id = (table_id or "").strip()
        if not base_id or not table_id:
            return "ERROR: base_id and table_id are required."
        limit = max(1, min(limit, 100))
        data = self._api(
            "GET", f"/{base_id}/{table_id}", params={"maxRecords": limit}
        )
        lines = []
        for record in data.get("records", [])[:limit]:
            fields = record.get("fields", {}) or {}
            if fields:
                pairs = ", ".join(f"{key}={value}" for key, value in fields.items())
                lines.append(f"{record['id']}: {pairs}")
            else:
                lines.append(f"{record['id']}: (no fields)")
        return "\n".join(lines) if lines else "No records found."

    def create_record(self, base_id: str, table_id: str, fields: dict) -> str:
        base_id = (base_id or "").strip()
        table_id = (table_id or "").strip()
        if not base_id or not table_id:
            return "ERROR: base_id and table_id are required."
        if not fields or not isinstance(fields, dict):
            return "ERROR: fields must be a non-empty dict."
        record = self._api(
            "POST", f"/{base_id}/{table_id}", json={"fields": fields}
        )
        return f"Record dibuat: {record.get('id', '?')}"


def _register() -> AirtableConnector:
    from zeline.connectors import register

    return register(AirtableConnector())


_register()
