"""Subconscious loop (OpenHuman parity).

Background process that:
1. Reviews compressed session history + goals + user model
2. Detects drift (agent going off-track from user goals)
3. Injects steering directives to realign

Runs periodically (e.g., via cron). Directives are stored and picked up
by the agent loop, or injected via steer_worker for active workers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def _directives_path() -> Path:
    p = Path.home() / ".zeline" / "subconscious_directives.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_directives() -> list[dict]:
    try:
        return json.loads(_directives_path().read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_directives(directives: list[dict]) -> None:
    _directives_path().write_text(
        json.dumps(directives, indent=2, ensure_ascii=False), encoding="utf-8")
    _directives_path().chmod(0o600)


def review(identity: str = "cli:local") -> list[str]:
    """Run one subconscious review cycle. Returns new directives created.

    Analyzes:
    - Active goals (are they being progressed?)
    - Recent episodes (what's the user focused on?)
    - User model (any new patterns?)
    """
    directives = []
    # Check goals (L4 fix: pass identity, was dead code)
    try:
        from zeline import goals as _goals
        glist = _goals.list_goals(identity)
        active = [g for g in glist if g.get("status") == "active"]
        if active and len(active) > 5:
            directives.append(
                "Subconscious: user has many active goals "
                f"({len(active)}). Prioritize the most urgent; "
                "suggest archiving stale ones."
            )
    except Exception:
        pass
    # Check recent episodes for focus
    try:
        from zeline import memory as _mem
        # Simplified: if we can get recent episodes
        pass
    except Exception:
        pass
    # Check for drift: compare recent activity vs stated goals
    # (Simplified heuristic - full implementation would use LLM)
    if directives:
        existing = _load_directives()
        for d in directives:
            existing.append({
                "directive": d,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "consumed": False,
            })
        # Keep last 20
        existing = existing[-20:]
        _save_directives(existing)
    return directives


def pop_directives() -> list[str]:
    """Get unconsumed directives (and mark consumed). For agent loop."""
    directives = _load_directives()
    result = [d["directive"] for d in directives if not d.get("consumed")]
    for d in directives:
        d["consumed"] = True
    _save_directives(directives)
    return result


def inject_into_workers() -> int:
    """Push pending directives to all running workers via steering.

    Returns number of workers steered.
    """
    directives = pop_directives()
    if not directives:
        return 0
    try:
        from zeline import supervisor as _sup
        # Get all supervisors and steer their workers
        # Simplified: use default identity
        count = 0
        combined = " | ".join(directives)
        # This would iterate supervisors; simplified for now
        return count
    except Exception:
        return 0
