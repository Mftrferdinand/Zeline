"""On-disk credential store for connectors.

Each connector keeps its credential as JSON in
``~/.zeline/connectors/<connector-id>.json`` with mode 0600. Values are
never printed or logged by this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _dir() -> Path:
    path = Path.home() / ".zeline" / "connectors"
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_path(cid: str) -> Path:
    """Path of the credential file for connector *cid*."""
    return _dir() / f"{cid}.json"


def save(cid: str, data: dict) -> Path:
    """Write *data* for *cid*, creating the directory, chmod 0600."""
    path = store_path(cid)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load(cid: str) -> dict | None:
    """Return the stored credential dict, or None when absent/invalid."""
    path = store_path(cid)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def delete(cid: str) -> bool:
    """Delete the stored credential. True when a file was removed."""
    path = store_path(cid)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True
