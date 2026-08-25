"""Resolve ZELINE_HOME for standalone skill scripts.

Skill scripts may run outside the Zeline process (e.g. system Python,
nix env, CI) where ``zeline_constants`` is not importable.  This module
provides the same ``get_zeline_home()`` and ``display_zeline_home()``
contracts as ``zeline_constants`` without requiring it on ``sys.path``.

When ``zeline_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``zeline_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``ZELINE_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from zeline_constants import display_zeline_home as display_zeline_home
    from zeline_constants import get_zeline_home as get_zeline_home
except (ModuleNotFoundError, ImportError):

    def get_zeline_home() -> Path:
        """Return the Zeline home directory (default: ~/.zeline).

        Mirrors ``zeline_constants.get_zeline_home()``."""
        val = os.environ.get("ZELINE_HOME", "").strip()
        return Path(val) if val else Path.home() / ".zeline"

    def display_zeline_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``zeline_constants.display_zeline_home()``."""
        home = get_zeline_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
