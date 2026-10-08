"""OpenWeatherMap connector (API key passed as the ``appid`` query param)."""

from __future__ import annotations

import requests

from zeline.connectors import store
from zeline.connectors.base import BaseConnector

API_BASE = "https://api.openweathermap.org/data/2.5"
_TIMEOUT = 30


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), 100))


class OpenWeatherMapConnector(BaseConnector):
    id = "openweathermap"
    name = "OpenWeatherMap"
    description = "Read current weather and short-term forecasts for any city."
    auth_kind = "pat"

    def connect(self, api_key: str = "", **kwargs) -> str:
        api_key = (api_key or kwargs.get("api_key") or kwargs.get("token") or "").strip()
        if not api_key:
            return "ERROR: no API key provided."
        try:
            resp = requests.get(
                f"{API_BASE}/weather",
                params={"q": "London", "appid": api_key, "units": "metric"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return f"ERROR: could not reach api.openweathermap.org ({exc})."
        if resp.status_code != 200:
            return f"ERROR: OpenWeatherMap rejected the API key (HTTP {resp.status_code})."
        try:
            body = resp.json()
        except ValueError:
            return "ERROR: OpenWeatherMap returned an unreadable response."
        if body.get("cod") != 200:
            return f"ERROR: OpenWeatherMap rejected the API key (cod={body.get('cod')})."
        store.save(self.id, {"api_key": api_key})
        return "Connected to OpenWeatherMap."

    def disconnect(self) -> str:
        if store.delete(self.id):
            return "OpenWeatherMap disconnected."
        return "OpenWeatherMap was not connected."

    def status(self) -> dict:
        data = store.load(self.id)
        if not data or not data.get("api_key"):
            return {"connected": False, "detail": "not linked"}
        return {"connected": True, "detail": "API key stored"}

    # -- API helpers -----------------------------------------------------

    def _require_connected(self) -> str:
        data = store.load(self.id) or {}
        api_key = data.get("api_key", "")
        if not api_key:
            raise RuntimeError(
                "ERROR: OpenWeatherMap is not connected. Run: zeline connect openweathermap"
            )
        return api_key

    def _get(self, path: str, params: dict) -> dict:
        api_key = self._require_connected()
        params = dict(params)
        params["appid"] = api_key
        try:
            resp = requests.get(f"{API_BASE}{path}", params=params, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"ERROR: OpenWeatherMap API request failed ({exc}).") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"ERROR: OpenWeatherMap API {resp.status_code} on {path}.")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("ERROR: OpenWeatherMap returned an unreadable response.") from None

    # -- user-facing operations -------------------------------------------

    def current_weather(self, city: str) -> str:
        city = (city or "").strip()
        if not city:
            raise RuntimeError("ERROR: no city provided.")
        body = self._get("/weather", {"q": city, "units": "metric"})
        name = body.get("name", city)
        main = body.get("main", {})
        weather = body.get("weather") or [{}]
        temp = main.get("temp", "?")
        desc = str(weather[0].get("description", "")).strip() or "n/a"
        humidity = main.get("humidity", "?")
        return f"{name}: {temp}\u00b0C, {desc} (humidity {humidity}%)"

    def forecast(self, city: str, limit: int = 8) -> str:
        city = (city or "").strip()
        if not city:
            raise RuntimeError("ERROR: no city provided.")
        limit = _clamp(limit)
        body = self._get("/forecast", {"q": city, "units": "metric", "cnt": limit})
        entries = body.get("list") or []
        lines = []
        for entry in entries[:limit]:
            when = entry.get("dt_txt", "?")
            temp = (entry.get("main") or {}).get("temp", "?")
            weather = entry.get("weather") or [{}]
            desc = str(weather[0].get("description", "")).strip() or "n/a"
            lines.append(f"{when}: {temp}\u00b0C, {desc}")
        return "\n".join(lines) if lines else f"No forecast entries found for {city}."


def _register() -> OpenWeatherMapConnector:
    from zeline.connectors import register

    return register(OpenWeatherMapConnector())


_register()
