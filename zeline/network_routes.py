"""Owner-only per-request network routes for geo-blocked public web pages.

Routes are HTTP(S) or SOCKS proxy endpoints stored in a private JSON file. They
never mutate process-wide proxy environment variables, so Telegram, provider
traffic, localhost, and 9Router remain direct.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import requests

from zeline import config

ROUTES_FILE = config.DATA_DIR / "network-routes.json"
_ALLOWED_SCHEMES = {"http", "https", "socks5", "socks5h"}
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")


def _private_write(payload: dict[str, Any]) -> None:
    ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = ROUTES_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(temp, 0o600)
    temp.replace(ROUTES_FILE)
    if os.name == "posix":
        os.chmod(ROUTES_FILE, 0o600)


def _load() -> dict[str, Any]:
    try:
        parsed = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"routes": []}
    routes = parsed.get("routes") if isinstance(parsed, dict) else None
    return {"routes": routes if isinstance(routes, list) else []}


def _mask_proxy(proxy_url: str) -> str:
    parsed = urlparse(proxy_url)
    host = parsed.hostname or "?"
    port = f":{parsed.port}" if parsed.port else ""
    auth = "***@" if parsed.username or parsed.password else ""
    return f"{parsed.scheme}://{auth}{host}{port}"


def _validate_proxy(proxy_url: str) -> str:
    parsed = urlparse((proxy_url or "").strip())
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError("proxy_url must use http, https, socks5, or socks5h and include a host")
    return proxy_url.strip()


def proxies(proxy_url: str) -> dict[str, str]:
    value = _validate_proxy(proxy_url)
    return {"http": value, "https": value}


def add(label: str, proxy_url: str, country: str = "", enabled: bool = True) -> str:
    label = (label or "").strip()
    if not _LABEL_RE.fullmatch(label):
        return "ERROR: label must be 1-48 characters using letters, numbers, dot, underscore, or dash."
    try:
        proxy_url = _validate_proxy(proxy_url)
    except ValueError as exc:
        return f"ERROR: {exc}"
    country = (country or "").strip().upper()[:2]
    data = _load()
    route = {"label": label, "proxy_url": proxy_url, "country": country, "enabled": bool(enabled)}
    existing = next((i for i, item in enumerate(data["routes"]) if str(item.get("label")) == label), None)
    if existing is None:
        data["routes"].append(route)
    else:
        data["routes"][existing] = route
    _private_write(data)
    return f"OK: route '{label}' saved ({_mask_proxy(proxy_url)}, country={country or 'unknown'})."


def remove(label: str) -> str:
    data = _load()
    before = len(data["routes"])
    data["routes"] = [item for item in data["routes"] if str(item.get("label")) != label]
    if len(data["routes"]) == before:
        return f"ERROR: route '{label}' not found."
    _private_write(data)
    return f"OK: route '{label}' removed."


def enabled_routes(country: str = "") -> list[dict[str, Any]]:
    wanted = (country or "").strip().upper()
    result = []
    for item in _load()["routes"]:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        try:
            _validate_proxy(str(item.get("proxy_url", "")))
        except ValueError:
            continue
        if wanted and str(item.get("country", "")).upper() != wanted:
            continue
        result.append(dict(item))
    return result


def list_routes() -> str:
    routes = _load()["routes"]
    if not routes:
        return "No network routes configured. Add an owner proxy with network_route action=add."
    safe = [
        {
            "label": str(item.get("label", "")),
            "proxy": _mask_proxy(str(item.get("proxy_url", ""))),
            "country": str(item.get("country", "")) or "unknown",
            "enabled": bool(item.get("enabled", True)),
        }
        for item in routes if isinstance(item, dict)
    ]
    return json.dumps({"routes": safe}, ensure_ascii=False, indent=2)


def test_route(label: str, timeout: int = 15) -> str:
    route = next((item for item in enabled_routes() if str(item.get("label")) == label), None)
    if not route:
        return f"ERROR: enabled route '{label}' not found."
    try:
        response = requests.get(
            "https://ipinfo.io/json",
            proxies=proxies(str(route["proxy_url"])),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=(6, max(6, min(int(timeout), 30))),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 — health check reports, never crashes
        return f"ERROR: route '{label}' failed health check ({exc.__class__.__name__})."
    return json.dumps({
        "label": label,
        "proxy": _mask_proxy(str(route["proxy_url"])),
        "ip": str(payload.get("ip", "unknown")),
        "country": str(payload.get("country", "unknown")),
        "city": str(payload.get("city", "unknown")),
        "healthy": True,
    }, ensure_ascii=False)


def tool(action: str, label: str = "", proxy_url: str = "", country: str = "") -> str:
    action = (action or "list").strip().lower()
    if action == "list":
        return list_routes()
    if action == "add":
        return add(label, proxy_url, country)
    if action == "remove":
        return remove(label)
    if action == "test":
        return test_route(label)
    return "ERROR: action must be list, add, remove, or test."
