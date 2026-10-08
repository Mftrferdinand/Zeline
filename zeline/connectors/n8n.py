"""n8n connector (API key + self-hosted base URL)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class N8nConnector(BaseConnector):
    id = "n8n"
    name = "n8n"
    description = "List and trigger n8n workflows via API."
    auth_kind = "pat"

    def connect(self, api_key: str = "", base_url: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        base_url = (base_url or kwargs.get("base_url") or "").strip().rstrip("/")
        if not api_key:
            return "ERROR: no API key provided."
        if not base_url:
            return "ERROR: no base URL provided (e.g. https://n8n.example.com)."
        try:
            resp = requests.get(
                f"{base_url}/api/v1/workflows",
                headers={"X-N8N-API-KEY": api_key},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach {base_url} ({exc})."
        if resp.status_code != 200:
            return f"ERROR: n8n rejected the API key (HTTP {resp.status_code})."
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("workflows") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        store.save(self.id, {"api_key": api_key, "base_url": base_url})
        return f"Connected to n8n at {base_url} ({len(items)} workflows)."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "n8n disconnected."
        return "n8n was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key") or not data.get("base_url"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"linked to {data['base_url']}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: n8n is not connected. Run: zeline connect n8n")

    def _stored(self) -> dict:
        return store.load(self.id) or {}

    def _headers(self) -> dict:
        return {"X-N8N-API-KEY": self._stored().get("api_key", "")}

    def _api(self, method: str, path: str, **kwargs) -> dict | list:
        self._require_connected()
        base_url = self._stored().get("base_url", "")
        kwargs.setdefault("timeout", _TIMEOUT)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(self._headers())
        try:
            resp = requests.request(method, f"{base_url}{path}", headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: n8n API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: n8n API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: n8n returned an unreadable response.") from None

    @staticmethod
    def _workflow_items(payload: dict | list) -> list:
        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("workflows") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        return items if isinstance(items, list) else []

    # -- user-facing operations -------------------------------------------

    def list_workflows(self, limit: int = 10) -> str:
        """[READ] List workflows on the n8n instance."""
        limit = _clamp(limit)
        data = self._api("GET", "/api/v1/workflows", params={"limit": limit})
        items = self._workflow_items(data)
        lines = []
        for workflow in items[:limit]:
            if isinstance(workflow, dict):
                lines.append(
                    f"{workflow.get('id', '?')}: {workflow.get('name', '(no name)')} "
                    f"({'active' if workflow.get('active') else 'inactive'})"
                )
        return "\n".join(lines) if lines else "No workflows found."

    def get_workflow(self, workflow_id: str) -> str:
        """[READ] Fetch one workflow's details."""
        data = self._api("GET", f"/api/v1/workflows/{workflow_id}")
        if isinstance(data, dict):
            nodes = data.get("nodes") or []
            return (
                f"{data.get('id', workflow_id)}: {data.get('name', '(no name)')} "
                f"({'active' if data.get('active') else 'inactive'}, "
                f"{len(nodes)} nodes)"
            )
        return str(data)

    def execute_workflow(self, workflow_id: str, data: dict | None = None) -> str:
        """[NETWORK — mutates] Trigger a manual execution of a workflow."""
        result = self._api(
            "POST",
            f"/api/v1/workflows/{workflow_id}/execute",
            json=data or {},
        )
        if isinstance(result, dict):
            execution_id = result.get("id") or result.get("executionId") or "?"
            return f"Execution {execution_id} started for workflow {workflow_id}."
        return str(result)


def _register() -> N8nConnector:
    from zeline.connectors import register

    return register(N8nConnector())


_register()
