"""Cloudinary connector (cloud name + API key + API secret, Basic auth)."""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

_TIMEOUT = 30
_VALID_RESOURCE_TYPES = ("image", "video", "raw")


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class CloudinaryConnector(BaseConnector):
    id = "cloudinary"
    name = "Cloudinary"
    description = "List and inspect Cloudinary media resources."
    auth_kind = "pat"

    def connect(
        self,
        cloud_name: str = "",
        api_key: str = "",
        api_secret: str = "",
        **kwargs,
    ) -> str:
        cloud_name = (cloud_name or kwargs.get("cloud_name") or "").strip()
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        api_secret = (api_secret or kwargs.get("api_secret") or "").strip()
        if not cloud_name:
            return "ERROR: no cloud name provided."
        if not api_key:
            return "ERROR: no API key provided."
        if not api_secret:
            return "ERROR: no API secret provided."
        base = f"https://api.cloudinary.com/v1_1/{cloud_name}"
        try:
            resp = requests.get(
                f"{base}/resources/image",
                auth=(api_key, api_secret),
                params={"max_results": 1},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach Cloudinary ({exc})."
        if resp.status_code == 401:
            return "ERROR: Cloudinary rejected the credentials (HTTP 401)."
        if resp.status_code != 200:
            return f"ERROR: Cloudinary validation failed (HTTP {resp.status_code})."
        store.save(
            self.id,
            {
                "cloud_name": cloud_name,
                "api_key": api_key,
                "api_secret": api_secret,
            },
        )
        return f"Connected to Cloudinary (cloud name: {cloud_name})."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "Cloudinary disconnected."
        return "Cloudinary was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("cloud_name") or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": f"linked to {data['cloud_name']}"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise RuntimeError("ERROR: Cloudinary is not connected. Run: zeline connect cloudinary")

    def _stored(self) -> dict:
        return store.load(self.id) or {}

    def _check_resource_type(self, resource_type: str) -> str:
        resource_type = (resource_type or "").strip().lower()
        if resource_type not in _VALID_RESOURCE_TYPES:
            raise RuntimeError(
                "ERROR: invalid resource type "
                f"'{resource_type}' (expected one of: {', '.join(_VALID_RESOURCE_TYPES)})."
            )
        return resource_type

    def _api(self, path: str, **kwargs) -> dict | list:
        self._require_connected()
        data = self._stored()
        base = f"https://api.cloudinary.com/v1_1/{data.get('cloud_name', '')}"
        kwargs.setdefault("timeout", _TIMEOUT)
        try:
            resp = requests.get(
                f"{base}{path}",
                auth=(data.get("api_key", ""), data.get("api_secret", "")),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: Cloudinary API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: Cloudinary API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: Cloudinary returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def list_resources(self, resource_type: str = "image", limit: int = 10) -> str:
        """[READ] List media resources in the cloud."""
        resource_type = self._check_resource_type(resource_type)
        limit = _clamp(limit)
        payload = self._api(
            f"/resources/{resource_type}",
            params={"max_results": limit},
        )
        items = payload.get("resources") if isinstance(payload, dict) else None
        if not items:
            return "No resources found."
        lines = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"{item.get('public_id', '?')} "
                f"(format: {item.get('format', '?')}, "
                f"bytes: {item.get('bytes', '?')})"
            )
        return "\n".join(lines) if lines else "No resources found."

    def resource_info(self, public_id: str, resource_type: str = "image") -> str:
        """[READ] Fetch one resource's details."""
        resource_type = self._check_resource_type(resource_type)
        payload = self._api(f"/resources/{resource_type}/upload/{public_id}")
        if not isinstance(payload, dict):
            return str(payload)
        lines = [
            f"Public ID: {payload.get('public_id', public_id)}",
            f"Format: {payload.get('format', '?')}",
        ]
        width, height = payload.get("width"), payload.get("height")
        if width is not None or height is not None:
            lines.append(f"Dimensions: {width}x{height}")
        lines.append(f"Bytes: {payload.get('bytes', '?')}")
        if payload.get("url"):
            lines.append(f"URL: {payload['url']}")
        return "\n".join(lines)


def _register() -> CloudinaryConnector:
    from zeline.connectors import register

    return register(CloudinaryConnector())


_register()
