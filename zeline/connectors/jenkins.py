"""Jenkins connector (username + API token)."""
from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30


class JenkinsConnector(BaseConnector):
    id = "jenkins"
    name = "Jenkins"
    description = "List Jenkins jobs and check build status."
    auth_kind = "pat"

    def connect(self, username: str = "", api_token: str = "", base_url: str = "", **kwargs) -> str:
        username = (username or kwargs.get("username") or "").strip()
        api_token = (api_token or kwargs.get("api_token") or "").strip()
        base = (base_url or kwargs.get("base_url") or "").strip().rstrip("/")
        if not username or not api_token or not base:
            return "ERROR: username, api_token and base_url are required."
        try:
            resp = requests.get(f"{base}/me/api/json", auth=(username, api_token), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            return f"ERROR: could not reach Jenkins ({exc})."
        if resp.status_code != 200:
            return f"ERROR: Jenkins rejected the credentials (HTTP {resp.status_code})."
        full_name = resp.json().get("fullName", "?")
        store.save(
            self.id,
            {"username": username, "api_token": api_token, "base_url": base, "full_name": full_name},
        )
        return f"Connected to Jenkins as {full_name}."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Jenkins disconnected."
        return "Jenkins was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_token"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": data.get("base_url", "")}

    # -- API helpers -----------------------------------------------------

    def _base(self) -> tuple[str, tuple[str, str]]:
        data = store.load(self.id) or {}
        base = data.get("base_url")
        if not base or not data.get("api_token"):
            raise RuntimeError("ERROR: Jenkins is not connected. Run: zeline connect jenkins")
        return base, (data.get("username", ""), data.get("api_token", ""))

    def _api(self, method: str, url: str, **kwargs) -> dict | list:
        base, auth = self._base()
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.request(method, f"{base}{url}", auth=auth, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Jenkins API request failed ({exc}).") from exc
        if resp.status_code == 404:
            raise RuntimeError("ERROR: no builds found.")
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Jenkins API {resp.status_code} on {url}.")
        return resp.json()

    # -- user-facing operations -------------------------------------------

    def list_jobs(self) -> str:
        payload = self._api("GET", "/api/json", params={"tree": "jobs[name,color]"})
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        lines = [f"{job.get('name', '?')} [{job.get('color', '?')}]" for job in jobs]
        return "\n".join(lines) if lines else "No jobs found."

    def job_status(self, job_name: str) -> str:
        build = self._api("GET", f"/job/{job_name}/lastBuild/api/json")
        number = build.get("number", "?")
        result = build.get("result") or "BUILDING"
        display_name = build.get("displayName", "")
        return f"#{number} {result} ({display_name})"


def _register() -> JenkinsConnector:
    from zeline.connectors import register

    return register(JenkinsConnector())


_register()
