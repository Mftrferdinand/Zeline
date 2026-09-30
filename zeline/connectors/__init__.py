"""External service connectors for Zeline.

A connector links the agent to an outside service (GitHub, Gmail, ...) so
native tools can act on the operator's behalf. Credentials are kept in
``~/.zeline/connectors/<id>.json`` (mode 0600) and are never logged.

Phase 1 ships the framework plus the GitHub connector; phase 2 adds Google
(Gmail, Calendar, Sheets, Drive) via the OAuth2 helpers in
:mod:`zeline.connectors.oauth`.
"""
from __future__ import annotations

from zeline.connectors.base import BaseConnector

REGISTRY: dict[str, BaseConnector] = {}


def register(conn: BaseConnector) -> BaseConnector:
    """Register a connector instance under its id."""
    REGISTRY[conn.id] = conn
    return conn


def get(cid: str) -> BaseConnector | None:
    """Return the connector for *cid*, or None when unknown."""
    return REGISTRY.get(cid)


def all_ids() -> list[str]:
    """Sorted connector ids."""
    return sorted(REGISTRY)


def all() -> list[BaseConnector]:
    """All registered connectors, sorted by id."""
    return [REGISTRY[cid] for cid in all_ids()]


# Built-in connectors self-register on import.
from zeline.connectors import github as _github  # noqa: E402,F401
from zeline.connectors import google as _google  # noqa: E402,F401
