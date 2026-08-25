"""Web search providers — optional premium backends with a free fallback.

Zeline already ships a robust *free* search chain (Bing→DDG→Google News→
Wikipedia via a reader proxy) that needs no API key and survives Termux/mobile
network blocking. This module layers OPTIONAL premium providers on top:

    premium (key-gated): tavily → exa → brave
    builtin free (always): the existing chain in ``zeline.tools``

A premium provider only activates when its API key is present (in the process
env or ``~/.zeline/.env``). Users with no keys get exactly the current free
behaviour — no new dependency, no behaviour change. Users who set a free-tier
key (Tavily/Exa/Brave all offer one) transparently get better results, with
automatic fallback down the chain on any failure.

All providers use ``requests`` (already a Zeline dependency) — nothing new to
install. Response contract mirrors the internal free chain: each ``search``
returns ``list[tuple[title, url]]`` (empty list on failure), so the caller can
treat premium and free identically.
"""
from __future__ import annotations

import abc
import os
from typing import Optional

import requests

# Kept in sync with zeline.tools; imported lazily there to avoid a cycle.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
_TIMEOUT = (4, 8)
_MAX_RESULTS = 5


def _env(name: str) -> str:
    """Read a key from the process env, loading ``~/.zeline/.env`` first.

    ``config._load_env_file()`` copies the dotenv into ``os.environ`` with
    ``setdefault`` so gateway/subprocess runs see keys set through Zeline's
    config layer, not only exported shell vars. Falls back to a bare lookup if
    the config module is unavailable (stripped installs / early import).
    """
    try:
        from zeline import config

        config._load_env_file()
    except Exception:  # noqa: BLE001 — config layer optional here
        pass
    return (os.environ.get(name) or "").strip()


class WebSearchProvider(abc.ABC):
    """A key-gated premium search backend.

    Subclasses set ``name`` + ``env_key`` and implement ``search``. The registry
    skips any provider whose ``is_available()`` is False (its key is unset), so
    a user with no keys never triggers a premium path.
    """

    name: str = ""
    env_key: str = ""

    def is_available(self) -> bool:
        return bool(_env(self.env_key))

    @abc.abstractmethod
    def search(self, query: str) -> list[tuple[str, str]]:
        """Return ``[(title, url), ...]``; empty list on any failure."""


class TavilyProvider(WebSearchProvider):
    name = "tavily"
    env_key = "TAVILY_API_KEY"

    def search(self, query: str) -> list[tuple[str, str]]:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": _env(self.env_key), "query": query,
                      "max_results": _MAX_RESULTS},
                headers={"User-Agent": _UA, "Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
            if not resp.ok:
                return []
            out: list[tuple[str, str]] = []
            for item in (resp.json().get("results") or [])[:_MAX_RESULTS]:
                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if title and url.startswith("http"):
                    out.append((title, url))
            return out
        except (requests.RequestException, ValueError):
            return []


class ExaProvider(WebSearchProvider):
    name = "exa"
    env_key = "EXA_API_KEY"

    def search(self, query: str) -> list[tuple[str, str]]:
        try:
            resp = requests.post(
                "https://api.exa.ai/search",
                json={"query": query, "numResults": _MAX_RESULTS},
                headers={"x-api-key": _env(self.env_key), "User-Agent": _UA,
                         "Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
            if not resp.ok:
                return []
            out: list[tuple[str, str]] = []
            for item in (resp.json().get("results") or [])[:_MAX_RESULTS]:
                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if title and url.startswith("http"):
                    out.append((title, url))
            return out
        except (requests.RequestException, ValueError):
            return []


class BraveProvider(WebSearchProvider):
    name = "brave"
    env_key = "BRAVE_SEARCH_API_KEY"

    def search(self, query: str) -> list[tuple[str, str]]:
        try:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": _MAX_RESULTS},
                headers={"X-Subscription-Token": _env(self.env_key),
                         "Accept": "application/json", "User-Agent": _UA},
                timeout=_TIMEOUT,
            )
            if not resp.ok:
                return []
            results = (resp.json().get("web") or {}).get("results") or []
            out: list[tuple[str, str]] = []
            for item in results[:_MAX_RESULTS]:
                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if title and url.startswith("http"):
                    out.append((title, url))
            return out
        except (requests.RequestException, ValueError):
            return []


# Preference order: highest-quality/most-generous free tier first. Filtered by
# is_available() at call time so only providers the user has a key for run.
PREMIUM_PROVIDERS: tuple[WebSearchProvider, ...] = (
    TavilyProvider(),
    ExaProvider(),
    BraveProvider(),
)


def available_premium() -> list[str]:
    """Names of premium providers whose API key is set (for diagnostics)."""
    return [p.name for p in PREMIUM_PROVIDERS if p.is_available()]


def search_premium(query: str) -> Optional[list[tuple[str, str]]]:
    """Try each key-gated premium provider in order.

    Returns the first non-empty result list, or ``None`` when no premium
    provider is available or all available ones failed — the caller then falls
    back to the builtin free chain.
    """
    query = (query or "").strip()
    if not query:
        return None
    for provider in PREMIUM_PROVIDERS:
        if not provider.is_available():
            continue
        results = provider.search(query)
        if results:
            return results
    return None
